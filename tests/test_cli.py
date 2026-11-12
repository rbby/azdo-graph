from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from ado_pipeline_graph import cli
from ado_pipeline_graph.ado import AdoApiError
from ado_pipeline_graph.cli import main

FIXTURE = str(Path(__file__).parent / "fixtures/local/azure-pipelines.yml")
PIPE = "trigger: none\nstages:\n- stage: S\n  jobs:\n  - job: J\n    steps:\n    - script: echo hi\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["a.yml", "--scan-root", "."],
        ["a.yml", "--rest", "--project", "p", "--pipeline-id", "1"],
        ["-o", "x", "--out-dir", "y"],
        ["--html"],
        ["--project", "p"],
        ["--pipeline-id", "1"],
        ["--branch", "b"],
        ["--runs", "3"],
        ["--api-version", "7.1"],
        ["--timeout", "5"],
        ["--no-expand"],
        ["--exclude", "x"],
        ["--strict"],
        ["--rest", "--project", "p", "--pipeline-id", "1", "--runs", "0"],
        ["--rest", "--project", "p", "--pipeline-id", "0"],
        ["--rest"],
        ["--rest", "--project", "p"],
        ["--out-dir"],
    ],
)
def test_usage_errors(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_out_dir_value_is_required_not_pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no azure-pipelines.yml here: the explicit positional must still be read
    assert main([FIXTURE, "--out-dir", "o"]) == 0
    assert (tmp_path / "o").is_dir()


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("ado-pipeline-graph ")


def test_stdout_mermaid(capsys):
    assert main([FIXTURE]) == 0
    assert capsys.readouterr().out.startswith("flowchart")


def test_out_dir_names_do_not_collide(tmp_path, capsys, monkeypatch):
    for d in "ab":
        (tmp_path / d).mkdir()
        (tmp_path / d / "azure-pipelines.yml").write_text(PIPE)
    (tmp_path / ".git").mkdir()
    (tmp_path / "azure-pipelines.yml").write_text(PIPE)
    out = tmp_path / "out"
    for p in ("a/azure-pipelines.yml", "b/azure-pipelines.yml", "azure-pipelines.yml"):
        assert main([str(tmp_path / p), "--out-dir", str(out)]) == 0
    names = sorted(f.name for f in out.iterdir())
    assert len(names) == 9
    assert "azure-pipelines.html" in names and "a-azure-pipelines.mmd" in names


def test_detail_direction_mermaid_js(tmp_path):
    js = tmp_path / "m.js"
    js.write_text("/*MERMAID_MARK é*/", encoding="utf-8")
    out = tmp_path / "o.html"
    assert (
        main([FIXTURE, "-o", str(out), "--html", "--detail", "stages", "--direction", "LR", "--mermaid-js", str(js)])
        == 0
    )
    text = out.read_text(encoding="utf-8")
    assert "MERMAID_MARK" in text and "flowchart LR" in text
    mmd = tmp_path / "o.mmd"
    main([FIXTURE, "-o", str(mmd), "--detail", "stages"])
    main([FIXTURE, "-o", str(tmp_path / "full.mmd")])
    assert len(mmd.read_text()) < len((tmp_path / "full.mmd").read_text())


def test_scan_skips_broken_yaml(tmp_path, capsys):
    (tmp_path / "azure-pipelines.yml").write_text(PIPE)
    (tmp_path / "bad.yml").write_text("a: [unclosed\n")
    assert main(["--scan-root", str(tmp_path)]) == 0
    cap = capsys.readouterr()
    assert "skipped 1 file" in cap.err and "bad.yml" in cap.err
    assert "bad.yml" not in cap.out
    assert main(["--scan-root", str(tmp_path), "--strict"]) != 0
    assert main(["--scan-root", str(tmp_path), "--exclude", "bad.yml"]) == 0
    assert "skipped" not in capsys.readouterr().err


class FakeClient:
    instances: list = []

    def __init__(self, **kw):
        self.kw = kw
        self.calls = []
        self.preview_error = None
        FakeClient.instances.append(self)

    def get_expanded_yaml(self, project, pid, branch=None):
        self.calls.append("expanded")
        if self.preview_error:
            raise AdoApiError(self.preview_error)
        return PIPE

    def get_pipeline_yaml(self, project, pid, branch=None):
        self.calls.append("raw")
        return PIPE

    def get_runs(self, project, pid, top=20):
        return []

    def get_variable_groups(self, project):
        return []


@pytest.fixture
def fake(monkeypatch):
    FakeClient.instances = []
    monkeypatch.setattr(cli, "AdoClient", FakeClient)
    return FakeClient


def test_rest_expanded_default_and_no_expand(fake, capsys):
    base = ["--rest", "--project", "p", "--pipeline-id", "3", "--timeout", "9"]
    assert main(base) == 0
    assert fake.instances[-1].calls == ["expanded"] and fake.instances[-1].kw["timeout"] == 9
    assert "flowchart" in capsys.readouterr().out
    assert main(base + ["--no-expand"]) == 0
    assert fake.instances[-1].calls == ["raw"]


def test_rest_preview_denied_hint(fake, monkeypatch, capsys):
    monkeypatch.setattr(
        fake,
        "get_expanded_yaml",
        lambda *a, **k: (_ for _ in ()).throw(AdoApiError("request failed (403): access denied")),
    )
    assert main(["--rest", "--project", "p", "--pipeline-id", "3"]) == 2
    assert "--no-expand" in capsys.readouterr().err


class DefsClient(FakeClient):
    def __init__(self, defs):
        super().__init__()
        self.defs = defs

    def get_build_definitions(self, project):
        return self.defs


def _defs(*items):
    return [
        {"id": i, "process": {"yamlFilename": f}, **({"repository": {"name": r}} if r else {})} for i, f, r in items
    ]


def _scan(tmp_path, defs, roots, capsys):
    args = argparse.Namespace(project="p", runs=5)
    client = DefsClient(defs)
    graph = cli._scan_with_rest(args, client, roots, {})
    return graph, capsys.readouterr().err


def test_scan_with_rest_matching(tmp_path, capsys):
    for p in ("ci.yml", "other-ci.yml", "sub/azure-pipelines.yml"):
        (tmp_path / p).parent.mkdir(exist_ok=True)
        (tmp_path / p).write_text(PIPE)
    defs = _defs(
        (1, "/ci.yml", None), (2, "\\sub\\azure-pipelines.yml", None), (3, "ine.yml", None), (4, "missing.yml", None)
    )
    graph, err = _scan(tmp_path, defs, [tmp_path], capsys)
    assert "4 definitions found, 2 matched, 0 ambiguous, 2 unmatched" in err


def test_scan_with_rest_repo_name_disambiguation(tmp_path, capsys):
    for r in ("repoA", "repoB"):
        (tmp_path / r).mkdir()
        (tmp_path / r / "azure-pipelines.yml").write_text(PIPE)
    roots = [tmp_path / "repoA", tmp_path / "repoB"]
    _, err = _scan(tmp_path, _defs((1, "azure-pipelines.yml", "repoB")), roots, capsys)
    assert "1 matched, 0 ambiguous, 0 unmatched" in err
    _, err = _scan(tmp_path, _defs((1, "azure-pipelines.yml", None)), roots, capsys)
    assert "0 matched, 1 ambiguous, 0 unmatched" in err


def test_install_skill(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_SKILLS_DIR", str(tmp_path / "skills"))
    assert main(["--install-skill", "user"]) == 0
    dest = tmp_path / "skills/ado-pipeline-graph/SKILL.md"
    assert dest.read_text(encoding="utf-8").startswith("---\nname: ado-pipeline-graph")
    assert str(dest) in capsys.readouterr().out
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    assert main(["--install-skill", "project"]) == 0
    assert (proj / ".claude/skills/ado-pipeline-graph/SKILL.md").read_text(encoding="utf-8").startswith("---\n")


def _err(capsys):
    return capsys.readouterr().err


def test_missing_pipeline_file_is_exit_2(tmp_path, capsys):
    assert main([str(tmp_path / "nope.yml")]) == 2
    err = _err(capsys)
    assert err.startswith("ado-pipeline-graph: error:") and "nope.yml" in err


def test_default_pipeline_name_is_read_from_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([]) == 2  # no azure-pipelines.yml here
    (tmp_path / "azure-pipelines.yml").write_text(PIPE)
    assert main([]) == 0
    assert capsys.readouterr().out.startswith("flowchart")


@pytest.mark.parametrize("text", ["a: [unclosed\n", "- a\n- b\n", "a: 1\n---\nb: 2\n"])
def test_invalid_or_non_mapping_yaml_is_exit_2(tmp_path, capsys, text):
    bad = tmp_path / "bad.yml"
    bad.write_text(text)
    assert main([str(bad)]) == 2
    assert _err(capsys).startswith("ado-pipeline-graph: error:")


def test_invalid_template_root_is_exit_2(capsys):
    assert main([FIXTURE, "--template-root", "no-equals"]) == 2
    assert "ALIAS=PATH" in _err(capsys)


def test_missing_mermaid_js_is_exit_2(tmp_path, capsys):
    assert main([FIXTURE, "-o", str(tmp_path / "o.html"), "--html", "--mermaid-js", str(tmp_path / "absent.js")]) == 2
    assert "absent.js" in _err(capsys)
    assert not (tmp_path / "o.html").exists()


def test_unwritable_output_is_exit_2(tmp_path, capsys):
    assert main([FIXTURE, "-o", str(tmp_path)]) == 2  # a directory
    assert _err(capsys).startswith("ado-pipeline-graph: error:")


def test_scan_root_missing_is_exit_2(tmp_path, capsys):
    assert main(["--scan-root", str(tmp_path / "missing")]) == 2
    assert "scan root" in _err(capsys)


def test_scan_root_stdout_and_file_root(tmp_path, capsys):
    pipe = tmp_path / "azure-pipelines.yml"
    pipe.write_text(PIPE)
    assert main(["--scan-root", str(pipe)]) == 0
    assert capsys.readouterr().out.startswith("flowchart")


def test_scan_out_dir_uses_repository_name(tmp_path, capsys):
    (tmp_path / "azure-pipelines.yml").write_text(PIPE)
    out = tmp_path / "out"
    assert main(["--scan-root", str(tmp_path), "--out-dir", str(out)]) == 0
    assert sorted(f.name for f in out.iterdir()) == ["repository.html", "repository.md", "repository.mmd"]
    printed = capsys.readouterr().out.split()
    assert sorted(Path(p).name for p in printed) == ["repository.html", "repository.md", "repository.mmd"]


def test_rest_out_dir_names_file_after_pipeline_id(fake, tmp_path):
    out = tmp_path / "out"
    assert main(["--rest", "--project", "p", "--pipeline-id", "7", "--out-dir", str(out)]) == 0
    assert (out / "pipeline-7.mmd").is_file()


def test_rest_missing_credentials_is_exit_2(monkeypatch, capsys):
    monkeypatch.delenv("ADO_ORG_URL", raising=False)
    monkeypatch.delenv("ADO_PAT", raising=False)
    assert main(["--rest", "--project", "p", "--pipeline-id", "3"]) == 2
    assert "ADO_ORG_URL" in _err(capsys)


def test_rest_passes_branch_runs_and_api_version(fake, monkeypatch):
    seen = {}

    def expanded(self, project, pid, branch=None):
        seen["expanded"] = (project, pid, branch)
        return PIPE

    def runs(self, project, pid, top=20):
        seen["runs"] = top
        return []

    monkeypatch.setattr(fake, "get_expanded_yaml", expanded)
    monkeypatch.setattr(fake, "get_runs", runs)
    assert (
        main(
            ["--rest", "--project", "p", "--pipeline-id", "3", "--branch", "dev", "--runs", "4", "--api-version", "7.2"]
        )
        == 0
    )
    assert seen == {"expanded": ("p", 3, "dev"), "runs": 4}
    assert fake.instances[-1].kw["api_version"] == "7.2"
    assert main(["--rest", "--project", "p", "--pipeline-id", "3"]) == 0
    assert seen["runs"] == 20 and fake.instances[-1].kw["api_version"] == "7.1"


@pytest.mark.parametrize("text", ["- a\n", "a: [x\n"])
def test_rest_bad_yaml_is_exit_2(fake, monkeypatch, capsys, text):
    monkeypatch.setattr(fake, "get_expanded_yaml", lambda *a, **k: text)
    assert main(["--rest", "--project", "p", "--pipeline-id", "3"]) == 2
    assert _err(capsys).startswith("ado-pipeline-graph: error:")


def test_rest_empty_yaml_renders_empty_graph(fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "get_expanded_yaml", lambda *a, **k: "")
    assert main(["--rest", "--project", "p", "--pipeline-id", "3"]) == 0
    assert capsys.readouterr().out.startswith("flowchart")


def test_rest_api_error_without_denial_has_no_hint(fake, monkeypatch, capsys):
    monkeypatch.setattr(
        fake, "get_expanded_yaml", lambda *a, **k: (_ for _ in ()).throw(AdoApiError("request failed (404): not found"))
    )
    assert main(["--rest", "--project", "p", "--pipeline-id", "3"]) == 2
    err = _err(capsys)
    assert "(404)" in err and "--no-expand" not in err


def test_rest_scan_mode(tmp_path, monkeypatch, capsys):
    (tmp_path / "azure-pipelines.yml").write_text(PIPE)
    DefsClient.instances = []
    monkeypatch.setattr(cli, "AdoClient", lambda **kw: DefsClient(_defs((5, "/azure-pipelines.yml", None))))
    assert main(["--rest", "--project", "p", "--scan-root", str(tmp_path)]) == 0
    cap = capsys.readouterr()
    assert "1 definitions found, 1 matched" in cap.err
    assert cap.out.startswith("flowchart")


def test_install_skill_failure_is_exit_2(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("CLAUDE_SKILLS_DIR", str(blocker))  # a file, so mkdir must fail
    assert main(["--install-skill", "user"]) == 2
    assert _err(capsys).startswith("ado-pipeline-graph: error:")


def test_python_dash_m_entry_point():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "ado_pipeline_graph", "--version"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0 and result.stdout.startswith("ado-pipeline-graph ")


def test_scan_with_rest_ignores_malformed_definitions(tmp_path, capsys):
    (tmp_path / "ci.yml").write_text(PIPE)
    defs = [
        {"id": "x", "process": {"yamlFilename": "ci.yml"}},
        {"id": 1},
        {"id": 2, "process": []},
        {"id": 3, "process": {"yamlFilename": 5}},
    ]
    _, err = _scan(tmp_path, defs, [tmp_path], capsys)
    assert "4 definitions found, 0 matched, 0 ambiguous, 4 unmatched" in err
