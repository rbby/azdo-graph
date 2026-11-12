from pathlib import Path

import pytest

from ado_pipeline_graph.core import (
    _slug,
    build_graph,
    find_repo_root,
    load_graph,
    parse_template_roots,
    resolve_template,
    write_graph,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_local_templates_dependencies_and_external_node():
    graph = load_graph(FIXTURES / "local/azure-pipelines.yml")
    labels = [label for label, _ in graph.nodes.values()]

    assert "Build" in labels
    assert "Compile" in labels
    # Templates no longer see the pipeline's parameters (#19); pipeline variables still apply.
    assert "Build ${{ parameters.configuration }}" in labels
    assert any(label.startswith("External template: remote.yml@missingRepo") for label in labels)
    assert any(label == "dependsOn" for _, _, label in graph.edges)
    assert "echo building ${{ parameters.configuration }}-done" in labels


def test_template_root_alias_resolves_local_checkout():
    roots = parse_template_roots([f"shared={FIXTURES / 'alias-root'}"])
    graph = load_graph(FIXTURES / "alias-pipeline.yml", roots)

    assert "FromAlias" in [label for label, _ in graph.nodes.values()]
    assert not any(label.startswith("External template:") for label, _ in graph.nodes.values())


def test_template_root_syntax_validation():
    for bad in ("missing-equals", "=path", "alias="):
        with pytest.raises(ValueError, match="ALIAS=PATH"):
            parse_template_roots([bad])


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
    assert "Variable group: Shared (ID 13)" in [label for label, _ in rest_graph.nodes.values()]


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
    assert "```mermaid" in md and "## Stages" in md
    assert main([str(FIXTURES / "local/azure-pipelines.yml"), "--out-dir", str(tmp_path / "ado-graph")]) == 0
    assert {p.suffix for p in (tmp_path / "ado-graph").iterdir()} == {".html", ".md", ".mmd"}


def _labels(graph):
    return [label for label, _ in graph.nodes.values()]


def _by_label(graph, label):
    return next(node_id for node_id, (text, _) in graph.nodes.items() if text == label)


def _write(root, files):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


JOB = "jobs:\n  - job: {name}\n    steps:\n      - script: echo {name}\n"


def test_root_relative_escape_and_aliased_root_relative_templates(tmp_path):
    repo, shared = tmp_path / "repo", tmp_path / "shared"
    (repo / ".git").mkdir(parents=True)
    _write(
        repo,
        {
            "templates/job.yml": JOB.format(name="RootRelative"),
            "pipelines/ci/azure-pipelines.yml": (
                "stages:\n  - stage: S\n    jobs:\n"
                "      - template: /templates/job.yml\n"
                "      - template: ../../../outside.yml\n"
                "      - template: /nested/inner.yml@shared\n"
            ),
        },
    )
    _write(tmp_path, {"outside.yml": JOB.format(name="Outside")})
    _write(shared, {"nested/inner.yml": "jobs:\n  - template: /job.yml\n", "job.yml": JOB.format(name="AliasRoot")})
    pipeline = repo / "pipelines/ci/azure-pipelines.yml"

    assert find_repo_root(pipeline) == repo.resolve()
    graph = load_graph(pipeline, {"shared": shared.resolve()})
    labels = _labels(graph)
    assert "RootRelative" in labels and "AliasRoot" in labels
    assert "Outside" not in labels
    assert "External template: ../../../outside.yml" in labels
    assert graph.templates == {"templates/job.yml", "nested/inner.yml@shared", "job.yml@shared"}

    # Escaping an alias root is refused too; the override moves the repo root.
    assert resolve_template("../outside.yml@shared", shared, {"shared": shared}, repo) is None
    assert resolve_template("/../outside.yml", repo, {}, repo) is None
    overridden = load_graph(pipeline, repo_root=repo / "pipelines")
    assert "External template: /templates/job.yml" in _labels(overridden)


def test_expression_blocks_render_condition_nodes_with_children():
    graph = load_graph(FIXTURES / "expressions/azure-pipelines.yml")
    labels = _labels(graph)
    assert not {"Step", "Job", "Stage"} & set(labels)
    cond = _by_label(graph, "if eq(parameters.runTests, true)")
    loop = _by_label(graph, "each env in parameters.environments")
    assert graph.kinds[cond] == "condition" and graph.kinds[loop] == "loop"
    assert graph.kinds[_by_label(graph, "else")] == "condition"
    assert graph.parents[_by_label(graph, "npm test")] == cond
    assert graph.parents[_by_label(graph, "deploy ${{ env }}")] == loop
    assert graph.parents[_by_label(graph, "echo from template")] == loop  # template under expression
    job_cond = _by_label(graph, "if eq(variables['Build.Reason'], 'PullRequest')")
    assert graph.parents[_by_label(graph, "PrOnly")] == job_cond
    assert graph.parents[_by_label(graph, "PerOs")] == _by_label(graph, "each os in parameters.platforms")
    assert graph.parents[_by_label(graph, "Optional")] == _by_label(graph, "if true")
    assert graph.templates == {"tests/fixtures/expressions/step.yml"}  # relative to the .git root


def test_pipeline_coverage():
    graph = load_graph(FIXTURES / "coverage/azure-pipelines.yml")
    labels = _labels(graph)
    for label in (
        "checkout: self",
        "Write-Host hi",
        "download: current",
        "downloadBuild: upstream",
        "getPackage: pkg",
        "publish: out",
        "reviewApp: review",
        "echo pwsh",
        "echo bash",
        "DotNetCoreCLI@2\ncondition: succeeded()",
        "echo deploying",
        "echo rollback",
        "Prod\nenvironment: production",
        "Compile\nmatrix: linux, windows",
        "Test\ncondition: succeeded()",
        "CI trigger: main, release/*",
        "PR trigger: none",
        "Schedule: Nightly",
        "Pipeline resource: Upstream CI (upstream)",
        "Repository resource: Shared/Tools (tools)",
    ):
        assert label in labels, label
    assert "Step" not in labels
    assert graph.parents[_by_label(graph, "echo deploying")] == _by_label(graph, "runOnce: deploy")
    assert graph.parents[_by_label(graph, "echo rollback")] == _by_label(graph, "runOnce: on.failure")
    ids = {label.split("\n")[0]: node_id for node_id, (label, _) in graph.nodes.items()}
    edges = set(graph.edges)
    assert (ids["Compile"], ids["Package"], "dependsOn") in edges
    assert (ids["Build"], ids["Test"], "then") in edges
    assert (ids["Test"], ids["Deploy"], "then") in edges
    assert not any(target == ids["Independent"] and label for _, target, label in edges)
    assert (ids["Pipeline resource: Upstream CI (upstream)"], "pipeline", "trigger") in edges
    assert graph.kinds[ids["Pipeline resource: Upstream CI (upstream)"]] == "resource"
    assert graph.kinds[ids["Schedule: Nightly"]] == "trigger"
    assert graph.kinds["pipeline"] == "pipeline"
    assert set(graph.kinds) == set(graph.nodes)


def test_template_scoping_variable_templates_and_groups():
    graph = load_graph(FIXTURES / "scoping/azure-pipelines.yml")
    labels = _labels(graph)
    # Own default overlaid by caller; undeclared parameter stays unresolved; template variable wins.
    assert "echo fromCaller ${{ parameters.leaked }} templateValue varsValue" in labels
    assert graph.variable_groups == {"FromVarsTemplate"}
    assert "Variable group: FromVarsTemplate" in labels

    extended = load_graph(FIXTURES / "scoping/extends-pipeline.yml")
    labels = _labels(extended)
    assert "FromTemplate" in labels and "Clobber" not in labels
    assert "CI trigger: none" in labels
    assert extended.variable_groups == {"FromExtender"}


def test_duplicate_edges_and_aggregated_run_history():
    graph = build_graph(
        {"steps": [{"script": "echo"}]},
        run_history=[{"triggeredByBuild": {"definition": {"id": 1, "name": "Up"}}}] * 12 + [{}] * 8,
    )
    graph.add_edge("pipeline", "x")
    graph.add_edge("pipeline", "x")
    assert graph.edges.count(("pipeline", "x", "")) == 1
    assert _labels(graph).count("Triggered by: Up") == 1
    assert [label for _, _, label in graph.edges if label.startswith("trigger")] == ["trigger (12 of 20 runs)"]


def test_output_independent_of_checkout_location(tmp_path):
    outputs = []
    for name in ("a", "b/deeper"):
        repo = tmp_path / name
        (repo / ".git").mkdir(parents=True)
        _write(
            repo,
            {
                "t/job.yml": JOB.format(name="J"),
                "ci/p.yml": "stages:\n  - stage: S\n    jobs:\n      - template: ../t/job.yml\n",
            },
        )
        graph = load_graph(repo / "ci/p.yml")
        assert graph.templates == {"t/job.yml"}
        outputs.append((graph.to_mermaid(), graph.to_markdown(), graph.to_html()))
    assert outputs[0] == outputs[1]
    assert str(tmp_path) not in "".join(outputs[0])


def test_recursive_template_is_rejected(tmp_path):
    (tmp_path / ".git").mkdir()
    _write(tmp_path, {"a.yml": "steps:\n  - template: a.yml\n"})
    with pytest.raises(ValueError, match="recursive"):
        load_graph(tmp_path / "a.yml")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Build", "Build"),
        ("1st stage", "n_1st_stage"),
        ("", "n_"),
        ("Déploy→prod", "D_ploy_prod"),
        ("日本語", "___"),
    ],
)
def test_slug(value, expected):
    slug = _slug(value)
    assert slug == expected
    assert slug.isascii() and slug.replace("_", "a").isalnum() and not slug[0].isdigit()


