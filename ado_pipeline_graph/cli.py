from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from importlib import resources
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import yaml

from .ado import AdoApiError, AdoClient
from .core import build_graph, find_repo_root, load_graph, parse_template_roots, write_graph, write_outputs
from .scan import build_repository_graph, yaml_files

REST_ONLY = ("project", "pipeline_id", "branch", "runs", "api_version", "timeout")


def _version() -> str:
    try:
        return version("ado-pipeline-graph")
    except PackageNotFoundError:
        return "0+unknown"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ado-pipeline-graph",
        description="Render Azure DevOps pipeline YAML as Mermaid.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    parser.add_argument(
        "pipeline",
        nargs="?",
        type=Path,
        default=None,
        help="pipeline YAML file (default: azure-pipelines.yml; local mode only)",
    )
    out = parser.add_mutually_exclusive_group()
    out.add_argument("-o", "--output", type=Path, help="write diagram to a file (default: stdout)")
    out.add_argument("--out-dir", type=Path, metavar="DIR", help="write .html, .md and .mmd files into DIR")
    parser.add_argument("--html", action="store_true", help="with -o, write a standalone HTML viewer")
    parser.add_argument(
        "--detail", choices=["stages", "jobs", "steps"], help="level of detail (default: steps; jobs with --scan-root)"
    )
    parser.add_argument("--direction", choices=["TD", "LR"], default="TD", help="graph direction")
    parser.add_argument(
        "--mermaid-js", type=Path, metavar="PATH", help="local mermaid.min.js to inline into the HTML (offline viewing)"
    )
    parser.add_argument(
        "--template-root",
        action="append",
        default=[],
        metavar="ALIAS=PATH",
        help="map a repository alias to a local template checkout; repeatable",
    )
    parser.add_argument(
        "--scan-root",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help="scan YAML files under this path and build a combined graph; repeatable",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="with --scan-root, skip files whose path relative to the scan root matches; repeatable",
    )
    parser.add_argument("--strict", action="store_true", help="with --scan-root, fail on the first unusable file")
    parser.add_argument("--rest", action="store_true", help="use Azure DevOps Services REST APIs")
    parser.add_argument("--project", help="Azure DevOps project name (required with --rest)")
    parser.add_argument("--pipeline-id", type=int, help="pipeline definition ID (REST single-pipeline mode)")
    parser.add_argument("--branch", help="branch to retrieve in REST mode")
    parser.add_argument("--runs", type=int, help="maximum run records to retrieve in REST mode (default 20)")
    parser.add_argument("--api-version", help="Azure DevOps REST API version (default 7.1)")
    parser.add_argument("--timeout", type=float, metavar="SECONDS", help="REST request timeout (default 30)")
    parser.add_argument(
        "--no-expand",
        action="store_true",
        help="REST single-pipeline mode: use the raw YAML file instead of the service-expanded "
        "YAML (for PATs without build scope)",
    )
    parser.add_argument(
        "--install-skill",
        choices=["user", "project"],
        help="install the Claude Code skill (user: ~/.claude/skills, project: ./.claude/skills) and exit",
    )
    return parser


def _validate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.rest:
        if args.pipeline is not None:
            parser.error("the pipeline argument cannot be used with --rest")
        if not args.project:
            parser.error("--project is required with --rest")
        if args.pipeline_id is None and not args.scan_root:
            parser.error("--pipeline-id is required with --rest unless --scan-root is used")
        if args.pipeline_id is not None and args.scan_root:
            parser.error("--pipeline-id cannot be used with --scan-root")
        if args.no_expand and args.scan_root:
            parser.error("--no-expand only applies to --rest --pipeline-id")
    else:
        for name in REST_ONLY + ("no_expand",):
            if getattr(args, name) not in (None, False):
                parser.error(f"--{name.replace('_', '-')} requires --rest")
    if args.scan_root:
        if args.pipeline is not None:
            parser.error("the pipeline argument cannot be used with --scan-root")
    elif args.exclude or args.strict:
        parser.error("--exclude and --strict require --scan-root")
    if args.html and not args.output:
        parser.error("--html requires -o/--output")
    if args.runs is not None and args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.pipeline_id is not None and args.pipeline_id < 1:
        parser.error("--pipeline-id must be at least 1")
    if args.timeout is not None and args.timeout <= 0:
        parser.error("--timeout must be positive")


def _install_skill(where: str) -> Path:
    text = resources.files("ado_pipeline_graph").joinpath("skill/SKILL.md").read_text(encoding="utf-8")
    if where == "user":
        base = Path(os.environ.get("CLAUDE_SKILLS_DIR") or Path.home() / ".claude" / "skills")
    else:
        base = Path.cwd() / ".claude" / "skills"
    dest = base / "ado-pipeline-graph" / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


def _parts(value: str) -> tuple[str, ...]:
    return tuple(p for p in value.replace("\\", "/").split("/") if p and p != ".")


