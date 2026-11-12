# ado-pipeline-graph

Render Azure DevOps Services pipeline YAML as Mermaid diagrams. Local files are the default source; REST access is opt-in. The tool does not evaluate or execute pipeline expressions.

## Install

```sh
pipx install .
# or
uv tool install .
```

For development, use `python -m pip install -e '.[test]'`.

## Usage

```sh
ado-pipeline-graph azure-pipelines.yml -o pipeline.mmd
ado-pipeline-graph azure-pipelines.yml --template-root sharedTemplates=../shared --html pipeline.html
ado-pipeline-graph --scan-root ./repo-a --scan-root ./repo-b -o all-pipelines.mmd
```

`--template-root` may be repeated. A mapped alias resolves templates from that checkout; an unmapped cross-repository template remains a dashed external node.

For Azure DevOps Services REST mode, set `ADO_ORG_URL` and `ADO_PAT` in the environment. The PAT is never accepted as a command-line option:

```sh
export ADO_ORG_URL=https://dev.azure.com/my-organization
export ADO_PAT=...
ado-pipeline-graph --rest --project MyProject --pipeline-id 42 --runs 20 -o pipeline.mmd
```

REST mode requires an Azure DevOps project and pipeline ID. `--scan-root` builds a combined static graph for local YAML files; shared templates and variable groups are shown as shared entities. Runtime trigger relationships can be added from REST run history where available.

Use the standard `pytest` command to run tests.

## Output folder and Claude Code skill

`ado-pipeline-graph azure-pipelines.yml --out-dir` writes `ado-graph/<name>.html` (Mermaid viewer, loads Mermaid from a CDN so needs internet), `.md` (description + diagram) and `.mmd`.

`./install.sh` installs the CLI (pipx or uv) and copies `skills/ado-pipeline-graph/SKILL.md` to `~/.claude/skills/`.
