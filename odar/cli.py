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


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        parser = argparse.ArgumentParser(prog="odar check", description="Check which citations hold up.")
        add_check_arguments(parser)
        return cmd_check(parser.parse_args(argv[1:]))
    try:
        import run_research  # research jobs live in the top-level CLI module
    except ImportError:
        print("usage: odar check <file|-> [options]", file=sys.stderr)
        return 2
    return int(run_research.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