def _scan_with_rest(
    args: argparse.Namespace, client: AdoClient, roots: list[Path], template_roots: dict[str, Path], **scan_kwargs
):
    """Match build definitions to local files by whole path components, then add run history."""
    definitions = client.get_build_definitions(args.project)
    # (file, relative-path candidates, directory names that identify the repo)
    candidates = []
    for root in roots:
        root = root.resolve()
        for path in yaml_files([root], scan_kwargs.get("exclude")):
            repo = find_repo_root(path)
            rels = set()
            for base in (root, repo):
                if path.is_relative_to(base):
                    rels.add(path.relative_to(base).parts)
            candidates.append((path, rels, {root.name, repo.name}))
    ids_by_path: dict[str, int] = {}
    runs_by_id: dict[int, list[dict]] = {}
    ambiguous = unmatched = 0
    for definition in definitions:
        definition_id = definition.get("id")
        process = definition.get("process")
        wanted = (
            _parts(process["yamlFilename"])
            if isinstance(process, dict) and isinstance(process.get("yamlFilename"), str)
            else ()
        )
        if not isinstance(definition_id, int) or not wanted:
            unmatched += 1
            continue
        matches = [c for c in candidates if wanted in c[1]]
        repository = definition.get("repository")
        repo_name = repository.get("name") if isinstance(repository, dict) else None
        if len(matches) > 1 and isinstance(repo_name, str):
            matches = [c for c in matches if repo_name in c[2]] or matches
        if len({c[0] for c in matches}) == 1:
            found = matches[0][0]
            ids_by_path[str(found)] = definition_id
            runs_by_id[definition_id] = client.get_runs(args.project, definition_id, args.runs)
        elif matches:
            ambiguous += 1
        else:
            unmatched += 1
    print(
        f"ado-pipeline-graph: {len(definitions)} definitions found, {len(ids_by_path)} matched, "
        f"{ambiguous} ambiguous, {unmatched} unmatched",
        file=sys.stderr,
    )
    return build_repository_graph(
        roots,
        template_roots=template_roots,
        rest_runs=runs_by_id,
        definition_ids=ids_by_path,
        variable_groups=client.get_variable_groups(args.project),
        **scan_kwargs,
    )


def _local_name(path: Path) -> str:
    """Pipeline path relative to its repo root, separators replaced, so same-named files differ."""
    path = path.resolve()
    rel = path.relative_to(find_repo_root(path)).with_suffix("")
    return "-".join(rel.parts)


def _fetch_rest_pipeline(args: argparse.Namespace, client: AdoClient):
    try:
        if args.no_expand:
            return client.get_pipeline_yaml(args.project, args.pipeline_id, args.branch)
        return client.get_expanded_yaml(args.project, args.pipeline_id, args.branch)
    except AdoApiError as exc:
        if not args.no_expand and any(code in str(exc) for code in ("(401)", "(403)", "sign-in")):
            print(
                "ado-pipeline-graph: the template preview call was denied; it needs a PAT with build "
                "scope. Use --no-expand to read the raw YAML file instead (templates stay unresolved).",
                file=sys.stderr,
            )
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.install_skill:
        try:
            print(_install_skill(args.install_skill))
            return 0
        except OSError as exc:
            print(f"ado-pipeline-graph: error: {exc}", file=sys.stderr)
            return 2
    _validate(parser, args)
    detail = args.detail or ("jobs" if args.scan_root else "steps")
    try:
        template_roots = parse_template_roots(args.template_root)
        mermaid_js = args.mermaid_js.read_text(encoding="utf-8") if args.mermaid_js else None
        pipeline = args.pipeline or Path("azure-pipelines.yml")
        if args.rest:
            client_kwargs = {"timeout": args.timeout} if args.timeout else {}
            client = AdoClient(api_version=args.api_version or "7.1", **client_kwargs)
            args.runs = args.runs or 20
            if args.scan_root:
                skipped: list[tuple[Path, str]] = []
                graph = _scan_with_rest(
                    args,
                    client,
                    args.scan_root,
                    template_roots,
                    exclude=args.exclude,
                    strict=args.strict,
                    skipped=skipped,
                )
            else:
                skipped = []
                yaml_text = _fetch_rest_pipeline(args, client)
                data = yaml.safe_load(yaml_text) or {}
                if not isinstance(data, dict):
                    raise ValueError("pipeline YAML must contain a mapping at its root")
                graph = build_graph(
                    data,
                    template_roots=template_roots,
                    run_history=client.get_runs(args.project, args.pipeline_id, args.runs),
                    variable_groups=client.get_variable_groups(args.project),
                )
        elif args.scan_root:
            skipped = []
            graph = build_repository_graph(
                args.scan_root, template_roots=template_roots, exclude=args.exclude, strict=args.strict, skipped=skipped
            )
        else:
            skipped = []
            graph = load_graph(pipeline, template_roots)

        if skipped:
            base = args.scan_root[0].resolve()
            print(f"ado-pipeline-graph: skipped {len(skipped)} file(s):", file=sys.stderr)
            for path, reason in skipped:
                shown = path.relative_to(base) if path.is_relative_to(base) else path
                print(f"  {shown}: {reason}", file=sys.stderr)

        opts = {"detail": detail, "direction": args.direction, "mermaid_js": mermaid_js}
        if args.out_dir:
            name = (
                "repository"
                if args.scan_root
                else (f"pipeline-{args.pipeline_id}" if args.rest else _local_name(pipeline))
            )
            for path in write_outputs(graph, args.out_dir, name, **opts):
                print(path)
        elif args.output:
            write_graph(graph, args.output, args.html, **opts)
        else:
            sys.stdout.write(graph.to_mermaid(detail, args.direction))
        return 0
    except (AdoApiError, OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ado-pipeline-graph: error: {exc}", file=sys.stderr)
        return 2
