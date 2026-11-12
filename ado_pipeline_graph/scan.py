from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .core import Graph, _load_yaml, build_graph


def _yaml_files(roots: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if root.is_file() and root.suffix.lower() in {".yml", ".yaml"}:
            files.add(root)
            continue
        if not root.is_dir():
            raise ValueError(f"scan root is not a directory or YAML file: {root}")
        for current, dirs, names in os.walk(root, onerror=_raise_walk_error):
            dirs[:] = [name for name in dirs if name not in {".git", ".venv", "venv", "node_modules"}]
            for name in names:
                if Path(name).suffix.lower() in {".yml", ".yaml"}:
                    files.add(Path(current, name).resolve())
    return sorted(files)


def _raise_walk_error(error: OSError) -> None:
    raise error


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
    if path.name.lower().startswith("azure-pipelines"):
        return True
    if any(key in data for key in ("trigger", "pr", "schedules", "extends")):
        return True
    return any(key in data for key in ("stages", "jobs", "steps"))


def build_repository_graph(
    roots: list[Path],
    *,
    template_roots: dict[str, Path] | None = None,
    rest_runs: dict[int, list[dict[str, Any]]] | None = None,
    definition_ids: dict[str, int] | None = None,
    variable_groups: list[dict[str, Any]] | None = None,
) -> Graph:
    files = _yaml_files(roots)
    loaded: dict[Path, dict[str, Any]] = {}
    referenced: set[Path] = set()
    for path in files:
        data = _load_yaml(path)
        loaded[path] = data
        for ref in _template_references(data):
            template, separator, alias = ref.rpartition("@")
            template_path = template if separator else ref
            base = (template_roots or {}).get(alias) if separator else path.parent
            if base is None:
                continue
            candidate = (base / template_path).resolve()
            if candidate in files:
                referenced.add(candidate)
    pipelines = [
        path for path, data in loaded.items()
        if _is_pipeline(path, data, referenced)
    ]
    combined = Graph(pipeline_name="Azure DevOps repository graph")
    combined.add_node("repository", combined.pipeline_name, "stadium")
    pipeline_ids: dict[Path, str] = {}
    path_by_definition: dict[int, Path] = {}
    api_groups = {
        item["name"]: item for item in (variable_groups or [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }

    for index, path in enumerate(pipelines):
        digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
        prefix = f"p{index}_{digest}"
        pipeline_graph = build_graph(
            loaded[path], source_path=path, template_roots=template_roots or {},
            variable_groups=variable_groups,
            include_variable_group_nodes=False,
        )
        for node_id, (label, shape) in pipeline_graph.nodes.items():
            combined.add_node(f"{prefix}_{node_id}", label, shape)
        for source, target, label in pipeline_graph.edges:
            combined.add_edge(f"{prefix}_{source}", f"{prefix}_{target}", label)
        pipeline_id = f"{prefix}_pipeline"
        pipeline_ids[path] = pipeline_id
        combined.add_edge("repository", pipeline_id)

        for template in sorted(pipeline_graph.templates):
            template_id = "template_" + hashlib.sha1(template.encode("utf-8")).hexdigest()[:10]
            combined.add_node(template_id, f"Template: {Path(template).name}", "diamond")
            combined.add_edge(pipeline_id, template_id, "uses")
        for group in sorted(pipeline_graph.variable_groups):
            group_id = "group_" + hashlib.sha1(group.encode("utf-8")).hexdigest()[:10]
            api_id = api_groups.get(group, {}).get("id")
            label = f"Variable group: {group}"
            if api_id is not None:
                label += f" (ID {api_id})"
            combined.add_node(group_id, label, "diamond")
            combined.add_edge(pipeline_id, group_id, "uses")

        if definition_ids:
            for key, definition_id in definition_ids.items():
                if Path(key).resolve() == path:
                    path_by_definition[definition_id] = path

    for pipeline_id, runs in (rest_runs or {}).items():
        target_path = path_by_definition.get(pipeline_id)
        if target_path is None:
            continue
        for run in runs:
            trigger = run.get("triggeredByBuild")
            source_definition = (
                trigger.get("definition", {}).get("id")
                if isinstance(trigger, dict) and isinstance(trigger.get("definition"), dict)
                else None
            )
            source_path = path_by_definition.get(source_definition)
            if source_path and source_path != target_path:
                combined.add_edge(pipeline_ids[source_path], pipeline_ids[target_path], "triggered")
    return combined
