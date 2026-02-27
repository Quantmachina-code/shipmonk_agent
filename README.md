# shipmonk_agent

A repository exploring AI-assisted code review tooling for dbt projects. It contains a ready-to-use CLI reviewer and a sample dbt project to test it against.

---

## Table of Contents

- [Is This Doable?](#is-this-doable)
  - [Minimal Working Example](#minimal-working-example)
  - [How the Full Tool Extends This](#how-the-full-tool-extends-this)
- [Repository Layout](#repository-layout)
- [Getting Started](#getting-started)
- [Tutorial: Adding New Checks](#tutorial-adding-new-checks)
  - [Part 1 — Adding a Deterministic Rule](#part-1--adding-a-deterministic-rule)
  - [Part 2 — Adding a Semantic Category](#part-2--adding-a-semantic-category)
  - [Part 3 — Adding a File-Level Check](#part-3--adding-a-file-level-check)
  - [Part 4 — Combining Both Layers](#part-4--combining-both-layers)
- [What Can Be Improved](#what-can-be-improved)
- [Further Reading](#further-reading)

---

## Is This Doable?

Yes. A Python CLI that accepts a git diff and prints structured review findings is a well-defined, straightforward pattern. Git produces diffs in a standardised format called *unified diff*, and parsing it requires no external libraries — only the Python standard library.

The output structure (file, line, severity, description) maps directly to a dataclass. Everything else — deterministic regex rules, AI calls, terminal formatting — layers on top of that core loop.

### Minimal Working Example

This is a self-contained 90-line script that demonstrates the complete pattern: read a diff from stdin or a file, parse it, run a check, and print structured output.

```python
#!/usr/bin/env python3
"""
minimal_reviewer.py — self-contained diff reviewer in ~90 lines.

Usage:
    git diff HEAD~1 | python minimal_reviewer.py
    python minimal_reviewer.py --diff changes.diff
"""

import re
import sys
import argparse
from dataclasses import dataclass, field
from typing import List, Optional


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class FileDiff:
    filename: str
    added_lines: List[str] = field(default_factory=list)

@dataclass
class Finding:
    rule: str
    severity: str          # "error" | "warning" | "info"
    file: str
    line: str
    message: str


# ── Diff parser ────────────────────────────────────────────────────────────────

def parse_diff(text: str) -> List[FileDiff]:
    """Read unified diff text and return one FileDiff per changed .sql file."""
    files: List[FileDiff] = []
    current: Optional[FileDiff] = None

    for raw_line in text.splitlines():
        if raw_line.startswith("diff --git"):
            if current:
                files.append(current)
            current = None
        elif raw_line.startswith("+++ b/"):
            path = raw_line[6:]
            if path.endswith(".sql"):
                current = FileDiff(filename=path)
        elif current and raw_line.startswith("+") and not raw_line.startswith("+++"):
            current.added_lines.append(raw_line[1:])

    if current:
        files.append(current)
    return files


# ── Checks ─────────────────────────────────────────────────────────────────────

_SELECT_STAR = re.compile(r"\bselect\s+\*", re.IGNORECASE)
_HARDCODED   = re.compile(r"\b(?:from|join)\s+\w+\.\w+", re.IGNORECASE)

def run_checks(file_diffs: List[FileDiff]) -> List[Finding]:
    findings = []
    for fd in file_diffs:
        for line in fd.added_lines:
            if _SELECT_STAR.search(line):
                findings.append(Finding(
                    rule="SELECT_STAR", severity="error",
                    file=fd.filename, line=line.strip(),
                    message="SELECT * causes schema drift — enumerate columns explicitly.",
                ))
            if _HARDCODED.search(line) and "{{" not in line:
                findings.append(Finding(
                    rule="HARDCODED_SCHEMA", severity="error",
                    file=fd.filename, line=line.strip(),
                    message="Hardcoded schema reference — use {{ ref() }} or {{ source() }}.",
                ))
    return findings


# ── Reporter ───────────────────────────────────────────────────────────────────

def print_report(findings: List[Finding]) -> int:
    if not findings:
        print("No issues found.")
        return 0
    for f in findings:
        print(f"[{f.severity.upper()}] {f.rule}")
        print(f"  File    : {f.file}")
        print(f"  Line    : {f.line[:80]}")
        print(f"  Message : {f.message}")
        print()
    errors = sum(1 for f in findings if f.severity == "error")
    print(f"{'FAILED' if errors else 'PASSED'} — {errors} error(s), "
          f"{len(findings) - errors} warning(s)")
    return 1 if errors else 0


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff", help="Path to diff file (default: stdin)")
    args = parser.parse_args()

    if args.diff:
        with open(args.diff) as fh:
            diff_text = fh.read()
    else:
        diff_text = sys.stdin.read()

    if not diff_text.strip():
        print("No diff input.")
        sys.exit(0)

    file_diffs = parse_diff(diff_text)
    findings   = run_checks(file_diffs)
    sys.exit(print_report(findings))

if __name__ == "__main__":
    main()
```

Run it:

```bash
# Against the sample diff included in this repo
python minimal_reviewer.py --diff workspace/jaffle_shop_sample/models/sample.diff

# Live from git
git diff HEAD~1 | python minimal_reviewer.py
```

Expected output shape:

```
[ERROR] SELECT_STAR
  File    : models/staging/stg_customers.sql
  Line    : select * from prod.customers
  Message : SELECT * causes schema drift — enumerate columns explicitly.

[ERROR] HARDCODED_SCHEMA
  File    : models/staging/stg_customers.sql
  Line    : select * from prod.customers
  Message : Hardcoded schema reference — use {{ ref() }} or {{ source() }}.

FAILED — 12 error(s), 0 warning(s)
```

### How the Full Tool Extends This

The production tool in `workspace/dbt-reviewer/` takes this exact skeleton and adds:

| Extension | What it adds |
|---|---|
| `diff_parser.py` | Reconstructs full file content (not just added lines) for semantic analysis |
| `deterministic.py` | More rules (`MISSING_REF`), deduplication, Jinja-aware filtering |
| `semantic.py` | Sends full file content to Claude or GPT, parses structured JSON findings |
| `reporter.py` | Grouped sections by severity, 62-char width formatting, line truncation |
| `reviewer.py` | `--no-semantic`, `--provider`, `--model`, `--api-key`, stdin or file input |

---

## Repository Layout

```
shipmonk_agent/
├── README.md                          ← you are here
└── workspace/
    ├── dbt-reviewer/                  ← the CLI tool
    │   ├── reviewer.py                main entry point
    │   ├── requirements.txt
    │   ├── core/
    │   │   ├── diff_parser.py         unified diff → FileDiff[]
    │   │   ├── deterministic.py       regex-based checks → Finding[]
    │   │   ├── semantic.py            LLM-based checks → Finding[]
    │   │   └── reporter.py            Finding[] → terminal output
    │   └── prompts/
    │       └── review_prompt.txt      system prompt for the AI layer
    └── jaffle_shop_sample/            ← test data
        └── models/
            ├── sample.diff            diff with intentional violations
            ├── staging/               stg_customers, stg_orders, stg_payments
            └── marts/                 customers, orders
```

---

## Getting Started

```bash
cd workspace/dbt-reviewer

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Deterministic checks only (no API key needed)
python reviewer.py \
  --diff ../jaffle_shop_sample/models/sample.diff \
  --no-semantic

# Full review with Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
python reviewer.py \
  --diff ../jaffle_shop_sample/models/sample.diff \
  --provider anthropic

# Full review with OpenAI
pip install openai
export OPENAI_API_KEY=sk-proj-...
python reviewer.py \
  --diff ../jaffle_shop_sample/models/sample.diff \
  --provider openai \
  --model gpt-4.1-nano
```

See `workspace/dbt-reviewer/README.md` for the full CLI reference, all AI models, and the complete improvement roadmap.

---

## Tutorial: Adding New Checks

This section walks through every way to extend the reviewer — from a single new regex rule to a full semantic category. All examples are production-ready and can be dropped into the actual codebase.

---

### Part 1 — Adding a Deterministic Rule

Deterministic rules live in `workspace/dbt-reviewer/core/deterministic.py`. They are pure functions — no network calls, no state — and run on every invocation regardless of `--no-semantic`.

**The anatomy of a deterministic rule:**

```
added_lines (the lines the PR author wrote)
     │
     ▼
regex / string pattern
     │
     ▼ (if match)
Finding(rule, severity, file, line, message)
```

Rules always check `added_lines`, never `removed_lines`. This scopes the review to code the author introduced and avoids noise from pre-existing violations.

---

#### Example A: Catch `SELECT DISTINCT` (masks fanout bugs)

**Step 1 — Define the regex** near the top of `deterministic.py`, alongside the existing patterns:

```python
_DISTINCT_RE = re.compile(r"\bselect\s+distinct\b", re.IGNORECASE)
```

**Step 2 — Write the check function:**

```python
def check_select_distinct(file_diffs: List[FileDiff]) -> List[Finding]:
    findings = []
    seen: set = set()
    for fd in file_diffs:
        for line in fd.added_lines:
            if _DISTINCT_RE.search(line):
                key = (fd.filename, line.strip())
                if key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(
                    rule="SELECT_DISTINCT",
                    severity="warning",
                    file=fd.filename,
                    line=line.strip(),
                    message=(
                        "SELECT DISTINCT often masks a JOIN_FANOUT problem — "
                        "find the root cause and fix the join instead of "
                        "deduplicating the output."
                    ),
                ))
    return findings
```

**Step 3 — Register it in `run_all()`:**

```python
def run_all(file_diffs: List[FileDiff]) -> List[Finding]:
    findings = []
    findings.extend(check_select_star(file_diffs))
    findings.extend(check_hardcoded_schema(file_diffs))
    findings.extend(check_missing_ref(file_diffs))
    findings.extend(check_select_distinct(file_diffs))   # ← new
    return findings
```

Done. The finding will appear in the WARNINGS section of every report going forward.

---

#### Example B: Catch `LIMIT` inside a CTE (silently truncates downstream models)

```python
_LIMIT_IN_CTE_RE = re.compile(r"\blimit\s+\d+", re.IGNORECASE)

def check_limit_in_cte(file_diffs: List[FileDiff]) -> List[Finding]:
    findings = []
    seen: set = set()
    for fd in file_diffs:
        # Track whether we are inside a CTE block.
        # A simple heuristic: any LIMIT that appears before the final SELECT.
        lines = fd.added_lines
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if _LIMIT_IN_CTE_RE.search(stripped):
                # Skip if this looks like the very last statement (LIMIT on the final output is fine)
                remaining = lines[idx + 1:]
                has_more_select = any(
                    re.search(r"\bselect\b", l, re.IGNORECASE) for l in remaining
                )
                if has_more_select:
                    key = (fd.filename, stripped)
                    if key not in seen:
                        seen.add(key)
                        findings.append(Finding(
                            rule="LIMIT_IN_CTE",
                            severity="warning",
                            file=fd.filename,
                            line=stripped,
                            message=(
                                "LIMIT inside a CTE silently truncates all downstream "
                                "models that reference it. Move LIMIT to the final "
                                "SELECT or remove it entirely."
                            ),
                        ))
    return findings
```

---

#### Example C: Catch a `JOIN` with no `ON` clause (Cartesian product)

```python
_JOIN_RE  = re.compile(r"\bjoin\b", re.IGNORECASE)
_ON_RE    = re.compile(r"\bon\b",   re.IGNORECASE)

def check_cartesian_join(file_diffs: List[FileDiff]) -> List[Finding]:
    """Flag a JOIN line that has no ON clause on the same or following line."""
    findings = []
    seen: set = set()
    for fd in file_diffs:
        lines = fd.added_lines
        for idx, line in enumerate(lines):
            if not _JOIN_RE.search(line):
                continue
            # Check this line and the next for an ON clause
            next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
            combined = line + " " + next_line
            if not _ON_RE.search(combined):
                key = (fd.filename, line.strip())
                if key not in seen:
                    seen.add(key)
                    findings.append(Finding(
                        rule="CARTESIAN_JOIN",
                        severity="error",
                        file=fd.filename,
                        line=line.strip(),
                        message=(
                            "JOIN without an ON clause produces a Cartesian product — "
                            "every row multiplied by every row in the joined table."
                        ),
                    ))
    return findings
```

---

#### Quick reference: more rule ideas

| Rule ID | Severity | Pattern to match | What goes wrong |
|---|---|---|---|
| `SELECT_DISTINCT` | warning | `SELECT DISTINCT` | Masks fanout; fix the join instead |
| `LIMIT_IN_CTE` | warning | `LIMIT` before a downstream SELECT | Silently truncates downstream |
| `CARTESIAN_JOIN` | error | `JOIN` with no `ON` | Full cross-product, huge result sets |
| `NULL_COMPARISON` | warning | `= NULL` or `!= NULL` | Always false/true; use `IS NULL` |
| `NO_CTE` | warning | File has no `with … as (` | Logic mixed into one giant SELECT |
| `DIRECT_SOURCE_IN_MART` | error | `source(` reference in a `marts/` path | Mart bypasses staging layer |
| `DEPRECATED_SCHEMA_SYNTAX` | info | `schema.dbt_` pattern | Old naming convention in new code |
| `UNPREFIXED_TEMP_TABLE` | warning | `create temp table` | Temp tables left in scripts |

---

### Part 2 — Adding a Semantic Category

Semantic categories are defined in `workspace/dbt-reviewer/prompts/review_prompt.txt`. No Python changes are needed — the model reads the prompt and returns structured JSON that the existing Python code already handles.

**The anatomy of the prompt file:**

```
You are a senior analytics engineer ...
Return ONLY a valid JSON array ...

Analyse the following dbt SQL model for these categories:

1. **JOIN_FANOUT** (severity: "warning")
   ...

2. **NAMING** (severity: "info")
   ...

[more categories]

Return JSON in this exact schema:
[{"rule": "...", "severity": "...", "message": "..."}]
```

---

#### Step 1 — Open the prompt file

```
workspace/dbt-reviewer/prompts/review_prompt.txt
```

#### Step 2 — Add a numbered category block

Follow the existing format exactly. Each block needs: a bold name in `**UPPERCASE**`, a severity, a one-line description, and bullet points listing what to look for.

**Example — add `INCREMENTAL_RISK`:**

```
5. **INCREMENTAL_RISK** (severity: "warning")
   Incremental model configurations that can cause silent data loss or
   incorrect results.
   Look for:
   - An `{{ config(materialized='incremental') }}` block with no
     `{% if is_incremental() %}` filter on the WHERE clause
   - A WHERE clause that filters on a column not in the incremental key
   - Missing `unique_key` in the config, which causes full re-inserts
   - Incremental logic that could allow late-arriving rows to be missed
```

#### Step 3 — Add the rule name to the schema comment

Find this line in the prompt:

```
"rule": "<one of: JOIN_FANOUT | NAMING | MODEL_STRUCTURE | PERFORMANCE>",
```

Change it to:

```
"rule": "<one of: JOIN_FANOUT | NAMING | MODEL_STRUCTURE | PERFORMANCE | INCREMENTAL_RISK>",
```

That is the entire change. On the next run, the model will return findings like:

```json
[
  {
    "rule": "INCREMENTAL_RISK",
    "severity": "warning",
    "message": "Incremental model uses config(materialized='incremental') but the WHERE clause has no {% if is_incremental() %} guard — every run will process the full table."
  }
]
```

The reporter already handles any rule name, so these will appear in the WARNINGS section automatically.

---

#### More semantic category ideas

| Category | Severity | What to instruct the model to look for |
|---|---|---|
| `INCREMENTAL_RISK` | warning | Incremental model with no `is_incremental()` guard; missing `unique_key` |
| `TEST_COVERAGE` | info | New public columns with no mention in `schema.yml` |
| `DEPRECATED_LOGIC` | warning | Business calculations that reference archived tables or known-deprecated fields |
| `WINDOW_FUNCTION` | info | Window frames like `ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING` |
| `DOCUMENTATION` | info | Mart model with no column-level descriptions visible in the SQL comments |
| `SECURITY` | error | Columns like `email`, `ssn`, `card_number` exposed without masking in a mart |
| `IDEMPOTENCY` | warning | Logic that would produce different results on repeated runs (non-deterministic functions: `NOW()`, `RANDOM()`) |

---

### Part 3 — Adding a File-Level Check

Sometimes a rule applies to the whole file, not individual lines. For example: "this mart model has more than 20 columns — split it up." These run against `fd.new_content` instead of `fd.added_lines`.

```python
_MAX_COLUMNS = 20  # configurable threshold

def check_wide_mart(file_diffs: List[FileDiff]) -> List[Finding]:
    """Warn when a mart model selects more than _MAX_COLUMNS columns."""
    findings = []
    for fd in file_diffs:
        # Only apply to mart models
        if "/marts/" not in fd.filename and "/mart_" not in fd.filename:
            continue

        # Count SELECT-level column expressions (rough heuristic)
        select_block = re.search(
            r"\bselect\b(.+?)(?:\bfrom\b)",
            fd.new_content,
            re.IGNORECASE | re.DOTALL,
        )
        if not select_block:
            continue

        columns = [
            c.strip()
            for c in select_block.group(1).split(",")
            if c.strip()
        ]
        if len(columns) > _MAX_COLUMNS:
            findings.append(Finding(
                rule="WIDE_MART",
                severity="warning",
                file=fd.filename,
                line="",
                message=(
                    f"Mart model selects {len(columns)} columns — consider splitting "
                    f"into focused models to reduce complexity (threshold: {_MAX_COLUMNS})."
                ),
            ))
    return findings
```

Register it in `run_all()` like any other rule.

---

### Part 4 — Combining Both Layers

Some violations are best caught by the deterministic layer *and* the semantic layer for different reasons:

| Violation | Deterministic layer | Semantic layer |
|---|---|---|
| `SELECT *` | Detects the line exactly | Explains *why* it is risky in this specific model's context |
| Hardcoded schema | Catches every instance | Notes structural implications (e.g., breaks dev/prod parity in this model) |
| JOIN without aggregation | Cannot detect (requires understanding cardinality) | `JOIN_FANOUT` — model-level reasoning |
| Bad naming | Cannot detect (needs vocabulary) | `NAMING` — interprets abbreviations |

A good strategy is to use deterministic rules as hard CI gates (exit code 1) and semantic categories as review commentary (exit code 0) so that only clearly wrong patterns block a merge, while AI suggestions surface for discussion.

---

## What Can Be Improved

### sqlglot is listed as a dependency but unused

`sqlglot` is a full SQL AST parser. The current deterministic checks use regex, which has false positives (e.g., `SELECT *` inside a SQL comment triggers `SELECT_STAR`). Switching to sqlglot-based AST traversal would eliminate false positives entirely and enable checks that regex cannot express, such as detecting unbounded window frames or type-mismatched comparisons.

### Semantic checks are sequential and slow on large PRs

Each changed file triggers a separate API call in sequence. On a PR touching 10 files, this means 10 serial round-trips. Adding `concurrent.futures.ThreadPoolExecutor` or `asyncio.gather` would run all calls in parallel, reducing total latency from N×latency to roughly 1×latency.

### No retry logic on transient API failures

If an API call fails (network hiccup, rate limit), the file is silently skipped. Adding exponential backoff (2s, 4s, 8s) with a configurable `--retries` flag would make the tool reliable in CI environments.

### Severity is not configurable

Some teams want `SELECT_STAR` as a warning rather than a blocker; others want `NAMING` findings to cause CI failures. A `.dbt-reviewer.yml` at the project root could override severity per rule. The reporter and exit-code logic already consume the `severity` field — only the rules themselves need to respect the config.

### No JSON output mode

There is no `--output json` flag. Adding one would enable integration with GitHub's PR review API, Slack webhooks, or any downstream tooling without screen-scraping the terminal output.

### The prompt sends full file content

The semantic layer sends the entire reconstructed file to the model, even if only two lines changed. Sending only the diff hunk (added lines + N lines of context) would reduce token usage by 60–80% on incremental changes, cutting cost and latency.

---

## Further Reading

- `workspace/dbt-reviewer/README.md` — full CLI reference, all AI models, complete improvement roadmap
- `workspace/dbt-reviewer/prompts/review_prompt.txt` — the system prompt governing all semantic checks
- `workspace/jaffle_shop_sample/models/sample.diff` — a diff with intentional violations to test against
