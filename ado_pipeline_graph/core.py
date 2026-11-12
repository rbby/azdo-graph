from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml


@dataclass
class Graph:
    nodes: dict[str, tuple[str, str]] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)
    templates: set[str] = field(default_factory=set)
    variable_groups: set[str] = field(default_factory=set)
    pipeline_name: str = "Pipeline"

    def add_node(self, node_id: str, label: str, shape: str = "box") -> None:
        self.nodes[node_id] = (label, shape)

    def add_edge(self, source: str, target: str, label: str = "") -> None:
        self.edges.append((source, target, label))

    def to_mermaid(self) -> str:
        lines = ["flowchart TD"]
        for node_id, (label, shape) in self.nodes.items():
            safe = label.replace('"', "#quot;").replace("\n", "<br/>")
            if shape == "external":
                lines.append(f'  {node_id}[("{safe}")]')
                lines.append(f"  style {node_id} stroke-dasharray: 5 5")
            elif shape == "stadium":
                lines.append(f'  {node_id}(["{safe}"])')
            elif shape == "diamond":
                lines.append(f'  {node_id}{{"{safe}"}}')
            else:
                lines.append(f'  {node_id}["{safe}"]')
        for source, target, label in self.edges:
            suffix = f" -- {label} -->" if label else "-->"
            lines.append(f"  {source} {suffix} {target}")
        return "\n".join(lines) + "\n"

    def to_markdown(self) -> str:
        lines = [f"# {self.pipeline_name}", "", "```mermaid", self.to_mermaid().rstrip(), "```", "", "## Nodes", ""]
        lines += [f"- {label} ({shape})" for label, shape in self.nodes.values()]
        if self.templates:
            lines += ["", "## Templates", ""] + [f"- `{t}`" for t in sorted(self.templates)]
        if self.variable_groups:
            lines += ["", "## Variable groups", ""] + [f"- {g}" for g in sorted(self.variable_groups)]
        lines += ["", "## Relationships", ""]
        for source, target, label in self.edges:
            src, dst = self.nodes.get(source, (source,))[0], self.nodes.get(target, (target,))[0]
            lines.append(f"- {src} -> {dst}" + (f" ({label})" if label else ""))
        return "\n".join(lines) + "\n"

    def to_html(self) -> str:
        diagram = html.escape(self.to_mermaid())
        return (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<script type=\"module\">import mermaid from "
            "'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';"
            "mermaid.initialize({startOnLoad:true});</script></head><body>"
            f'<pre class="mermaid">{diagram}</pre></body></html>'
        )


def parse_template_roots(values: Iterable[str] | None) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values or ():
        if "=" not in value:
            raise ValueError(f"template root must be ALIAS=PATH: {value!r}")
        alias, raw_path = value.split("=", 1)
        if not alias or not raw_path:
            raise ValueError(f"template root must be ALIAS=PATH: {value!r}")
        roots[alias] = Path(raw_path).expanduser().resolve()
    return roots


def _literal_values(data: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    parameters: dict[str, str] = {}
    raw_parameters = data.get("parameters", [])
    if isinstance(raw_parameters, list):
        for item in raw_parameters:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                value = item.get("default")
                if isinstance(value, (str, int, float, bool)):
                    parameters[item["name"]] = str(value)
    elif isinstance(raw_parameters, dict):
        parameters.update(
            (key, str(value))
            for key, value in raw_parameters.items()
            if isinstance(value, (str, int, float, bool))
        )

    variables: dict[str, str] = {}
    raw_variables = data.get("variables", [])
    if isinstance(raw_variables, dict):
        variables.update(
            (key, str(value))
            for key, value in raw_variables.items()
            if isinstance(value, (str, int, float, bool))
        )
    elif isinstance(raw_variables, list):
        for item in raw_variables:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                value = item.get("value")
                if isinstance(value, (str, int, float, bool)):
                    variables[item["name"]] = str(value)
    return parameters, variables


_EXPRESSION = re.compile(r"\$\{\{\s*(parameters|variables)\.([A-Za-z_][\w.-]*)\s*\}\}")


def _substitute(value: Any, parameters: dict[str, str], variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            values = parameters if match.group(1) == "parameters" else variables
            return values.get(match.group(2), match.group(0))

        return _EXPRESSION.sub(replace, value)
    if isinstance(value, list):
        return [_substitute(item, parameters, variables) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, parameters, variables) for key, item in value.items()}
    return value


def _template_parts(reference: str) -> tuple[str, str | None]:
    path, separator, alias = reference.rpartition("@")
    if separator:
        return path, alias or None
    return reference, None


def _resolve_template(
    reference: str,
    current_dir: Path,
    template_roots: dict[str, Path],
) -> Path | None:
    template, alias = _template_parts(reference)
    root = template_roots.get(alias) if alias else current_dir
    if root is None:
        return None
    candidate = (root / template).resolve()
    if alias:
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None
    return candidate if candidate.is_file() else None


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"pipeline YAML must contain a mapping at its root: {path}")
    return data


