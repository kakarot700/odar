"""`odar` console entry point.

odar check answer.md                 # verify the citations in a pasted answer
cat answer.txt | odar check -        # from stdin
odar check answer.md --json --no-llm --output-dir out/
odar run "question" ...              # forwarded to run_research.py (research jobs)
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional


def add_check_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("input", help="file with the answer/essay to check, or - for stdin")
    p.add_argument("--json", action="store_true", help="print the JSON report instead of markdown")
    p.add_argument("--output-dir", default="", help="also write <check_id>.md and .json here")
    p.add_argument("--no-llm", action="store_true", help="fully local: no model calls (parser + NLI only)")
    p.add_argument("--no-search", action="store_true", help="do not search for replacement sources")
    p.add_argument("--max-claims", type=int, default=40)
    p.add_argument("--max-urls", type=int, default=30)
    p.add_argument("--max-replacements", type=int, default=8, help="replacement-source searches")
    p.add_argument("--max-llm-calls", type=int, default=10)
    p.add_argument("--timeout", type=float, default=420.0, help="soft deadline in seconds")


def cmd_check(args: argparse.Namespace) -> int:
    from odar.check import CheckLimits, check_text

    text = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    if not text.strip():
        print("nothing to check: empty input", file=sys.stderr)
        return 2
    report = check_text(
        text,
        use_llm=not args.no_llm,
        search=not args.no_search,
        limits=CheckLimits(
            max_claims=args.max_claims,
            max_urls=args.max_urls,
            max_replacement_searches=args.max_replacements,
            max_llm_calls=args.max_llm_calls,
            deadline_s=args.timeout,
        ),
    )
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        base = os.path.join(args.output_dir, report.check_id)
        with open(base + ".md", "w", encoding="utf-8") as handle:
            handle.write(report.to_markdown())
        with open(base + ".json", "w", encoding="utf-8") as handle:
            handle.write(report.to_json())
    print(report.to_json() if args.json else report.to_markdown())
    return 0


def cmd_refs(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="odar refs", description="Validate references and build a bibliography."
    )
    parser.add_argument("input", help="file with one reference per line, or - for stdin")
    parser.add_argument("--style", choices=("apa", "mla", "chicago", "ieee"), default="apa")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    from odar.scholar import check_references
    from odar.web.exporters import references_markdown

    text = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    result = check_references(text, style=args.style)
    if args.json:
        import json

        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(references_markdown(result))
    bad = result["counts"].get("NOT FOUND", 0) + result["counts"].get("MISMATCH", 0)
    return 1 if bad else 0


def cmd_serve(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="odar serve", description="Run the ODAR web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None, help="run history database (default odar_web.db)")
    args = parser.parse_args(argv)
    try:
        import uvicorn

        from odar.web.app import create_app
        from odar.web.store import RunStore
    except ImportError:
        print("the web app needs extras: pip install 'odar[web,pdf]'", file=sys.stderr)
        return 2
    import os

    app = create_app(RunStore(args.db or os.environ.get("ODAR_WEB_DB", "odar_web.db")))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        parser = argparse.ArgumentParser(prog="odar check", description="Check which citations hold up.")
        add_check_arguments(parser)
        return cmd_check(parser.parse_args(argv[1:]))
    if argv and argv[0] in ("refs", "references"):
        return cmd_refs(argv[1:])
    if argv and argv[0] == "serve":
        return cmd_serve(argv[1:])
    try:
        import run_research  # research jobs live in the top-level CLI module
    except ImportError:
        print("usage: odar check <file|-> [options]", file=sys.stderr)
        return 2
    return int(run_research.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
