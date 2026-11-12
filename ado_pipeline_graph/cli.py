from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import yaml

from .ado import AdoApiError, AdoClient
from .core import build_graph, load_graph, parse_template_roots, write_graph, write_outputs
from .scan import _yaml_files, build_repository_graph


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ado-pipeline-graph",
        description="Render Azure DevOps pipeline YAML as Mermaid.",
    )
    parser.add_argument("pipeline", nargs="?", type=Path, default=Path("azure-pipelines.yml"))
    parser.add_argument("-o", "--output", type=Path, help="write diagram to a file (default: stdout)")
    parser.add_argument("--out-dir", type=Path, nargs="?", const=Path("ado-graph"), metavar="DIR",
                        help="write .html, .md and .mmd files into DIR (default ado-graph)")
    parser.add_argument("--html", action="store_true", help="write a standalone HTML Mermaid viewer")
    parser.add_argument("--template-root", action="append", default=[], metavar="ALIAS=PATH",
                        help="map a repository alias to a local template checkout; repeatable")
    parser.add_argument("--scan-root", action="append", type=Path, default=[], metavar="PATH",
                        help="scan YAML files under this path and build a combined graph; repeatable")
    parser.add_argument("--rest", action="store_true", help="use Azure DevOps Services REST APIs")
    parser.add_argument("--project", help="Azure DevOps project name (required with --rest)")
    parser.add_argument("--pipeline-id", type=int, help="pipeline definition ID (required for REST single-pipeline mode)")
    parser.add_argument("--branch", help="branch to retrieve in REST mode")
    parser.add_argument("--runs", type=int, default=20, help="maximum run records to retrieve in REST mode")
    parser.add_argument("--api-version", default="7.1", help="Azure DevOps REST API version")
    return parser


def _scan_with_rest(args: argparse.Namespace, client: AdoClient, roots: list[Path],
                    template_roots: dict[str, Path]):
    definitions = client.get_build_definitions(args.project)
    ids_by_path: dict[str, int] = {}
    runs_by_id: dict[int, list[dict]] = {}
    yaml_files = _yaml_files(roots)
    for definition in definitions:
        definition_id = definition.get("id")
        definition_path = definition.get("path")
        if not isinstance(definition_id, int) or not isinstance(definition_path, str):
            continue
        process = definition.get("process")
        configured_path = process.get("yamlFilename") if isinstance(process, dict) else None
        yaml_path = configured_path if isinstance(configured_path, str) else definition_path
        yaml_suffix = Path(yaml_path.replace("\\", "/").lstrip("/"))
        matches = [
            path for path in yaml_files
            if path.as_posix().endswith(yaml_suffix.as_posix())
        ]
        if not matches:
            matches = [path for path in yaml_files if path.name == yaml_suffix.name]
        if len(matches) == 1:
            found = matches[0]
            ids_by_path[str(found)] = definition_id
            runs_by_id[definition_id] = client.get_runs(args.project, definition_id, args.runs)
    return build_repository_graph(
        roots, template_roots=template_roots,
        rest_runs=runs_by_id, definition_ids=ids_by_path,
        variable_groups=client.get_variable_groups(args.project),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        template_roots = parse_template_roots(args.template_root)
        if args.rest:
            if not args.project:
                raise ValueError("--project is required with --rest")
            client = AdoClient(api_version=args.api_version)
            if args.scan_root:
                graph = _scan_with_rest(args, client, args.scan_root, template_roots)
            else:
                if args.pipeline_id is None:
                    raise ValueError("--pipeline-id is required with --rest unless --scan-root is used")
                yaml_text = client.get_pipeline_yaml(args.project, args.pipeline_id, args.branch)
                pipeline = yaml.safe_load(yaml_text) or {}
                if not isinstance(pipeline, dict):
                    raise ValueError("pipeline YAML must contain a mapping at its root")
                graph = build_graph(
                    pipeline, template_roots=template_roots,
                    run_history=client.get_runs(args.project, args.pipeline_id, args.runs),
                    variable_groups=client.get_variable_groups(args.project),
                )
        elif args.scan_root:
            graph = build_repository_graph(args.scan_root, template_roots=template_roots)
        else:
            graph = load_graph(args.pipeline, template_roots)

        if args.out_dir:
            name = "repository" if args.scan_root else (
                f"pipeline-{args.pipeline_id}" if args.rest else args.pipeline.stem)
            for path in write_outputs(graph, args.out_dir, name):
                print(path)
        elif args.output:
            write_graph(graph, args.output, args.html)
        else:
            if args.html:
                raise ValueError("--html requires --output")
            sys.stdout.write(graph.to_mermaid())
        return 0
    except (AdoApiError, OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ado-pipeline-graph: error: {exc}", file=sys.stderr)
        return 2