def _expand(
    data: dict[str, Any],
    current_dir: Path,
    template_roots: dict[str, Path],
    graph: Graph,
    parameters: dict[str, str],
    variables: dict[str, str],
    seen: set[Path],
) -> dict[str, Any]:
    data = _substitute(data, parameters, variables)
    extends = data.get("extends")
    if isinstance(extends, dict) and isinstance(extends.get("template"), str):
        resolved = _resolve_template(extends["template"], current_dir, template_roots)
        if resolved:
            graph.templates.add(str(resolved))
            if resolved in seen:
                raise ValueError(f"recursive template reference: {resolved}")
            base = _load_yaml(resolved)
            base_parameters, base_variables = _literal_values(base)
            passed_parameters = extends.get("parameters")
            if isinstance(passed_parameters, dict):
                base_parameters.update(
                    (key, str(value))
                    for key, value in passed_parameters.items()
                    if isinstance(value, (str, int, float, bool))
                )
            merged_parameters = {**parameters, **base_parameters}
            merged_variables = {**base_variables, **variables}
            base = _expand(base, resolved.parent, template_roots, graph,
                           merged_parameters, merged_variables, seen | {resolved})
            overrides = {key: value for key, value in data.items() if key != "extends"}
            base.update(overrides)
            data = base
        else:
            label = f"External template: {extends['template']}"
            graph.add_node("external_extends", label, "external")
            graph.add_edge("pipeline", "external_extends")

    def expand_sequence(value: Any, context: str) -> Any:
        if not isinstance(value, list):
            return value
        expanded: list[Any] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("template"), str):
                ref = item["template"]
                resolved = _resolve_template(ref, current_dir, template_roots)
                if resolved is None:
                    expanded.append({"__external_template__": ref})
                    continue
                graph.templates.add(str(resolved))
                if resolved in seen:
                    raise ValueError(f"recursive template reference: {resolved}")
                template_data = _load_yaml(resolved)
                local_params, local_vars = _literal_values(template_data)
                passed = item.get("parameters")
                if isinstance(passed, dict):
                    local_params.update(
                        (key, str(val)) for key, val in passed.items()
                        if isinstance(val, (str, int, float, bool))
                    )
                local_params = {**parameters, **local_params}
                local_vars = {**variables, **local_vars}
                template_data = _expand(
                    template_data, resolved.parent, template_roots, graph,
                    local_params, local_vars, seen | {resolved},
                )
                content = template_data.get(context)
                if isinstance(content, list):
                    expanded.extend(content)
                else:
                    raise ValueError(
                        f"template {resolved} does not define a {context} list"
                    )
            else:
                expanded.append(_expand(
                    item, current_dir, template_roots, graph,
                    parameters, variables, seen,
                ) if isinstance(item, dict) else item)
        return expanded

    for context in ("stages", "jobs", "steps"):
        if context in data:
            data[context] = expand_sequence(data[context], context)
    return data


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not slug or slug[0].isdigit():
        slug = "n_" + slug
    return slug


def _collect_variable_groups(value: Any) -> set[str]:
    groups: set[str] = set()
    if isinstance(value, dict):
        group = value.get("group")
        if isinstance(group, str):
            groups.add(group)
        for item in value.values():
            groups.update(_collect_variable_groups(item))
    elif isinstance(value, list):
        for item in value:
            groups.update(_collect_variable_groups(item))
    return groups


