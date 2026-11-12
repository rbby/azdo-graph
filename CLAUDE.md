# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
python -m pip install -e '.[dev]'                       # install with dev tools
pytest                                                   # all tests
pytest tests/test_core.py::test_pipeline_coverage        # one test
ruff check . && ruff format --check .                    # lint and format check
mypy ado_pipeline_graph                                  # type check
UPDATE_GOLDEN=1 pytest tests/test_render.py              # regenerate tests/golden/*
```

## Architecture

- `ado_pipeline_graph/cli.py`: argument parser (`_parser`), option validation (`_validate`), mode dispatch in `main`, REST and scan orchestration (`_scan_with_rest`, `_fetch_rest_pipeline`), `--install-skill`. Exit 0 on success, 2 on error, errors on stderr.
- `ado_pipeline_graph/core.py`: the `Graph` model and its renderers (`to_mermaid`, `to_markdown`, `to_html`); `build_graph`, which expands templates and builds nodes; template resolution (`resolve_template`, `find_repo_root`); file writers (`write_graph`, `write_outputs`).
- `ado_pipeline_graph/scan.py`: directory walk and excludes (`_walk`, `SKIP_DIRS`), pipeline detection (`_is_pipeline`), `build_repository_graph`, which merges pipelines into one graph with prefixed node IDs and adds shared template and variable-group nodes and REST run edges.
- `ado_pipeline_graph/ado.py`: `AdoClient`. Reads `ADO_ORG_URL` and `ADO_PAT`, performs requests (`_request`: auth, retries, sign-in page detection, error mapping, same-host redirects), follows continuation tokens (`_list`), and wraps the endpoints used.
- `ado_pipeline_graph/skill/SKILL.md`: the Claude Code skill. It ships as package data and is copied by `--install-skill`.

## Graph model

`Graph` (in `core.py`) holds:

- `nodes`: node ID to `(label, shape)`. Shapes are `stadium`, `box`, `diamond` and `external`.
- `kinds`: node ID to kind (`repository`, `pipeline`, `stage`, `job`, `step`, `hook`, `condition`, `loop`, `external_template`, `variable_group`, `trigger`, `resource`, `template`). `--detail` filters on kinds through `_rank`.
- `parents`: node ID to its containing node.
- `edges`: `(source, target, label)` tuples, deduplicated by `add_edge`. Labels include `dependsOn`, `then`, `uses`, `trigger`, `resource` and `trigger (N of M runs)`.
- `templates`, `variable_groups`: names used by the pipeline, merged across pipelines in a scan.
- `pipeline_name`.

## Templates and containment

`resolve_template` decides the target of a reference:

- `path@alias` resolves inside `template_roots[alias]` (from `--template-root`).
- A leading `/` resolves from the repository root.
- Anything else resolves from the directory of the referencing file.

The result must be inside the repository root, or the alias root, and must be an existing file. Otherwise it returns `None` and the reference becomes an `external_template` node. `_expand` raises on recursive references. The repository root comes from `find_repo_root` (nearest ancestor with `.git`). In a scan it falls back to the scan root when there is no `.git`.

## Scoping rules

Documented in the `build_graph` docstring, which is the reference:

- Parameters: each file sees only its own declared defaults, overlaid with values its caller passed.
- Variables: a template sees the variables in scope at its caller, overridden by its own top-level variables. `extends` and included templates follow the same rule.
- Only `${{ parameters.x }}` and `${{ variables.x }}` with literal values are substituted (`_substitute`). Everything else is left as written.

## Invariants

- Credentials come only from `ADO_ORG_URL` and `ADO_PAT`. There is no CLI option for the PAT. HTTPS is required, and `Authorization` is dropped on a redirect to another host.
- Local mode performs no network access and never constructs `AdoClient`.
- REST failures are errors (exit 2). REST mode never falls back to local files.
- Pipeline expressions are never evaluated or executed.
- Output is deterministic and contains no absolute paths (`test_output_independent_of_checkout_location`, and the golden tests assert the fixture path is absent).
- Diagnostics (skipped files, REST hints, scan counts) go to stderr. The diagram goes to stdout or the file named.
- REST-only options are rejected without `--rest` (`_validate`).
