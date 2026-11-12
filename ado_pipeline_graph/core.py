from __future__ import annotations

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# --- rendering helpers -------------------------------------------------------

MERMAID_VERSION = "11.15.0"
MERMAID_CDN = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.esm.min.mjs"
_MAX_LABEL = 60
_EXTRA_LINE = re.compile(r"(environment|matrix|parallel|condition): ")
_MERMAID_ENTITIES = (("#", "#35;"), ("&", "#amp;"), ("<", "#lt;"), (">", "#gt;"), ('"', "#quot;"), ("`", "#96;"))
_DETAIL_RANK = {"stages": 1, "jobs": 2, "steps": 3}
_KIND_RANK = {"stage": 1, "job": 2, "step": 3, "hook": 3}
_PASS_THROUGH = ("condition", "loop", "external_template")
_DIRECTIONS = ("TD", "TB", "BT", "LR", "RL")


def _truncate(text: str, limit: int = _MAX_LABEL) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _split_label(label: str, kind: str | None = None) -> tuple[str, list[str]]:
    """(one-line truncated title, short extra lines). Steps drop the rest of a script body."""
    lines = [line for line in label.split("\n") if line.strip()]
    if not lines:
        return "", []
    extras = lines[1:]
    if kind == "step":
        extras = [line for line in extras if _EXTRA_LINE.match(line.strip())]
    return _truncate(lines[0]), [_truncate(line) for line in extras]


def _mermaid_label(text: str) -> str:
    """Escape one line of text for use inside a quoted Mermaid label (nodes and edges)."""
    for char, entity in _MERMAID_ENTITIES:
        text = text.replace(char, entity)
    return text


def _md_text(text: str) -> str:
    """Collapse whitespace and backslash-escape Markdown formatting characters."""
    return re.sub(r"([\\`*_\[\]<>|~#&])", r"\\\1", " ".join(text.split()))


def _md_code(text: str) -> str:
    text = " ".join(text.split())
    return f"`` {text} ``" if "`" in text else f"`{text}`"


def _children(graph: Graph) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for node_id in graph.nodes:
        parent = graph.parents.get(node_id)
        if parent in graph.nodes:
            children.setdefault(parent, []).append(node_id)
    return children


def _subtree(children: dict[str, list[str]], root: str) -> list[str]:
    found = [root]
    for node_id in found:
        found.extend(children.get(node_id, ()))
    return found


_LEGEND = (
    "<li><b>Rounded box</b>: pipeline or stage</li>"
    "<li><b>Box</b>: job, step or hook</li>"
    "<li><b>Diamond</b>: condition, loop, trigger, resource, template or variable group</li>"
    "<li><b>Dashed outline</b>: template that could not be resolved</li>"
    "<li><b>Dashed line</b>: implicit stage order or edge out of a condition or loop</li>"
)

