# Contributing

## Set up

Python 3.10 or later is required.

```sh
python -m pip install -e '.[dev]'
pre-commit install   # optional: runs ruff and whitespace hooks before each commit
```

## Checks

Run these before you open a pull request. CI runs the same checks.

```sh
pytest                      # tests; add --cov for coverage
ruff check .                # lint
ruff format --check .       # formatting (run `ruff format .` to fix)
mypy ado_pipeline_graph     # type check
```

Ruff uses a line length of 120 and targets Python 3.10. mypy is configured in `pyproject.toml`.

## Tests

- Tests never touch the network. REST tests inject a fake opener and a fake `sleep` through the `AdoClient` constructor, so no request leaves the process. Keep it that way. A new REST test must not call a real endpoint.
- Tests must not need `ADO_ORG_URL` or `ADO_PAT`. Set them with `monkeypatch` only where a test needs them.
- Fixtures live in `tests/fixtures/`.

## Golden files

`tests/golden/` holds the expected Mermaid (`.mmd`) and Markdown (`.md`) output for the `local` and `coverage` fixtures. `tests/test_render.py` compares the generated output against them.

If you change rendering on purpose, regenerate the files:

```sh
UPDATE_GOLDEN=1 pytest tests/test_render.py
```

Review the diff in `tests/golden/` before you commit it. A change there means the diagram output changed for users.

## Documentation

If you change a command, option, default or behaviour, update `README.md` in the same pull request. The option table there follows `ado-pipeline-graph --help`. Update `CLAUDE.md` if you change the architecture or an invariant, and `CHANGELOG.md` under `[Unreleased]`.

## Pull requests

- Keep each pull request focused on one change.
- Fill in the pull request template.
- Never commit a personal access token, and do not paste one into an issue, a pull request or a test.
