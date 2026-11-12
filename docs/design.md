# Design

## Goals

- Turn Azure Pipelines YAML into a Mermaid flowchart, a standalone HTML viewer and a Markdown description.
- Show cross-repository templates through explicit, local `--template-root` mappings.
- Show shared templates, variable groups and pipeline relationships across a repository through `--scan-root`.
- Read pipeline YAML, run history and variable groups from Azure DevOps Services through an opt-in REST mode, using credentials from environment variables.

## Non-goals

- Evaluating or executing pipeline expressions. Conditions and loops are shown as written.
- Azure DevOps Server. Only Azure DevOps Services is targeted.
- Queuing builds. The REST preview call is a dry run.

## Assumptions

- Input is Azure Pipelines YAML, read as files on disk or fetched through the Azure DevOps Services REST API.
- A repository is identified by its `.git` entry. Template paths are resolved relative to it.
- Static scanning finds declared references: templates, variable groups and pipeline resources. Runtime trigger relationships come only from REST run history.

## Key decisions

- **Local mode is the default and is offline.** It needs no credentials and performs no network access. REST is enabled only by `--rest`.
- **Parsing and rendering are separate from data sources and the CLI.** `core.py` builds and renders a `Graph` without I/O beyond reading files. `ado.py` is the only module that talks to the network. `cli.py` wires them together.
- **Credentials come from the environment only.** `ADO_ORG_URL` must use HTTPS. `ADO_PAT` is never a command-line argument. The client drops the `Authorization` header on a redirect to another host.
- **REST failures are errors.** When REST is selected, an API failure exits with status 2. Nothing falls back to local input. The only extra step is a hint when the expanded-YAML preview is denied.
- **Template expansion is limited.** Only literal `parameters` and `variables` are substituted. Templates resolve only to files inside the repository root, or inside a `--template-root` checkout. Other references stay as unresolved nodes.
- **Scans separate pipelines from templates.** A file referenced as a template by another scanned file is not treated as a pipeline. Files that are not valid pipelines are reported on stderr, or raised with `--strict`.
- **Output is deterministic.** Output contains no absolute paths, so the same pipeline gives the same diagram from any checkout location. Golden files in `tests/golden/` lock the Mermaid and Markdown output.
- **The diagram is a view of the YAML.** Node kinds and edge labels describe what is declared (`dependsOn`, implicit `then` order, `uses`, `trigger`). Observed run history is shown separately, with counts.
- **The HTML page loads Mermaid at runtime.** It loads a pinned version from a CDN by default. `--mermaid-js` inlines a local copy for offline use.
- **The Claude Code skill ships inside the package.** `--install-skill` copies it into `~/.claude/skills` or `./.claude/skills`.

## Open items

- Licence: MIT.
- REST behaviour has not been checked against a live Azure DevOps organisation.
