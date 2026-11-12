# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Python CLI (`ado-pipeline-graph`) that renders Azure DevOps Services pipeline YAML as Mermaid (or a standalone HTML viewer). Pipeline expressions are never evaluated or executed. See `PLAN.md` for the phased design and assumptions (Azure DevOps Services only, not Server).

## Commands

```sh
python -m pip install -e '.[test]'   # dev install (only runtime dep: PyYAML)
pytest                               # all tests (testpaths = tests/)
pytest tests/test_core.py::test_name # single test
python -m ado_pipeline_graph azure-pipelines.yml   # run without installing
```

No linter or formatter is configured.

## Architecture

- `core.py` — the parsing/rendering engine. `build_graph` / `load_graph` walk stages → jobs → steps, recursively expand `template`/`extends` references (`_expand`, `_resolve_template`), and emit a `Graph` (nodes, edges, `templates`, `variable_groups`) with `to_mermaid()`. Label substitution only handles simple string-literal `parameters.x` / `variables.x`; anything else is left unchanged. Unmapped cross-repo templates (`name@alias`) become dashed external nodes; `--template-root ALIAS=PATH` maps an alias to a local checkout.
- `scan.py` — `build_repository_graph` scans roots for YAML, classifies pipelines vs. templates (files referenced by others are templates; otherwise name/keys heuristics in `_is_pipeline`), runs `build_graph` per pipeline with a per-pipeline node-ID prefix, and merges into one graph with shared template / variable-group nodes. Optional REST run history adds "triggered" edges between pipelines.
- `ado.py` — `AdoClient` for REST (pipeline YAML, build definitions, runs, variable groups). Credentials come only from `ADO_ORG_URL` / `ADO_PAT` env vars; the PAT must never become a CLI option or be persisted. Errors surface as `AdoApiError`.
- `cli.py` — argparse wiring. Local mode is the default and needs no network/credentials; `--rest` is opt-in and must never silently fall back to local input. `_scan_with_rest` maps ADO build definitions to local YAML files by path suffix (falling back to filename, requiring a unique match). Expected errors (`AdoApiError`, `OSError`, `ValueError`, `YAMLError`) print `ado-pipeline-graph: error: …` and exit 2.
- `ado_pipeline_graph.py` (repo root) is a thin compatibility wrapper; put logic in the package.

Tests (`tests/`) use YAML fixtures in `tests/fixtures/`; REST code is tested with mocked responses, never the network.
