from pathlib import Path

import pytest

from ado_pipeline_graph.scan import build_repository_graph, yaml_files


def test_repository_graph_shows_shared_templates_and_variable_groups():
    root = Path(__file__).parent / "fixtures/repository"
    graph = build_repository_graph([root])
    labels = [label for label, _ in graph.nodes.values()]

    assert [lbl for lbl in labels if lbl.startswith("Template: ") and lbl.endswith("templates/shared-job.yml")] != []
    assert sum(lbl.endswith("shared-job.yml") for lbl in labels) == 1
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
    assert any(label == "triggered (1 of 1 runs)" for _, _, label in graph.edges)


PIPE = "trigger: none\nsteps:\n  - script: echo hi\n"


def _tree(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "templates").mkdir()
    (root / "templates/job.yml").write_text("steps:\n  - script: echo t\n")
    for name in ("a", "b"):
        (root / f"azure-pipelines-{name}.yml").write_text(
            "trigger: none\nstages:\n  - stage: S\n    jobs:\n      - job: J\n"
            "        steps:\n          - template: /templates/job.yml\n"
        )
    return root


def test_bad_files_are_skipped_and_reported(tmp_path):
    (tmp_path / "ok.yml").write_text(PIPE)
    (tmp_path / "multi.yml").write_text("a: 1\n---\nb: 2\n")
    (tmp_path / "list.yml").write_text("- a\n- b\n")
    (tmp_path / "bad.yml").write_text("a: [unclosed\n")
    skipped = []
    graph = build_repository_graph([tmp_path], skipped=skipped)
    assert sorted(p.name for p, _ in skipped) == ["bad.yml", "list.yml", "multi.yml"]
    assert len(graph.nodes) > 1
    with pytest.raises(ValueError):
        build_repository_graph([tmp_path], strict=True)


def test_github_actions_file_is_not_a_pipeline(tmp_path):
    (tmp_path / "ok.yml").write_text(PIPE)
    (tmp_path / "x.yml").write_text("on: push\njobs:\n  b:\n    runs-on: x\n    steps:\n      - run: hi\n")
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/x.yml").write_text("on: push\njobs: {}\n")
    graph = build_repository_graph([tmp_path])
    assert sum(1 for k in graph.kinds.values() if k == "pipeline") == 1


def test_exclude_globs(tmp_path):
    (tmp_path / "keep.yml").write_text(PIPE)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub/drop.yml").write_text(PIPE)
    assert [p.name for p in yaml_files([tmp_path], ["sub/*"])] == ["keep.yml"]


def test_root_relative_template_shared_and_output_location_independent(tmp_path):
    outputs = []
    for name in ("one", "two"):
        graph = build_repository_graph([_tree(tmp_path / name / "repo")])
        outputs.append(graph.to_mermaid())
        labels = [label for label, _ in graph.nodes.values()]
        assert labels.count("Template: templates/job.yml") == 1
        assert graph.kinds["repository"] == "repository"
        assert "template" in graph.kinds.values() and "stage" in graph.kinds.values()
        assert graph.parents
        assert graph.templates == {"templates/job.yml"}
    assert outputs[0] == outputs[1]
    assert str(tmp_path) not in outputs[0]


def test_repeated_runs_collapse_to_one_counted_edge(tmp_path):
    root = _tree(tmp_path / "r")
    a, b = (root / "azure-pipelines-a.yml").resolve(), (root / "azure-pipelines-b.yml").resolve()
    runs = [{"triggeredByBuild": {"definition": {"id": 1}}}] * 3 + [{}]
    graph = build_repository_graph([root], rest_runs={2: runs}, definition_ids={str(a): 1, str(b): 2})
    assert [lbl for _, _, lbl in graph.edges if lbl.startswith("triggered")] == ["triggered (3 of 4 runs)"]


def test_yaml_files_file_root_and_non_yaml_file_root(tmp_path):
    (tmp_path / "a.yaml").write_text(PIPE)
    (tmp_path / "notes.txt").write_text("x")
    assert yaml_files([tmp_path / "a.yaml"]) == [(tmp_path / "a.yaml").resolve()]
    with pytest.raises(ValueError, match="scan root"):
        yaml_files([tmp_path / "notes.txt"])


def test_yaml_files_missing_root(tmp_path):
    with pytest.raises(ValueError, match="scan root"):
        yaml_files([tmp_path / "missing"])


def test_yaml_files_skips_excluded_directories_and_non_yaml(tmp_path):
    (tmp_path / "keep.yml").write_text(PIPE)
    (tmp_path / "UPPER.YAML").write_text(PIPE)
    (tmp_path / "readme.md").write_text("x")
    for skipped in (".git", "node_modules", ".github", ".venv", "build", "ado-graph", "pkg/__pycache__"):
        (tmp_path / skipped).mkdir(parents=True)
        (tmp_path / skipped / "x.yml").write_text(PIPE)
    (tmp_path / "deep/er").mkdir(parents=True)
    (tmp_path / "deep/er/ok.yml").write_text(PIPE)
    found = yaml_files([tmp_path])
    assert sorted(p.relative_to(tmp_path.resolve()).as_posix() for p in found) == [
        "UPPER.YAML",
        "deep/er/ok.yml",
        "keep.yml",
    ]


def test_yaml_files_exclude_patterns_and_dedup_across_roots(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a/one.yml").write_text(PIPE)
    (tmp_path / "a/two.yml").write_text(PIPE)
    assert yaml_files([tmp_path, tmp_path / "a"]) == yaml_files([tmp_path])
    assert [p.name for p in yaml_files([tmp_path], ["a/two.yml"])] == ["one.yml"]
    assert yaml_files([tmp_path], ["*.yml"]) == []  # fnmatch's * crosses directories


def test_empty_and_unreadable_yaml_handling(tmp_path):
    (tmp_path / "azure-pipelines.yml").write_text(PIPE)
    (tmp_path / "empty.yml").write_text("")
    (tmp_path / "latin1.yml").write_bytes(b"a: \xe9\n")
    skipped = []
    build_repository_graph([tmp_path], skipped=skipped)
    assert [p.name for p, _ in skipped] == ["latin1.yml"]


def test_template_that_fails_to_expand_is_skipped_or_strict(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "azure-pipelines.yml").write_text(PIPE)
    (tmp_path / "t.yml").write_text("jobs: []\n")
    (tmp_path / "azure-pipelines-bad.yml").write_text("steps:\n  - template: t.yml\n")
    skipped = []
    graph = build_repository_graph([tmp_path], skipped=skipped)
    assert [p.name for p, _ in skipped] == ["azure-pipelines-bad.yml"]
    assert "does not define a steps list" in skipped[0][1]
    assert sum(1 for k in graph.kinds.values() if k == "pipeline") == 1
    with pytest.raises(ValueError, match="azure-pipelines-bad.yml"):
        build_repository_graph([tmp_path], strict=True)


def test_templates_are_not_pipelines_and_same_name_in_two_roots_is_one_node(tmp_path):
    for repo in ("r1", "r2"):
        (tmp_path / repo / ".git").mkdir(parents=True)
        (tmp_path / repo / "azure-pipelines.yml").write_text(PIPE)
    graph = build_repository_graph([tmp_path / "r1", tmp_path / "r2"])
    ids = list(graph.nodes)
    assert len(ids) == len(set(ids))
    assert sum(1 for k in graph.kinds.values() if k == "pipeline") == 2
