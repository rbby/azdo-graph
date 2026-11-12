from __future__ import annotations

import os
from pathlib import Path

import pytest

from ado_pipeline_graph.core import (
    MERMAID_CDN,
    Graph,
    _md_text,
    _mermaid_label,
    _split_label,
    build_graph,
    load_graph,
    write_graph,
    write_outputs,
)

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


def test_mermaid_label_escapes_everything():
    out = _mermaid_label('say "hi" #1; <b>x</b> & `code` | pipe')
    assert out == "say #quot;hi#quot; #35;1; #lt;b#gt;x#lt;/b#gt; #amp; #96;code#96; | pipe"
    for raw in ('"', "<", ">", "`"):
        assert raw not in out


def test_md_text_escapes_formatting():
    out = _md_text("a *b* _c_ `d` |e| <f>\ng")
    assert "\n" not in out
    for raw in ("*", "_", "`", "|", "<", ">"):
        assert out.count(raw) == out.count("\\" + raw)


def test_long_script_is_one_truncated_line():
    script = "\n".join(f"echo line {i}" for i in range(100))
    graph = build_graph({"steps": [{"script": script, "condition": "succeeded()"}]})
    mermaid = graph.to_mermaid()
    assert "line 1" not in mermaid
    assert 'echo line 0<br/>condition: succeeded()"' in mermaid
    title, extras = _split_label("x" * 200 + "\nmore", "step")
    assert len(title) == 60 and title.endswith("…") and extras == []


def test_edges_and_nodes_are_escaped_and_quoted():
    g = Graph()
    g.add_node("a", 'A "q" #1', kind="stage")
    g.add_node("b", "B <i>", kind="stage")
    g.add_edge("a", "b", "x|y")
    mermaid = g.to_mermaid()
    assert 'a["A #quot;q#quot; #35;1"]' in mermaid
    assert 'a -- "x|y" --> b' in mermaid


def test_edge_styles():
    g = Graph()
    for n, k in (("s1", "stage"), ("s2", "stage"), ("c", "condition"), ("t", "step")):
        g.add_node(n, n, kind=k)
    g.add_edge("s1", "s2", "then")
    g.add_edge("c", "t")
    g.add_edge("s2", "c", "dependsOn")
    mermaid = g.to_mermaid()
    assert 's1 -. "then" .-> s2' in mermaid
    assert "c -.-> t" in mermaid
    assert 's2 -- "dependsOn" --> c' in mermaid


def test_detail_levels():
    graph = load_graph(FIXTURES / "coverage" / "azure-pipelines.yml")
    stages, jobs, steps = (graph.to_mermaid(d) for d in ("stages", "jobs", "steps"))
    assert "stage_6_Build" in stages and "job_7" not in stages and "step_8" not in stages
    assert "job_7" in jobs and "step_8" not in jobs and "hook_23" not in jobs
    assert "step_8" in steps and "hook_23" in steps
    for line in stages.splitlines():  # no edge mentions a hidden node
        assert "job_" not in line and "step_" not in line
    assert graph.to_mermaid(direction="LR").startswith("flowchart LR\n")
    with pytest.raises(ValueError):
        graph.to_mermaid("everything")
    with pytest.raises(ValueError):
        graph.to_mermaid(direction="XX")


def test_html_page():
    graph = build_graph({"name": "My <Pipe>", "steps": [{"script": "echo"}]})
    page = graph.to_html()
    assert '<html lang="en">' in page
    assert "<title>My &lt;Pipe&gt;" in page and "<h1>My &lt;Pipe&gt;</h1>" in page
    assert "My <Pipe>" not in page
    assert "Legend" in page and "prefers-color-scheme" in page
    assert 'securityLevel: "strict"' in page
    assert MERMAID_CDN in page and "@11." in MERMAID_CDN and "@latest" not in MERMAID_CDN
    assert 'id="fallback" open' in page and "<noscript>" in page
    assert "flowchart TD" in page


def test_html_inlines_library():
    page = build_graph({"steps": [{"script": "echo"}]}).to_html(mermaid_js="var mermaid={x:'</script>'};")
    assert "var mermaid={x:'<\\/script>'};" in page
    assert "window.mermaid ||" in page


def test_write_functions_pass_options(tmp_path):
    graph = load_graph(FIXTURES / "coverage" / "azure-pipelines.yml")
    write_graph(graph, tmp_path / "a.mmd", detail="stages", direction="LR")
    assert (tmp_path / "a.mmd").read_text().startswith("flowchart LR")
    write_graph(graph, tmp_path / "a.html", True, mermaid_js="/*lib*/")
    assert "/*lib*/" in (tmp_path / "a.html").read_text()
    paths = write_outputs(graph, tmp_path, "g", detail="jobs")
    assert [p.suffix for p in paths] == [".html", ".md", ".mmd"]
    assert "step_8" not in (tmp_path / "g.mmd").read_text()
    write_graph(graph, tmp_path / "b.mmd")
    write_outputs(graph, tmp_path, "h")


def test_markdown_describes_pipeline():
    md = load_graph(FIXTURES / "local" / "azure-pipelines.yml").to_markdown()
    for heading in ("## Overview", "## Stages", "## Templates used", "## Diagram"):
        assert heading in md
    assert "- Stages: 2" in md
    assert "depends on Build" in md
    assert "unresolved" in md and "stadium" not in md
    assert md.rstrip().endswith("shown as written._")
    assert "## Triggers" not in md  # empty sections are omitted


def test_markdown_multi_pipeline():
    g = Graph()
    g.add_node("repo", "Repo", "box", kind="repository")
    for i in (1, 2):
        g.add_node(f"p{i}", f"Pipe {i}", "stadium", kind="pipeline", parent="repo")
        g.add_node(f"s{i}", f"Stage {i}", "stadium", kind="stage", parent=f"p{i}")
        g.add_edge(f"p{i}", f"s{i}")
    g.add_node("t", "shared.yml", "diamond", kind="template", parent="repo")
    g.add_edge("p1", "t", "uses")
    md = g.to_markdown()
    assert md.startswith("# Repo\n")
    assert "| Pipe 1 | 1 | 0 | 0 | shared.yml | - |" in md
    assert "## Pipe 1" in md and "## Pipe 2" in md


@pytest.mark.parametrize("fixture", ["local", "coverage"])
@pytest.mark.parametrize("suffix", [".mmd", ".md"])
def test_golden(fixture, suffix):
    graph = load_graph(FIXTURES / fixture / "azure-pipelines.yml")
    text = graph.to_mermaid() if suffix == ".mmd" else graph.to_markdown()
    golden = GOLDEN / f"{fixture}{suffix}"
    if os.environ.get("UPDATE_GOLDEN"):
        golden.write_text(text, encoding="utf-8")
    assert text == golden.read_text(encoding="utf-8")
    assert str(FIXTURES) not in text
