---
name: ado-pipeline-graph
description: Render an Azure DevOps pipeline YAML as a Mermaid diagram (HTML) plus a Markdown description in ./ado-graph. Use when the user asks to visualize, graph, map or document an Azure Pipelines YAML or a repo's pipelines.
---

Run from the repo containing the pipeline:

```sh
ado-pipeline-graph azure-pipelines.yml --out-dir ado-graph          # single pipeline
ado-pipeline-graph --scan-root . --out-dir ado-graph                # all pipelines in repo
```

Add `--template-root ALIAS=PATH` for cross-repo templates. Outputs `ado-graph/<name>.html` (open in a browser; needs internet for Mermaid CDN), `.md` (description + diagram), `.mmd`.

REST mode (optional): set `ADO_ORG_URL` and `ADO_PAT` in the environment, then add `--rest --project P --pipeline-id N`. Never pass the PAT as an argument.

If `ado-pipeline-graph` is not found, run `install.sh` from the tool's repo.
