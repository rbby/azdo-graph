from pathlib import Path

from ado_pipeline_graph.scan import build_repository_graph


def test_repository_graph_shows_shared_templates_and_variable_groups():
    root = Path(__file__).parent / "fixtures/repository"
    graph = build_repository_graph([root])
    labels = [label for label, _ in graph.nodes.values()]

    assert labels.count("Template: shared-job.yml") == 1
    assert labels.count("Variable group: SharedSecrets") == 1
    assert "PipelineA" in labels
    assert "PipelineB" in labels


def test_repository_graph_adds_observed_trigger_edges():
    root = Path(__file__).parent / "fixtures/repository"
    first = (root / "azure-pipelines.yml").resolve()
    second = (root / "azure-pipelines-two.yaml").resolve()
    graph = build_repository_graph(
        [root],
        rest_runs={2: [{"triggeredByBuild": {"definition": {"id": 1}}}]},
        definition_ids={str(first): 1, str(second): 2},
    )
    assert any(label == "triggered" for _, _, label in graph.edges)