def build_graph(
    pipeline: dict[str, Any],
    *,
    source_path: Path | None = None,
    template_roots: dict[str, Path] | None = None,
    run_history: list[dict[str, Any]] | None = None,
    variable_groups: list[dict[str, Any]] | None = None,
    include_variable_group_nodes: bool = True,
) -> Graph:
    default_name = source_path.stem if source_path else "Pipeline"
    pipeline_name = pipeline.get("name")
    graph = Graph(pipeline_name=pipeline_name if isinstance(pipeline_name, str) and pipeline_name else default_name)
    parameters, variables = _literal_values(pipeline)
    initial_path = source_path.resolve() if source_path else None
    pipeline = _expand(
        pipeline,
        initial_path.parent if initial_path else Path("/__ado_remote_pipeline__"),
        template_roots or {},
        graph,
        parameters,
        variables,
        {initial_path} if initial_path else set(),
    )
    root_id = "pipeline"
    graph.add_node(root_id, graph.pipeline_name, "stadium")
    stage_ids: dict[str, str] = {}
    counter = 0

    def new_id(prefix: str, label: str) -> str:
        nonlocal counter
        counter += 1
        return f"{prefix}_{counter}_{_slug(label)[:32]}"

    def add_steps(items: Any, parent_id: str) -> None:
        if not isinstance(items, list):
            return
        previous = parent_id
        for item in items:
            if not isinstance(item, dict):
                continue
            if "__external_template__" in item:
                label = f"External template: {item['__external_template__']}"
                node_id = new_id("external", label)
                graph.add_node(node_id, label, "external")
            else:
                task = item.get("task")
                script = item.get("script", item.get("bash", item.get("pwsh")))
                label = str(item.get("displayName") or task or script or item.get("template") or "Step")
                node_id = new_id("step", label)
                graph.add_node(node_id, label)
            graph.add_edge(previous, node_id)
            previous = node_id

    def add_jobs(items: Any, parent_id: str) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            if "__external_template__" in item:
                label = f"External template: {item['__external_template__']}"
                job_id = new_id("external", label)
                graph.add_node(job_id, label, "external")
                graph.add_edge(parent_id, job_id)
                continue
            name = str(item.get("displayName") or item.get("job") or item.get("deployment") or item.get("template") or "Job")
            job_id = new_id("job", name)
            graph.add_node(job_id, name)
            graph.add_edge(parent_id, job_id)
            add_steps(item.get("steps"), job_id)

    stages = pipeline.get("stages")
    if isinstance(stages, list):
        for item in stages:
            if not isinstance(item, dict):
                continue
            if "__external_template__" in item:
                label = f"External template: {item['__external_template__']}"
                stage_id = new_id("external", label)
                graph.add_node(stage_id, label, "external")
                graph.add_edge(root_id, stage_id)
                continue
            name = str(item.get("displayName") or item.get("stage") or "Stage")
            stage_id = new_id("stage", name)
            graph.add_node(stage_id, name, "stadium")
            graph.add_edge(root_id, stage_id)
            stage_ids[str(item.get("stage", name))] = stage_id
            add_jobs(item.get("jobs"), stage_id)
            add_steps(item.get("steps"), stage_id)
        for item in stages:
            if not isinstance(item, dict) or "__external_template__" in item:
                continue
            target_id = stage_ids.get(str(item.get("stage", item.get("displayName", "Stage"))))
            depends = item.get("dependsOn", [])
            if isinstance(depends, str):
                depends = [depends]
            if target_id and isinstance(depends, list):
                for dependency in depends:
                    if str(dependency) in stage_ids:
                        graph.add_edge(stage_ids[str(dependency)], target_id, "dependsOn")
    elif isinstance(pipeline.get("jobs"), list):
        add_jobs(pipeline["jobs"], root_id)
    else:
        add_steps(pipeline.get("steps"), root_id)

    graph.variable_groups.update(_collect_variable_groups(pipeline))
    api_groups = {
        item["name"]: item for item in (variable_groups or [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if include_variable_group_nodes:
        for group in sorted(graph.variable_groups):
            api_group = api_groups.get(group, {})
            group_id = new_id("group", group)
            api_id = api_group.get("id")
            label = f"Variable group: {group}"
            if api_id is not None:
                label += f" (ID {api_id})"
            graph.add_node(group_id, label, "diamond")
            graph.add_edge(root_id, group_id, "uses")
    if run_history:
        for run in run_history:
            triggered_by = run.get("triggeredByBuild")
            if isinstance(triggered_by, dict):
                definition = triggered_by.get("definition")
                trigger_name = (
                    definition.get("name", "upstream pipeline")
                    if isinstance(definition, dict)
                    else "upstream pipeline"
                )
                trigger_id = new_id("trigger", str(trigger_name))
                graph.add_node(trigger_id, f"Triggered by: {trigger_name}", "diamond")
                graph.add_edge(trigger_id, root_id, "trigger")
    return graph


def load_graph(path: Path, template_roots: dict[str, Path] | None = None) -> Graph:
    pipeline = _load_yaml(path)
    return build_graph(pipeline, source_path=path, template_roots=template_roots)


def write_graph(graph: Graph, output: Path, html_output: bool = False) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(graph.to_html() if html_output else graph.to_mermaid(), encoding="utf-8")


def write_outputs(graph: Graph, out_dir: Path, name: str) -> list[Path]:
    """Write <name>.html, <name>.md and <name>.mmd into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {".html": graph.to_html(), ".md": graph.to_markdown(), ".mmd": graph.to_mermaid()}
    for suffix, text in files.items():
        (out_dir / f"{name}{suffix}").write_text(text, encoding="utf-8")
    return [out_dir / f"{name}{suffix}" for suffix in files]
