---
name: ado-pipeline-graph
description: Render an Azure DevOps pipeline YAML (or all pipelines in a repo) as a Mermaid diagram in HTML plus a Markdown description in ./ado-graph. Use when the user asks to visualize, graph, map or document Azure Pipelines YAML.
---

## 1. Make sure the CLI is available

Run `ado-pipeline-graph --version`. If the command is not found, either run every command below through
`uvx --from git+https://github.com/rbby/azdo-graph ado-pipeline-graph ...` (no install needed), or install it once with
`pipx install git+https://github.com/rbby/azdo-graph` or `uv tool install git+https://github.com/rbby/azdo-graph`.
If none of `uvx`, `pipx`, `uv` exist, tell the user to install one of them (or Python 3.10+ with pip) and stop.

## 2. Generate the graph (run from the repository root)

```sh
ado-pipeline-graph azure-pipelines.yml --out-dir ado-graph     # one pipeline
ado-pipeline-graph --scan-root . --out-dir ado-graph           # every pipeline in the repo
```

Useful options:
- `--template-root ALIAS=PATH` maps a cross-repo template alias (`template: x.yml@alias`) to a local checkout.
- `--detail stages|jobs|steps` and `--direction TD|LR` control size and layout (use `--detail stages` for big graphs).
- `--exclude GLOB` (repeatable) and `--strict` apply to `--scan-root`; skipped files are listed on stderr.
- The HTML loads Mermaid from a CDN. For offline viewing, download `mermaid.min.js` and pass `--mermaid-js PATH`.

## 3. Report the result

Tell the user the paths printed by the command (`ado-graph/<name>.html`, `.md`, `.mmd`), mention any skipped files
reported on stderr, and offer to open the HTML in a browser.

## REST mode (optional)

Only when the user wants pipelines from Azure DevOps itself: they must set `ADO_ORG_URL` and `ADO_PAT` in the
environment, then add `--rest --project P --pipeline-id N` (or `--rest --project P --scan-root .` for run history edges).
Templates are expanded by the service (needs a PAT with build scope); add `--no-expand` if that is not permitted.
Never pass the PAT as a command-line argument and never print it.
