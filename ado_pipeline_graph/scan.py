from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path
from typing import Any

import yaml

from .core import Graph, build_graph, find_repo_root, resolve_template

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", ".github", ".tox", "dist", "build", "__pycache__", "ado-graph"}


def _walk(roots: list[Path], exclude: list[str] | None) -> list[tuple[Path, Path]]:
    """(scan_root, file) pairs; ``exclude`` globs match the POSIX path relative to the scan root."""
    found: dict[Path, Path] = {}
    for root in roots:
        root = root.resolve()
        if root.is_file() and root.suffix.lower() in {".yml", ".yaml"}:
            found.setdefault(root, root.parent)
            continue
        if not root.is_dir():
            raise ValueError(f"scan root is not a directory or YAML file: {root}")
        for current, dirs, names in os.walk(root, onerror=_raise_walk_error):
            dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
            for name in names:
                path = Path(current, name).resolve()
                if path.suffix.lower() not in {".yml", ".yaml"}:
                    continue
                rel = path.relative_to(root).as_posix()
                if not any(fnmatch.fnmatch(rel, pattern) for pattern in exclude or ()):
                    found.setdefault(path, root)
    return sorted((root, path) for path, root in found.items())


def yaml_files(roots: list[Path], exclude: list[str] | None = None) -> list[Path]:
    return sorted(path for _, path in _walk(roots, exclude))


_yaml_files = yaml_files


def _raise_walk_error(error: OSError) -> None:
    raise error


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)  # multi-document files raise here
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("YAML root must be a mapping")
    return data


def _repo_root(path: Path, scan_root: Path) -> Path:
    root = find_repo_root(path)
    return root if (root / ".git").exists() else scan_root