_STYLE = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1f2328;--muted:#59636e;--border:#d1d9e0;--panel:#f6f8fa}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--border:#3d444d;--panel:#151b23}}
body{margin:0;padding:1rem 1.5rem;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
h1{font-size:1.4rem;margin:.2rem 0 .6rem}
details{margin:.5rem 0;color:var(--muted)}
ul.legend{margin:.3rem 0 0;padding-left:1.2rem}
.toolbar{display:flex;gap:.4rem;align-items:center;margin:.6rem 0}
button{background:var(--panel);color:var(--fg);border:1px solid var(--border);border-radius:6px;
padding:.2rem .7rem;cursor:pointer}
#viewport{overflow:auto;border:1px solid var(--border);border-radius:6px;padding:.5rem;max-height:80vh}
#view{transform-origin:0 0;width:max-content}
pre{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:.7rem;overflow:auto;font-size:12px}
#error{color:#d1242f}
body.rendered #fallback,body.rendered #error{display:none}
body:not(.rendered) .toolbar,body:not(.rendered) #viewport{display:none}
"""

_SCRIPT = """
(async function () {
  var src = document.getElementById("source").textContent;
  var view = document.getElementById("view");
  var zoom = 1;
  function setZoom(z) { zoom = Math.max(0.1, Math.min(5, z)); view.style.transform = "scale(" + zoom + ")"; }
  document.getElementById("zin").onclick = function () { setZoom(zoom * 1.25); };
  document.getElementById("zout").onclick = function () { setZoom(zoom / 1.25); };
  document.getElementById("zreset").onclick = function () { setZoom(1); };
  try {
    var m = window.mermaid || (await import("__CDN__")).default;
    var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    m.initialize({startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "default",
      maxTextSize: 5000000, maxEdges: 10000, flowchart: {useMaxWidth: false}});
    var out = await m.render("diagram", src);
    view.innerHTML = out.svg;
    document.body.classList.add("rendered");
  } catch (e) {
    document.getElementById("error").textContent = "Could not render the diagram (" + (e && e.message || e) +
      "). The Mermaid source is shown below.";
  }
})();
"""


@dataclass
class Graph:
    nodes: dict[str, tuple[str, str]] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)
    templates: set[str] = field(default_factory=set)
    variable_groups: set[str] = field(default_factory=set)
    pipeline_name: str = "Pipeline"
    kinds: dict[str, str] = field(default_factory=dict)
    parents: dict[str, str] = field(default_factory=dict)

    def add_node(
        self,
        node_id: str,
        label: str,
        shape: str = "box",
        *,
        kind: str | None = None,
        parent: str | None = None,
    ) -> None:
        self.nodes[node_id] = (label, shape)
        if kind:
            self.kinds[node_id] = kind
        if parent:
            self.parents[node_id] = parent

    def add_edge(self, source: str, target: str, label: str = "") -> None:
        edge = (source, target, label)
        if edge not in self.edges:  # ponytail: O(n) scan, fine for pipeline-sized graphs
            self.edges.append(edge)

    def _rank(self, node_id: str) -> int:
        kind = self.kinds.get(node_id)
        if kind in _KIND_RANK:
            return _KIND_RANK[kind]
        if kind in _PASS_THROUGH:
            parent = self.parents.get(node_id)
            if parent is None:
                return 1
            if self.kinds.get(parent) in _PASS_THROUGH:
                return self._rank(parent)
            return min(self._rank(parent) + 1, 3)
        return 0

    def to_mermaid(self, detail: str = "steps", direction: str = "TD") -> str:
        if detail not in _DETAIL_RANK:
            raise ValueError(f"detail must be one of {', '.join(_DETAIL_RANK)}: {detail!r}")
        if direction not in _DIRECTIONS:
            raise ValueError(f"direction must be one of {', '.join(_DIRECTIONS)}: {direction!r}")
        shown = {n for n in self.nodes if self._rank(n) <= _DETAIL_RANK[detail]}
        lines = [f"flowchart {direction}"]
        for node_id, (label, shape) in self.nodes.items():
            if node_id not in shown:
                continue
            title, extras = _split_label(label, self.kinds.get(node_id))
            safe = "<br/>".join(_mermaid_label(line) for line in [title, *extras])
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
            if source not in shown or target not in shown:
                continue
            text = f' "{_mermaid_label(_truncate(label))}" ' if label.strip() else ""
            if label == "then" or self.kinds.get(source) in ("condition", "loop"):
                arrow = f"-.{text}.->" if text else "-.->"
            else:
                arrow = f"--{text}-->" if text else "-->"
            lines.append(f"  {source} {arrow} {target}")
        return "\n".join(lines) + "\n"

    def to_markdown(self, detail: str = "steps", direction: str = "TD") -> str:
        children = _children(self)
        pipelines = [n for n, k in self.kinds.items() if k == "pipeline" and n in self.nodes]
        multi = len(pipelines) > 1
        title = self.pipeline_name
        for node_id, kind in self.kinds.items():
            if kind == "repository" and node_id in self.nodes:
                title = _split_label(self.nodes[node_id][0])[0]
                break
        lines = [f"# {_md_text(title)}", ""]
        if multi:
            lines += ["| Pipeline | Stages | Jobs | Steps | Templates | Variable groups |", "|---|---|---|---|---|---|"]
            for pid in pipelines:
                info = self._pipeline_info(pid, children)
                lines.append(
                    f"| {_md_text(info['name'])} | {info['counts']['stage']} | {info['counts']['job']} | "
                    f"{info['counts']['step']} | {_md_text(', '.join(info['templates']) or '-')} | "
                    f"{_md_text(', '.join(info['groups']) or '-')} |"
                )
            lines.append("")
        roots: list[str | None] = list(pipelines) or [None]
        for root in roots:
            lines += self._markdown_pipeline(root, children, multi)
        lines += ["## Diagram", "", "```mermaid", self.to_mermaid(detail, direction).rstrip(), "```", ""]
        lines += [
            "---",
            "",
            "_This file is generated by ado-pipeline-graph from the pipeline YAML. "
            "Pipeline expressions are not evaluated; conditions and templates are shown as written._",
        ]
        return "\n".join(lines) + "\n"

    def _pipeline_info(self, pid: str | None, children: dict[str, list[str]]) -> dict[str, Any]:
        members = _subtree(children, pid) if pid else list(self.nodes)
        inside = set(members)
        counts = {k: 0 for k in ("stage", "job", "step")}
        for node_id in members:
            if self.kinds.get(node_id) in counts:
                counts[self.kinds[node_id]] += 1
        templates: set[str] = set()
        groups: set[str] = set()
        for source, target, _ in self.edges:
            if source in inside and target not in inside:
                kind = self.kinds.get(target)
                label = self.nodes.get(target, ("",))[0]
                if kind == "template":
                    templates.add(_split_label(label)[0].removeprefix("Template: "))
                elif kind == "variable_group":
                    groups.add(_split_label(label)[0].removeprefix("Variable group: "))
        for node_id in members:
            if self.kinds.get(node_id) == "variable_group":
                groups.add(_split_label(self.nodes[node_id][0])[0].removeprefix("Variable group: "))
        if list(self.kinds.values()).count("pipeline") <= 1:
            templates |= self.templates
            groups |= self.variable_groups
        external = {
            _split_label(self.nodes[n][0])[0].removeprefix("External template: ")
            for n in members
            if self.kinds.get(n) == "external_template"
        }
        name = _split_label(self.nodes[pid][0])[0] if pid else self.pipeline_name
        return {
            "name": name,
            "counts": counts,
            "templates": sorted(templates),
            "groups": sorted(groups),
            "external": sorted(external),
            "members": members,
        }

    def _markdown_pipeline(self, pid: str | None, children: dict[str, list[str]], multi: bool) -> list[str]:
        info = self._pipeline_info(pid, children)
        sub = "###" if multi else "##"
        out: list[str] = []
        if multi:
            out += [f"## {_md_text(info['name'])}", ""]
        counts = info["counts"]
        out += [
            f"{sub} Overview",
            "",
            f"- Name: {_md_text(info['name'])}",
            f"- Stages: {counts['stage']}",
            f"- Jobs: {counts['job']}",
            f"- Steps: {counts['step']}",
            "",
        ]
        direct = children.get(pid, []) if pid else []
        triggers = []
        for node_id in direct:
            if self.kinds.get(node_id) not in ("trigger", "resource"):
                continue
            text = _md_text(_split_label(self.nodes[node_id][0])[0])
            for source, target, label in self.edges:
                if source == node_id and target == pid:
                    if self.kinds[node_id] == "resource" and label == "trigger":
                        text += " (triggers this pipeline)"
                    elif label.startswith("trigger ("):
                        text += f" {_md_text(label[len('trigger ') :])}"
            triggers.append(f"- {text}")
        for source, target, label in self.edges:
            if target == pid and self.kinds.get(source) == "pipeline" and source != pid:
                suffix = f" ({_md_text(label)})" if label else ""
                triggers.append(f"- Triggered by pipeline {_md_text(self.nodes[source][0].split(chr(10))[0])}{suffix}")
        if triggers:
            out += [f"{sub} Triggers and resources", ""] + triggers + [""]
        body: list[str] = []
        for node_id in direct:
            if self.kinds.get(node_id) in ("stage", "job", "step", "condition", "loop", "external_template"):
                body += self._markdown_node(node_id, children, 0)
        if body:
            out += [f"{sub} Stages", ""] + body + [""]
        if info["templates"] or info["external"]:
            out += [f"{sub} Templates used", ""]
            out += [f"- {_md_code(t)}" for t in info["templates"]]
            out += [f"- {_md_code(t)} (unresolved, not expanded)" for t in info["external"]]
            out.append("")
        if info["groups"]:
            out += [f"{sub} Variable groups", ""] + [f"- {_md_text(g)}" for g in info["groups"]] + [""]
        return out

    def _markdown_node(self, node_id: str, children: dict[str, list[str]], depth: int) -> list[str]:
        kind = self.kinds.get(node_id)
        title, extras = _split_label(self.nodes[node_id][0], kind)
        if kind == "condition":
            head = f"Conditional block {_md_code(title)}"
        elif kind == "loop":
            head = f"Loop {_md_code(title)}"
        elif kind == "external_template":
            head = f"Unresolved template {_md_code(title.removeprefix('External template: '))}"
        else:
            word = {"stage": "Stage", "job": "Job", "step": "Step", "hook": "Hook"}.get(kind or "", "Item")
            head = f"{word}: **{_md_text(title)}**" if kind in ("stage", "job") else f"{word}: {_md_text(title)}"
        notes = [_md_text(e) for e in extras]
        for source, target, label in self.edges:
            if target == node_id and label in ("dependsOn", "then") and source in self.nodes:
                name = _md_text(_split_label(self.nodes[source][0])[0])
                notes.append(f"depends on {name}" if label == "dependsOn" else f"runs after {name}")
        line = "  " * depth + f"- {head}" + (f" ({'; '.join(notes)})" if notes else "")
        out = [line]
        for child in children.get(node_id, ()):
            if self.kinds.get(child) in ("stage", "job", "step", "hook", "condition", "loop", "external_template"):
                out += self._markdown_node(child, children, depth + 1)
        return out

    def to_html(
        self,
        detail: str = "steps",
        direction: str = "TD",
        *,
        mermaid_js: str | None = None,
    ) -> str:
        source = html.escape(self.to_mermaid(detail, direction))
        name = html.escape(self.pipeline_name)
        script = _SCRIPT.replace("__CDN__", MERMAID_CDN)
        library = ""
        if mermaid_js is not None:
            library = "<script>" + mermaid_js.replace("</script", "<\\/script") + "</script>\n"
        return (
            '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{name} - pipeline graph</title><style>{_STYLE}</style></head><body>\n"
            f"<h1>{name}</h1>\n"
            f'<details><summary>Legend</summary><ul class="legend">{_LEGEND}</ul></details>\n'
            '<div class="toolbar"><button id="zout" type="button" aria-label="Zoom out">-</button>'
            '<button id="zreset" type="button">100%</button>'
            '<button id="zin" type="button" aria-label="Zoom in">+</button></div>\n'
            '<div id="viewport"><div id="view"></div></div>\n'
            '<p id="error"></p>\n'
            "<noscript><p>JavaScript is disabled or blocked, so the diagram cannot be drawn. "
            "The Mermaid source is shown below.</p></noscript>\n"
            f'<details id="fallback" open><summary>Mermaid source</summary><pre id="source">{source}</pre></details>\n'
            f"{library}<script>{script}</script>\n</body></html>\n"
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
            (key, str(value)) for key, value in raw_parameters.items() if isinstance(value, (str, int, float, bool))
        )

    variables: dict[str, str] = {}
    raw_variables = data.get("variables", [])
    if isinstance(raw_variables, dict):
        variables.update(
            (key, str(value)) for key, value in raw_variables.items() if isinstance(value, (str, int, float, bool))
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


def find_repo_root(path: Path) -> Path:
    """Nearest ancestor of ``path`` (file or directory) containing ``.git``, else its directory."""
    path = path.resolve()
    start = path if path.is_dir() else path.parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def resolve_template(
    reference: str,
    current_dir: Path,
    template_roots: dict[str, Path],
    repo_root: Path,
) -> Path | None:
    """Resolve a template reference to an existing file, or None.

    ``name@alias`` resolves against ``template_roots[alias]``; otherwise a leading
    ``/`` resolves against ``repo_root`` and anything else against ``current_dir``.
    The result must stay inside the repo root (or alias root), else None.
    """
    template, alias = _template_parts(reference)
    root = template_roots.get(alias) if alias else repo_root
    if root is None:
        return None
    root = root.resolve()
    base = root if alias or template.startswith("/") else current_dir
    candidate = (base / template.lstrip("/")).resolve()
    if not candidate.is_relative_to(root):
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


_LITERAL = (str, int, float, bool)
_CONTEXTS = ("variables", "stages", "jobs", "steps")


def _expression_key(item: Any) -> str | None:
    """The key of a ``- ${{ if ... }}: [...]``-style single-key item, else None."""
    if isinstance(item, dict) and len(item) == 1:
        key = next(iter(item))
        if isinstance(key, str) and key.lstrip().startswith("${{"):
            return key
    return None


def _variable_list(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [{"name": key, "value": item} for key, item in value.items()]
    return value if isinstance(value, list) else []


def _expand(
    pipeline: dict[str, Any],
    graph: Graph,
    current_dir: Path,
    repo_root: Path,
    template_roots: dict[str, Path],
    seen: set[Path],
) -> dict[str, Any]:
    # A location is (current_dir, root, alias): templates inside an aliased checkout
    # resolve "/" against that checkout and are named "path@alias".
    def load(reference: str, loc: tuple[Path, Path, str | None], seen: set[Path]) -> Any:
        resolved = resolve_template(reference, loc[0], template_roots, loc[1])
        if resolved is None:
            return None
        root, alias = loc[1], loc[2]
        ref_alias = _template_parts(reference)[1]
        if ref_alias:
            root, alias = template_roots[ref_alias].resolve(), ref_alias
        name = resolved.relative_to(root).as_posix() + (f"@{alias}" if alias else "")
        graph.templates.add(name)
        if resolved in seen:
            raise ValueError(f"recursive template reference: {name}")
        return _load_yaml(resolved), (resolved.parent, root, alias), seen | {resolved}, name

    def open_scope(
        data: dict[str, Any], loc: Any, passed: Any, outer: dict[str, str], seen: set[Path]
    ) -> dict[str, Any]:
        parameters, _ = _literal_values(data)
        if isinstance(passed, dict):
            parameters.update((k, str(v)) for k, v in passed.items() if isinstance(v, _LITERAL))
        data = _substitute(data, parameters, {})
        if "variables" in data:
            data["variables"] = expand_list(_variable_list(data["variables"]), "variables", loc, outer, seen)
        variables = {**outer, **_literal_values(data)[1]}
        data = _substitute(data, {}, variables)

        extends = data.get("extends")
        if isinstance(extends, dict) and isinstance(extends.get("template"), str):
            loaded = load(extends["template"], loc, seen)
            if loaded:
                base_data, base_loc, base_seen, _ = loaded
                base = open_scope(base_data, base_loc, extends.get("parameters"), variables, base_seen)
                # The template owns stages/jobs/steps; the extending file keeps its
                # other keys (trigger, resources, ...) and both variable lists apply.
                merged = {k: v for k, v in data.items() if k not in ("extends", "stages", "jobs", "steps")}
                for key, value in base.items():
                    if key == "variables":
                        merged[key] = _variable_list(data.get("variables")) + _variable_list(value)
                    elif key in ("stages", "jobs", "steps") or key not in merged:
                        merged[key] = value
                return merged
            label = f"External template: {extends['template']}"
            graph.add_node("external_extends", label, "external", kind="external_template", parent="pipeline")
            graph.add_edge("pipeline", "external_extends")
        return expand_body(data, loc, variables, seen)

    def expand_body(data: dict[str, Any], loc: Any, variables: dict[str, str], seen: set[Path]) -> dict[str, Any]:
        for key, value in list(data.items()):
            if key in _CONTEXTS and isinstance(value, list):
                data[key] = expand_list(value, key, loc, variables, seen)
            elif isinstance(value, dict) and key not in ("parameters", "extends"):
                data[key] = expand_body(value, loc, variables, seen)
        return data

    def expand_list(items: list[Any], context: str, loc: Any, variables: dict[str, str], seen: set[Path]) -> list[Any]:
        expanded: list[Any] = []
        for item in items:
            expression = _expression_key(item)
            if expression and isinstance(item[expression], list):
                expanded.append({expression: expand_list(item[expression], context, loc, variables, seen)})
            elif isinstance(item, dict) and isinstance(item.get("template"), str):
                loaded = load(item["template"], loc, seen)
                if loaded is None:
                    expanded.append(item if context == "variables" else {"__external_template__": item["template"]})
                    continue
                data, template_loc, template_seen, name = loaded
                data = open_scope(data, template_loc, item.get("parameters"), variables, template_seen)
                content = data.get(context)
                if context == "variables":
                    content = _variable_list(content)
                if not isinstance(content, list):
                    raise ValueError(f"template {name} does not define a {context} list")
                expanded.extend(content)
            elif isinstance(item, dict):
                expanded.append(expand_body(item, loc, variables, seen))
            else:
                expanded.append(item)
        return expanded

    return open_scope(pipeline, (current_dir, repo_root, None), None, {}, seen)


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not slug or slug[0].isdigit():
        slug = "n_" + slug
    return slug


def _collect_variable_groups(value: Any, in_variables: bool = False) -> set[str]:
    """Groups from ``- group: X`` items of any ``variables`` list (including under ``${{ if }}``)."""
    groups: set[str] = set()
    if isinstance(value, list):
        for item in value:
            groups |= _collect_variable_groups(item, in_variables)
    elif isinstance(value, dict):
        if in_variables and isinstance(value.get("group"), str):
            groups.add(value["group"])
        for key, item in value.items():
            nested = key == "variables" or (in_variables and _expression_key({key: item}) is not None)
            groups |= _collect_variable_groups(item, nested)
    return groups


_SCRIPT_KEYS = ("script", "bash", "pwsh", "powershell")
_STEP_KEYS = ("checkout", "download", "downloadBuild", "getPackage", "publish", "reviewApp", "template")


def _step_label(item: dict[str, Any]) -> str:
    if item.get("displayName"):
        return str(item["displayName"])
    for key in ("task", *_SCRIPT_KEYS):
        if item.get(key):
            return str(item[key])
    for key in _STEP_KEYS:
        if key in item:
            return f"{key}: {item[key]}"
    return "Step"


def _label_suffix(item: dict[str, Any]) -> str:
    parts = []
    environment = item.get("environment")
    if isinstance(environment, dict):
        environment = environment.get("name")
    if environment:
        parts.append(f"environment: {environment}")
    strategy = item.get("strategy")
    if isinstance(strategy, dict):
        matrix = strategy.get("matrix")
        if matrix:
            parts.append("matrix: " + (", ".join(map(str, matrix)) if isinstance(matrix, dict) else str(matrix)))
        if "parallel" in strategy:
            parts.append(f"parallel: {strategy['parallel']}")
    if "condition" in item:
        parts.append(f"condition: {item['condition']}")
    return "".join("\n" + part for part in parts)


def _trigger_summary(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("branches", [])
        if isinstance(value, dict):
            value = value.get("include", [])
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return "" if value is None else str(value)


def build_graph(
    pipeline: dict[str, Any],
    *,
    source_path: Path | None = None,
    template_roots: dict[str, Path] | None = None,
    run_history: list[dict[str, Any]] | None = None,
    variable_groups: list[dict[str, Any]] | None = None,
    include_variable_group_nodes: bool = True,
    repo_root: Path | None = None,
) -> Graph:
    """Build a Graph from a parsed pipeline without evaluating any expression.

    Templates resolve via ``resolve_template``; ``repo_root`` defaults to
    ``find_repo_root(source_path)``.

    Substitution: only ``${{ parameters.x }}`` / ``${{ variables.x }}`` with literal
    values are replaced; ``$(var)``, ``parameters['x']`` and every other expression is
    left as written. Scoping:

    - Parameters: each file (pipeline, extended or included template) sees only its
      own declared parameter defaults overlaid with the values its caller passed.
    - Variables: a template sees the variables in scope at its caller, overridden by
      the template's own top-level variables (innermost wins). The same rule applies
      to ``extends`` and included templates; ``- template:`` items under
      ``variables`` are expanded first.

    Node kinds (``graph.kinds``): pipeline, stage, job, step, hook (deployment
    lifecycle hook), condition (``${{ if/elseif/else }}``), loop (``${{ each }}``),
    external_template, variable_group, trigger, resource. ``graph.parents`` maps a
    node to its containing node. Edge labels: ``dependsOn`` (explicit), ``then``
    (implicit sequential stage order), ``uses``, ``trigger``, ``resource`` and
    ``trigger (N of M runs)`` for observed run history.
    """
    default_name = source_path.stem if source_path else "Pipeline"
    pipeline_name = pipeline.get("name")
    graph = Graph(pipeline_name=pipeline_name if isinstance(pipeline_name, str) and pipeline_name else default_name)
    initial_path = source_path.resolve() if source_path else None
    current_dir = initial_path.parent if initial_path else Path("/__ado_remote_pipeline__")
    if repo_root is not None:
        root_dir = Path(repo_root).resolve()
    else:
        root_dir = find_repo_root(initial_path) if initial_path else current_dir
    root_id = "pipeline"
    graph.add_node(root_id, graph.pipeline_name, "stadium", kind="pipeline")
    pipeline = _expand(
        pipeline,
        graph,
        current_dir,
        root_dir,
        template_roots or {},
        {initial_path} if initial_path else set(),
    )
    counter = 0

    def new_id(prefix: str, label: str) -> str:
        nonlocal counter
        counter += 1
        return f"{prefix}_{counter}_{_slug(label)[:32]}"

    def add(prefix: str, label: str, shape: str, kind: str, parent: str) -> str:
        node_id = new_id(prefix, label)
        graph.add_node(node_id, label, shape, kind=kind, parent=parent)
        return node_id

    def add_list(items: Any, parent_id: str, level: str) -> list[tuple[str, dict[str, Any]]]:
        """Add stage/job/step items; return the real (node_id, item) pairs in order."""
        added: list[tuple[str, dict[str, Any]]] = []
        previous = parent_id
        for item in items if isinstance(items, list) else ():
            if not isinstance(item, dict):
                continue
            expression = _expression_key(item)
            if expression:
                text = expression.strip().removeprefix("${{").removesuffix("}}").strip()
                kind = "loop" if text.startswith("each ") else "condition"
                node_id = add(kind, text, "diamond", kind, parent_id)
                graph.add_edge(previous, node_id)
                added += add_list(item[expression], node_id, level)
            elif "__external_template__" in item:
                label = f"External template: {item['__external_template__']}"
                node_id = add("external", label, "external", "external_template", parent_id)
                graph.add_edge(previous, node_id)
            else:
                node_id = add_item(item, parent_id, level)
                graph.add_edge(previous, node_id)
                added.append((node_id, item))
            if level == "step":
                previous = node_id
        return added

    def add_item(item: dict[str, Any], parent_id: str, level: str) -> str:
        if level == "step":
            return add("step", _step_label(item) + _label_suffix(item), "box", "step", parent_id)
        if level == "stage":
            name = str(item.get("displayName") or item.get("stage") or "Stage")
            node_id = add("stage", name + _label_suffix(item), "stadium", "stage", parent_id)
            link_dependencies(add_list(item.get("jobs"), node_id, "job"), implicit=False)
            add_list(item.get("steps"), node_id, "step")
            return node_id
        name = str(
            item.get("displayName") or item.get("job") or item.get("deployment") or item.get("template") or "Job"
        )
        node_id = add("job", name + _label_suffix(item), "box", "job", parent_id)
        add_list(item.get("steps"), node_id, "step")
        strategy = item.get("strategy")
        for strategy_name in ("runOnce", "rolling", "canary"):
            hooks = strategy.get(strategy_name) if isinstance(strategy, dict) else None
            if not isinstance(hooks, dict):
                continue
            on = hooks.get("on", hooks.get(True))  # YAML 1.1 loads the key `on` as True
            on = on if isinstance(on, dict) else {}
            phases = [(hook, hooks.get(hook)) for hook in ("preDeploy", "deploy", "routeTraffic", "postRouteTraffic")]
            phases += [(f"on.{hook}", on.get(hook)) for hook in ("success", "failure")]
            for hook, body in phases:
                if isinstance(body, dict) and isinstance(body.get("steps"), list):
                    hook_id = add("hook", f"{strategy_name}: {hook}", "box", "hook", node_id)
                    graph.add_edge(node_id, hook_id)
                    add_list(body["steps"], hook_id, "step")
        return node_id

    def link_dependencies(added: list[tuple[str, dict[str, Any]]], implicit: bool) -> None:
        ids = {}
        for node_id, item in added:
            name = item.get("stage", item.get("job", item.get("deployment")))
            if name is not None:
                ids[str(name)] = node_id
        previous = None
        for node_id, item in added:
            if "dependsOn" in item:
                depends = item["dependsOn"]
                for dependency in (
                    [depends] if isinstance(depends, str) else depends if isinstance(depends, list) else ()
                ):
                    if str(dependency) in ids:
                        graph.add_edge(ids[str(dependency)], node_id, "dependsOn")
            elif implicit and previous:
                graph.add_edge(previous, node_id, "then")
            previous = node_id

    for key, title in (("trigger", "CI trigger"), ("pr", "PR trigger")):
        if key in pipeline:
            summary = _trigger_summary(pipeline[key])
            node_id = add("trigger", title + (f": {summary}" if summary else ""), "diamond", "trigger", root_id)
            graph.add_edge(node_id, root_id, "trigger")
    for schedule in pipeline.get("schedules") or ():
        if isinstance(schedule, dict):
            label = f"Schedule: {schedule.get('displayName') or schedule.get('cron')}"
            graph.add_edge(add("trigger", label, "diamond", "trigger", root_id), root_id, "trigger")
    resources = pipeline.get("resources")
    if not isinstance(resources, dict):
        resources = {}
    for key, title in (("pipelines", "Pipeline resource"), ("repositories", "Repository resource")):
        for resource in resources.get(key) or ():
            if not isinstance(resource, dict):
                continue
            alias = resource.get("pipeline", resource.get("repository"))
            source = resource.get("source", resource.get("name", alias))
            label = f"{title}: {source}" + (f" ({alias})" if alias != source else "")
            triggers = key == "pipelines" and resource.get("trigger") not in (None, "none", False)
            node_id = add("resource", label, "diamond", "resource", root_id)
            graph.add_edge(node_id, root_id, "trigger" if triggers else "resource")

    if isinstance(pipeline.get("stages"), list):
        link_dependencies(add_list(pipeline["stages"], root_id, "stage"), implicit=True)
    elif isinstance(pipeline.get("jobs"), list):
        link_dependencies(add_list(pipeline["jobs"], root_id, "job"), implicit=False)
    else:
        add_list(pipeline.get("steps"), root_id, "step")

    graph.variable_groups.update(_collect_variable_groups(pipeline))
    api_groups = {
        item["name"]: item
        for item in (variable_groups or [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if include_variable_group_nodes:
        for group in sorted(graph.variable_groups):
            api_id = api_groups.get(group, {}).get("id")
            label = f"Variable group: {group}"
            if api_id is not None:
                label += f" (ID {api_id})"
            group_id = new_id("group", group)
            graph.add_node(group_id, label, "diamond", kind="variable_group", parent=root_id)
            graph.add_edge(root_id, group_id, "uses")
    upstream: dict[Any, list[Any]] = {}
    for run in run_history or ():
        triggered_by = run.get("triggeredByBuild")
        if isinstance(triggered_by, dict):
            definition = triggered_by.get("definition")
            definition = definition if isinstance(definition, dict) else {}
            name = str(definition.get("name", "upstream pipeline"))
            upstream.setdefault((definition.get("id"), name), [name, 0])[1] += 1
    for name, count in upstream.values():
        node_id = add("trigger", f"Triggered by: {name}", "diamond", "trigger", root_id)
        graph.add_edge(node_id, root_id, f"trigger ({count} of {len(run_history or ())} runs)")
    return graph


def load_graph(
    path: Path,
    template_roots: dict[str, Path] | None = None,
    *,
    repo_root: Path | None = None,
) -> Graph:
    pipeline = _load_yaml(path)
    return build_graph(pipeline, source_path=path, template_roots=template_roots, repo_root=repo_root)


def write_graph(
    graph: Graph,
    output: Path,
    html_output: bool = False,
    *,
    detail: str = "steps",
    direction: str = "TD",
    mermaid_js: str | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    text = (
        graph.to_html(detail, direction, mermaid_js=mermaid_js) if html_output else graph.to_mermaid(detail, direction)
    )
    output.write_text(text, encoding="utf-8")


def write_outputs(
    graph: Graph,
    out_dir: Path,
    name: str,
    *,
    detail: str = "steps",
    direction: str = "TD",
    mermaid_js: str | None = None,
) -> list[Path]:
    """Write <name>.html, <name>.md and <name>.mmd into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        ".html": graph.to_html(detail, direction, mermaid_js=mermaid_js),
        ".md": graph.to_markdown(detail, direction),
        ".mmd": graph.to_mermaid(detail, direction),
    }
    for suffix, text in files.items():
        (out_dir / f"{name}{suffix}").write_text(text, encoding="utf-8")
    return [out_dir / f"{name}{suffix}" for suffix in files]