def test_node_ids_unique_for_identical_and_nonascii_names():
    pipeline = {
        "stages": [
            {"stage": "Same", "jobs": [{"job": "J", "steps": [{"script": "echo"}] * 3}]},
            {"stage": "Same", "jobs": [{"job": "J", "steps": [{"script": "echo"}]}]},
            {"stage": "日本語"},
            {"stage": "日本"},
            {"stage": "1"},
            {"stage": "n_1"},
        ]
    }
    graph = build_graph(pipeline)
    ids = list(graph.nodes)
    assert len(ids) == len(set(ids)) == 1 + 6 + 2 + 4
    for node_id in ids:
        assert node_id.isascii() and node_id.replace("_", "a").isalnum()
        assert not node_id[0].isdigit()
    assert "flowchart" in graph.to_mermaid()


@pytest.mark.parametrize(
    "context,body",
    [
        ("stages", "jobs:\n  - job: J\n"),
        ("jobs", "steps:\n  - script: x\n"),
        ("steps", "jobs: []\n"),
        ("steps", "steps: not-a-list\n"),
        ("jobs", ""),
    ],
)
def test_template_missing_expected_list(tmp_path, context, body):
    (tmp_path / ".git").mkdir()
    (tmp_path / "t.yml").write_text(body)
    if context == "stages":
        head = "stages:\n  - template: t.yml\n"
    elif context == "jobs":
        head = "stages:\n  - stage: S\n    jobs:\n      - template: t.yml\n"
    else:
        head = "steps:\n  - template: t.yml\n"
    (tmp_path / "p.yml").write_text(head)
    with pytest.raises(ValueError, match=f"t.yml does not define a {context} list"):
        load_graph(tmp_path / "p.yml")


