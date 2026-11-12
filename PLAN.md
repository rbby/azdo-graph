# Azure DevOps Pipeline Graph CLI — Implementation Plan

## Problem and proposed approach

This repository is a greenfield implementation. Build a single-purpose Python CLI that converts Azure Pipelines YAML into Mermaid while adding explicit template-root mappings, limited label substitution, Azure DevOps Services data access, and repository-wide relationship graphs. Keep parsing/rendering independent from data-source and CLI concerns so local mode remains the default and REST functionality can be tested without network access.

## Todos

1. Establish the Python package, CLI entry point, local YAML parsing/rendering baseline, and pytest fixtures.
2. Add explicit cross-repository template roots and simple parameter/variable substitution in labels.
3. Package and expose the CLI as an installable console command.
4. Add Azure DevOps Services REST data access for pipeline YAML, run history, and variable groups using environment-sourced credentials.
5. Add repository/multi-repository scanning and combined relationship graph output, using REST run data for observed pipeline-trigger relationships.
6. Run focused tests and end-to-end CLI checks for the local and REST-backed paths.

## Phases and dependencies

### Phase 1 — Greenfield local CLI and regression tests
- Create the package/module structure and retain `ado_pipeline_graph.py` as a compatibility wrapper.
- Implement local YAML loading, recursive `template`/`extends` resolution, stage/job/step traversal, stage `dependsOn` edges, unresolved external-template nodes, Mermaid output, and HTML viewer output.
- Add representative YAML fixtures covering dependencies, local step/job templates, and unresolved external templates; test parsing, IDs/edges, and rendering.

### Phase 2 — Local resolution and readable labels
- Add repeatable `--template-root <alias>=<path>` mappings for templates whose repository alias is checked out locally.
- Substitute only simple string-literal `parameters.x` and `variables.x` references in labels; leave unsupported or unresolved expressions unchanged.

### Phase 3 — Reusable CLI packaging
- Add `pyproject.toml`, declare dependencies, and configure a console-script entry point, with installable `pipx`/`uv tool` usage.

### Phase 4 — Azure DevOps Services REST mode
- Add opt-in REST mode while retaining local-file parsing as the default.
- Read organization URL and PAT exclusively from `ADO_ORG_URL` and `ADO_PAT`; never accept the PAT as a command-line argument or persist it.
- Retrieve pipeline YAML, run history, and variable-group data through Azure DevOps Services REST APIs. Test the API client with mocked responses.
- Report authentication, permission, missing-resource, and API errors clearly; never silently fall back to local input when REST mode was explicitly selected.

### Phase 5 — Combined repository graph
- Scan one or more local roots for YAML files, distinguish pipeline definitions from reusable templates, and build one graph.
- Represent pipelines, templates, and variable groups as shared graph entities/relationships.
- When REST data is available, add observed pipeline-trigger relationships from run history; do not imply local static scanning establishes runtime trigger history.

### Phase 6 — Verification and user-facing behavior
- Run focused tests and verify CLI usage for local Mermaid/HTML output and mocked REST mode.
- Confirm local mode requires no credentials or network access.

Dependencies: phases 2–4 build on phase 1; phase 5 depends on phases 1, 2, and 4; final verification follows all implementation phases.

## Assumptions and considerations

- Target Azure DevOps Services only, not Azure DevOps Server.
- Use `ADO_ORG_URL` and `ADO_PAT` environment variables for REST configuration.
- REST API version, project, and pipeline identifiers should be explicit configuration/CLI inputs where needed.
- Static scanning can identify declared YAML references such as templates, variable groups, and pipeline resources; runtime trigger relationships should be based on REST run history where available.
- Do not fully evaluate Azure Pipelines expressions or execute pipeline YAML.
