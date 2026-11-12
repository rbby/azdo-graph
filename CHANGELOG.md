# Changelog

All notable changes to this project are documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `ado-pipeline-graph` command. It renders one pipeline YAML file as Mermaid source, to stdout or to a file with `-o`.
- Standalone HTML viewer with `-o FILE --html`, with zoom controls and the Mermaid source. `--mermaid-js` inlines a local Mermaid file for offline use.
- `--out-dir DIR`, which writes `.html`, `.md` and `.mmd` files.
- Markdown description of a pipeline, with overview, triggers, stages, templates and variable groups, plus the diagram.
- `--detail {stages,jobs,steps}` and `--direction {TD,LR}`.
- Template resolution: `--template-root ALIAS=PATH` for aliased templates, and root-relative and file-relative local templates. Resolution is limited to the repository root or the alias checkout.
- `extends` and included templates, with parameter and variable scoping. Literal `${{ parameters.x }}` and `${{ variables.x }}` values are substituted.
- Diagram nodes for `${{ if }}` / `${{ elseif }}` / `${{ else }}` blocks, `${{ each }}` blocks, deployment strategy hooks, triggers, resources, templates and variable groups.
- Repository scan with `--scan-root` (repeatable), `--exclude GLOB` and `--strict`. The scan reports unusable files on stderr.
- REST mode with `--rest`, reading `ADO_ORG_URL` and `ADO_PAT` from the environment. Single-pipeline mode reads the service-expanded YAML, or the raw YAML with `--no-expand`. It also reads run history and variable groups. Options: `--project`, `--pipeline-id`, `--branch`, `--runs`, `--api-version`, `--timeout`.
- REST repository scan. It matches pipeline definitions to local files and adds observed run-history edges.
- `--install-skill user|project`, which installs the bundled Claude Code skill.
- GitHub Actions CI: Ruff lint and format check, mypy, pytest with coverage on Python 3.10 to 3.13 on Ubuntu and Windows, and a package build with `twine check`.
- Dependabot configuration and a pre-commit configuration.
- README, `CONTRIBUTING.md`, `SECURITY.md`, `docs/design.md`, and issue and pull request templates.

### Changed

- Documentation rewritten to match the code: `README.md`, `CLAUDE.md`, and the new `docs/design.md`.
- The package includes a `py.typed` marker.

### Removed

- `install.sh`. Use `ado-pipeline-graph --install-skill` instead.
- `skills/ado-pipeline-graph/SKILL.md`. The skill is now packaged at `ado_pipeline_graph/skill/SKILL.md`.
- The top-level `ado_pipeline_graph.py` compatibility file.
- `PLAN.md`. Its still-valid content is in `docs/design.md`.