def test_indirect_recursive_template_is_rejected(tmp_path):
    (tmp_path / ".git").mkdir()
    _write(tmp_path, {"a.yml": "steps:\n  - template: b.yml\n", "b.yml": "steps:\n  - template: a.yml\n"})
    with pytest.raises(ValueError, match="recursive"):
        load_graph(tmp_path / "a.yml")


def test_alias_traversal_is_not_resolved_in_a_pipeline(tmp_path):
    shared = tmp_path / "shared"
    _write(shared, {"ok.yml": JOB.format(name="Ok")})
    _write(
        tmp_path,
        {
            "secret.yml": JOB.format(name="Secret"),
            "p.yml": "jobs:\n  - template: ../secret.yml@shared\n  - template: ok.yml@shared\n",
        },
    )
    labels = _labels(load_graph(tmp_path / "p.yml", {"shared": shared.resolve()}))
    assert "Ok" in labels and "Secret" not in labels
    assert "External template: ../secret.yml@shared" in labels


def test_non_mapping_pipeline_root_is_rejected(tmp_path):
    (tmp_path / "p.yml").write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="mapping"):
        load_graph(tmp_path / "p.yml")


def test_write_graph_output_mode_mermaid_and_html(tmp_path):
    graph = load_graph(FIXTURES / "local/azure-pipelines.yml")
    out = tmp_path / "nested.mmd"
    write_graph(graph, out)
    assert out.read_text(encoding="utf-8") == graph.to_mermaid()
    page = tmp_path / "view.html"
    write_graph(graph, page, True, direction="LR")
    text = page.read_text(encoding="utf-8")
    assert text.lstrip().lower().startswith("<!doctype html")
    assert "flowchart LR" in text and "Compile" in text


def test_invalid_detail_and_direction_rejected():
    graph = build_graph({"steps": [{"script": "x"}]})
    with pytest.raises(ValueError, match="detail"):
        graph.to_mermaid("bogus")
    with pytest.raises(ValueError, match="direction"):
        graph.to_mermaid("steps", "UP")
