"""Compatibility entry point for running the CLI directly."""

from ado_pipeline_graph.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
