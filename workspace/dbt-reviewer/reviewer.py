#!/usr/bin/env python3
"""dbt AI Code Reviewer — CLI entry point.

Usage:
    # From a diff file
    python reviewer.py --diff ../jaffle_shop_sample/models/sample.diff \\
                       --project ../jaffle_shop_sample

    # From git directly
    git diff HEAD~1 | python reviewer.py --project ../jaffle_shop_sample

    # Skip AI checks (deterministic only)
    python reviewer.py --diff sample.diff --no-semantic
"""

import argparse
import sys
from pathlib import Path

# Allow running from any working directory
sys.path.insert(0, str(Path(__file__).parent))

from core.diff_parser import parse_diff
from core.deterministic import run_all as run_deterministic
from core.reporter import exit_code, format_report
from core.semantic import run_semantic_checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reviewer",
        description="Review dbt model changes with deterministic rules and AI analysis.",
    )
    parser.add_argument(
        "--diff",
        metavar="FILE",
        help="Path to a unified diff file. Reads from stdin when omitted.",
    )
    parser.add_argument(
        "--project",
        metavar="DIR",
        help="Path to the dbt project root (used for context, not required).",
    )
    parser.add_argument(
        "--no-semantic",
        action="store_true",
        help="Skip AI-powered semantic checks (deterministic rules only).",
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        help="Anthropic API key. Defaults to ANTHROPIC_API_KEY environment variable.",
    )
    parser.add_argument(
        "--model",
        metavar="MODEL",
        default="claude-haiku-4-5-20251001",
        help="Claude model to use for semantic checks (default: claude-haiku-4-5-20251001).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # ------------------------------------------------------------------ #
    # 1. Read the diff
    # ------------------------------------------------------------------ #
    if args.diff:
        diff_path = Path(args.diff)
        if not diff_path.exists():
            print(f"Error: diff file not found: {args.diff}", file=sys.stderr)
            sys.exit(2)
        diff_text = diff_path.read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            parser.print_help()
            print("\nError: no diff provided — use --diff FILE or pipe from git diff.", file=sys.stderr)
            sys.exit(2)
        diff_text = sys.stdin.read()

    if not diff_text.strip():
        print("Empty diff — nothing to review.")
        sys.exit(0)

    # ------------------------------------------------------------------ #
    # 2. Parse
    # ------------------------------------------------------------------ #
    file_diffs = parse_diff(diff_text)
    if not file_diffs:
        print("No SQL files found in diff.")
        sys.exit(0)

    print(f"Reviewing {len(file_diffs)} changed SQL file(s)…\n")

    # ------------------------------------------------------------------ #
    # 3. Deterministic checks
    # ------------------------------------------------------------------ #
    all_findings = run_deterministic(file_diffs)

    # ------------------------------------------------------------------ #
    # 4. Semantic checks (optional)
    # ------------------------------------------------------------------ #
    if not args.no_semantic:
        semantic = run_semantic_checks(
            file_diffs,
            api_key=args.api_key,
            model=args.model,
        )
        all_findings.extend(semantic)

    # ------------------------------------------------------------------ #
    # 5. Report
    # ------------------------------------------------------------------ #
    print(format_report(file_diffs, all_findings))
    sys.exit(exit_code(all_findings))


if __name__ == "__main__":
    main()