def _template_references(data: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(data, dict):
        template = data.get("template")
        if isinstance(template, str):
            refs.add(template)
        extends = data.get("extends")
        if isinstance(extends, dict) and isinstance(extends.get("template"), str):
            refs.add(extends["template"])
        for value in data.values():
            refs.update(_template_references(value))
    elif isinstance(data, list):
        for value in data:
            refs.update(_template_references(value))
    return refs


def _is_pipeline(path: Path, data: dict[str, Any], referenced: set[Path]) -> bool:
    if path in referenced:
        return False
    # GitHub Actions (PyYAML parses a bare `on:` as True), Kubernetes, compose
    if "on" in data or True in data or isinstance(data.get("jobs"), dict):
        return False
    if "services" in data or ("apiVersion" in data and "kind" in data):
        return False
    if path.name.lower().startswith("azure-pipelines"):
        return True
    if any(key in data for key in ("trigger", "pr", "schedules", "extends")):
        return True
    return any(isinstance(data.get(key), list) for key in ("stages", "jobs", "steps"))


def build_repository_graph(
    roots: list[Path],
    *,
    template_roots: dict[str, Path] | None = None,
    rest_runs: dict[int, list[dict[str, Any]]] | None = None,
    definition_ids: dict[str, int] | None = None,
    variable_groups: list[dict[str, Any]] | None = None,
    exclude: list[str] | None = None,
    strict: bool = False,
    skipped: list[tuple[Path, str]] | None = None,
) -> Graph:
    """Combine every pipeline under ``roots``. Unusable files are appended to ``skipped``
    as (path, reason) unless ``strict``, which raises instead."""
    entries = _walk(roots, exclude)
    files = {path for _, path in entries}
    loaded: dict[Path, dict[str, Any]] = {}
    scan_roots: dict[Path, Path] = {}
    referenced: set[Path] = set()

    def skip(path: Path, error: Exception) -> None:
        if strict:
            raise ValueError(f"{path}: {error}") from error
        if skipped is not None:
            skipped.append((path, str(error).splitlines()[0] if str(error) else type(error).__name__))

    for scan_root, path in entries:
        try:
            data = _load(path)
        except (yaml.YAMLError, ValueError, OSError, UnicodeDecodeError) as error:
            skip(path, error)
            continue
        loaded[path] = data
        scan_roots[path] = scan_root
        repo_root = _repo_root(path, scan_root)
        for ref in _template_references(data):
            candidate = resolve_template(ref, path.parent, template_roots or {}, repo_root)
            if candidate in files:
                referenced.add(candidate)
    combined = Graph(pipeline_name="Azure DevOps repository graph")
    combined.add_node("repository", combined.pipeline_name, "stadium", kind="repository")
    pipeline_ids: dict[Path, str] = {}
    path_by_definition: dict[int, Path] = {}
    api_groups = {
        item["name"]: item
        for item in (variable_groups or [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }

    index = 0
    for path, data in loaded.items():
        if not _is_pipeline(path, data, referenced):
            continue
        scan_root = scan_roots[path]
        try:
            pipeline_graph = build_graph(
                data,
                source_path=path,
                template_roots=template_roots or {},
                variable_groups=variable_groups,
                include_variable_group_nodes=False,
                repo_root=_repo_root(path, scan_root),
            )
        except Exception as error:  # template expansion can fail in many ways
            skip(path, error)
            continue
        rel = path.relative_to(scan_root).as_posix() if path.is_relative_to(scan_root) else path.name
        digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]
        prefix = f"p{index}_{digest}"
        index += 1
        for node_id, (label, shape) in pipeline_graph.nodes.items():
            parent = pipeline_graph.parents.get(node_id)
            combined.add_node(
                f"{prefix}_{node_id}",
                label,
                shape,
                kind=pipeline_graph.kinds.get(node_id),
                parent=f"{prefix}_{parent}" if parent else None,
            )
        for source, target, label in pipeline_graph.edges:
            combined.add_edge(f"{prefix}_{source}", f"{prefix}_{target}", label)
        pipeline_id = f"{prefix}_pipeline"
        pipeline_ids[path] = pipeline_id
        combined.parents[pipeline_id] = "repository"
        combined.add_edge("repository", pipeline_id)
        combined.templates.update(pipeline_graph.templates)
        combined.variable_groups.update(pipeline_graph.variable_groups)

        for template in sorted(pipeline_graph.templates):
            template_id = "template_" + hashlib.sha1(template.encode("utf-8")).hexdigest()[:10]
            combined.add_node(template_id, f"Template: {template}", "diamond", kind="template")
            combined.add_edge(pipeline_id, template_id, "uses")
        for group in sorted(pipeline_graph.variable_groups):
            group_id = "group_" + hashlib.sha1(group.encode("utf-8")).hexdigest()[:10]
            api_id = api_groups.get(group, {}).get("id")
            label = f"Variable group: {group}"
            if api_id is not None:
                label += f" (ID {api_id})"
            combined.add_node(group_id, label, "diamond", kind="variable_group")
            combined.add_edge(pipeline_id, group_id, "uses")

        if definition_ids:
            for key, definition_id in definition_ids.items():
                if Path(key).resolve() == path:
                    path_by_definition[definition_id] = path

    for target_definition, runs in (rest_runs or {}).items():
        target_path = path_by_definition.get(target_definition)
        if target_path is None or target_path not in pipeline_ids:
            continue
        counts: dict[Path, int] = {}
        for run in runs:
            trigger = run.get("triggeredByBuild")
            source_definition: Any = (
                trigger.get("definition", {}).get("id")
                if isinstance(trigger, dict) and isinstance(trigger.get("definition"), dict)
                else None
            )
            source_path = path_by_definition.get(source_definition)
            if source_path and source_path != target_path and source_path in pipeline_ids:
                counts[source_path] = counts.get(source_path, 0) + 1
        for source_path, count in counts.items():
            combined.add_edge(
                pipeline_ids[source_path], pipeline_ids[target_path], f"triggered ({count} of {len(runs)} runs)"
            )
    return combined
