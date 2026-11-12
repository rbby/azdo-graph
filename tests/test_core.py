from pathlib import Path

from ado_pipeline_graph.core import build_graph, load_graph, parse_template_roots


FIXTURES = Path(__file__).parent / "fixtures"


def test_local_templates_dependencies_and_external_node():
    graph = load_graph(FIXTURES / "local/azure-pipelines.yml")
    labels = [label for label, _ in graph.nodes.values()]

    assert "Build" in labels
    assert "Compile" in labels
    assert "Build Release" in labels
    assert any(label.startswith("External template: remote.yml@missingRepo") for label in labels)
    assert any(label == "dependsOn" for _, _, label in graph.edges)
    assert "echo building Release-done" in labels


def test_template_root_alias_resolves_local_checkout():
    roots = parse_template_roots([f"shared={FIXTURES / 'alias-root'}"])
    graph = load_graph(FIXTURES / "alias-pipeline.yml", roots)

    assert "FromAlias" in [label for label, _ in graph.nodes.values()]
    assert not any(label.startswith("External template:") for label, _ in graph.nodes.values())


def test_template_root_syntax_validation():
    try:
        parse_template_roots(["missing-equals"])
    except ValueError as exc:
        assert "ALIAS=PATH" in str(exc)
    else:
        raise AssertionError("invalid template root was accepted")


def test_mermaid_and_html_output():
    graph = load_graph(FIXTURES / "local/azure-pipelines.yml")
    mermaid = graph.to_mermaid()
    assert mermaid.startswith("flowchart TD\n")
    assert "dependsOn" in mermaid
    assert "mermaid" in graph.to_html()


def test_extends_parameter_override_and_rest_variable_group():
    graph = load_graph(FIXTURES / "extends-pipeline.yml")
    assert "echo Release" in [label for label, _ in graph.nodes.values()]

    rest_graph = build_graph(
        {"variables": [{"group": "Shared"}]},
        variable_groups=[{"name": "Shared", "id": 13}],
    )
    assert "Variable group: Shared (ID 13)" in [
        label for label, _ in rest_graph.nodes.values()
    ]


def test_unresolved_external_extends_and_nested_variable_group():
    graph = build_graph(
        {
            "extends": {"template": "pipeline.yml@anotherRepo"},
            "stages": [{"stage": "Build", "variables": [{"group": "Nested"}]}],
        }
    )
    labels = [label for label, _ in graph.nodes.values()]
    assert "External template: pipeline.yml@anotherRepo" in labels
    assert "Variable group: Nested" in labels


def test_markdown_and_out_dir(tmp_path):
    from ado_pipeline_graph.cli import main
    graph = load_graph(FIXTURES / "local/azure-pipelines.yml")
    md = graph.to_markdown()
    assert "```mermaid" in md and "## Relationships" in md
    assert main([str(FIXTURES / "local/azure-pipelines.yml"), "--out-dir", str(tmp_path / "ado-graph")]) == 0
    assert {p.suffix for p in (tmp_path / "ado-graph").iterdir()} == {".html", ".md", ".mmd"}
