# dbt Code Reviewer

A CLI that reads a git diff, extracts changed SQL files, and reports findings grouped by severity. Two layers: fast regex rules that always run, and an optional AI layer for the things regex can't catch.

---

## Table of Contents

- [What It Checks](#what-it-checks)
- [How It Works](#how-it-works)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [CLI Reference](#cli-reference)
- [Running Without AI](#running-without-ai)
- [Why Anthropic and OpenAI](#why-anthropic-and-openai)
- [Model Reference](#model-reference)
- [Understanding the Output](#understanding-the-output)
- [What Can Be Improved](#what-can-be-improved)
- [Exit Codes](#exit-codes)

---

## What It Checks

**Deterministic layer** — regex, no API key, always runs:

| Rule | Severity | What it catches |
|---|---|---|
| `SELECT_STAR` | error | `SELECT *` — any upstream column change silently breaks downstream models |
| `HARDCODED_SCHEMA` | error | `prod.table` in a FROM or JOIN — breaks in dev/staging environments |
| `MISSING_REF` | warning | `stg_`, `fct_`, `dim_` etc. referenced directly instead of through `{{ ref() }}` |

**Semantic layer** — AI, requires API key, skippable with `--no-semantic`:

| Rule | Severity | What it catches |
|---|---|---|
| `JOIN_FANOUT` | warning | Right-hand table in a join is not unique on the join key — rows multiply silently |
| `NAMING` | info | Abbreviated or ambiguous identifiers: `dt`, `flg`, `cust_id`, `amt` |
| `MODEL_STRUCTURE` | warning | Mart reading raw tables directly, staging with no real transformation, missing CTEs |
| `PERFORMANCE` | warning | Cartesian products, repeated subqueries, no filter push-down |

---

## How It Works

```
git diff
    │
    ▼
diff_parser.py      Parses the unified diff format into FileDiff objects.
                    Each holds: filename, added lines, removed lines,
                    and the reconstructed full file content.
    │
    ├──► deterministic.py    Regex on added_lines only. No network calls.
    │                        Catches things that are always wrong.
    │
    └──► semantic.py         Sends full file content to the model.
                             Gets back a JSON array of findings.
                             Skipped with --no-semantic or if no API key is set.
    │
    ▼
reporter.py         Groups findings by severity, formats the terminal output,
                    returns exit code 1 if any errors were found.
```

Deterministic checks run on `added_lines` only — the lines the author introduced. This keeps the report focused on new problems, not existing ones.

Semantic checks get the full reconstructed file so the model can reason about the SQL as a whole: join structure, CTE layering, naming patterns across the whole model.

---

## Installation

```bash
cd workspace/dbt-reviewer

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# Only needed for OpenAI
pip install openai
```

Dependencies:
- `anthropic>=0.20.0` — Anthropic SDK
- `sqlglot>=20.0.0` — SQL parser (listed as a dependency; not yet used in checks — see [What Can Be Improved](#what-can-be-improved))
- `openai` — optional, only for `--provider openai`

---

## Quick Start

```bash
# From a diff file
python reviewer.py \
  --diff ../jaffle_shop_sample/models/sample.diff \
  --project ../jaffle_shop_sample

# Piped from git
git diff HEAD~1 | python reviewer.py --project .

# In CI (GitHub Actions)
git diff origin/main...HEAD | python reviewer.py \
  --project . \
  --provider anthropic
```

---

## CLI Reference

| Argument | Default | Description |
|---|---|---|
| `--diff FILE` | stdin | Diff file to read. Omit to pipe from `git diff`. |
| `--project DIR` | — | dbt project root. Provides context; does not affect parsing. |
| `--no-semantic` | off | Skip AI checks. Only deterministic rules run. No key needed. |
| `--provider` | `anthropic` | `anthropic` or `openai`. |
| `--api-key KEY` | env var | Overrides `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`. |
| `--model MODEL` | see below | Model override. Defaults depend on provider. |

Set your key once as an environment variable instead of passing it every time:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-proj-...
```

---

## Running Without AI

Pass `--no-semantic` and no API key is needed:

```bash
python reviewer.py --diff changes.diff --no-semantic
```

Good for:
- Testing the tool before you have a key
- CI pipelines where you only want hard rule enforcement
- Keeping costs at zero for high-frequency checks (e.g., pre-commit hook)

The deterministic layer alone catches the two most dangerous patterns (`SELECT_STAR` and `HARDCODED_SCHEMA`) and will block a PR via exit code 1.

---

## Why Anthropic and OpenAI

Both providers were chosen because they cover almost all real-world situations where someone would use this tool.

**Anthropic** is the default. Claude models have strong instruction-following and reliably return valid JSON when asked to — which matters here because the semantic layer depends entirely on parsing the model's response. Claude is also particularly good at reasoning about code structure and intent, not just surface patterns. The Haiku model is fast and cheap enough to run on every PR without meaningful cost.

**OpenAI** was added because a lot of teams already have GPT access through existing enterprise agreements or have OpenAI keys before they have Anthropic keys. GPT-4o-mini and the newer nano models are competitive on structured output and code analysis. Supporting both means you can use whatever you already have.

Providers like Gemini, Mistral, or Cohere could be added using the same pattern in `semantic.py` — the abstraction is a single function that takes a prompt and returns a string. The two current providers were prioritised because they have the most stable Python SDKs and the widest adoption.

---

## Model Reference

### Anthropic

| Model | Speed | Notes |
|---|---|---|
| `claude-haiku-4-5-20251001` | Fast | **Default.** Best cost/performance for routine PR review. |
| `claude-sonnet-4-6` | Medium | Better reasoning on complex multi-join models. |
| `claude-opus-4-6` | Slow | Highest quality. Worth it for a thorough review of a large refactor. |

### OpenAI

| Model | Speed | Notes |
|---|---|---|
| `gpt-4o-mini` | Fast | **Default.** May not be available on new or lower-tier accounts. |
| `gpt-4.1-nano` | Fast | Use this if `gpt-4o-mini` returns `insufficient_quota`. Available on tier 1. |
| `gpt-4.1-mini` | Medium | Stronger reasoning than nano. |
| `gpt-4.1` | Slow | Best OpenAI quality for SQL analysis. |

---

## Understanding the Output

```
==============================================================
  dbt Code Review Report
==============================================================
Files reviewed : 4
Total findings : 20
--------------------------------------------------------------
ERRORS  (15)
--------------------------------------------------------------
[ERROR] SELECT_STAR
  File    : models/staging/stg_customers.sql
  Line    : select * from prod.customers
  Message : SELECT * detected — enumerate columns explicitly to avoid schema drift

--------------------------------------------------------------
WARNINGS  (3)
--------------------------------------------------------------
[WARNING] JOIN_FANOUT
  File    : models/marts/customers.sql
  Message : Joining customers to orders without prior aggregation may multiply rows...

--------------------------------------------------------------
INFOS  (2)
--------------------------------------------------------------
[INFO] NAMING
  File    : models/staging/stg_orders.sql
  Message : Column alias 'dt' is ambiguous — rename to 'order_date' for clarity

==============================================================
  Summary
==============================================================
FAILED  — 15 error(s)  |  3 warning(s)  |  2 info(s)
==============================================================
```

- **ERRORS** — deterministic, always right. Exit code 1. Block the merge.
- **WARNINGS** — AI-generated. Exit code 0. Worth looking at but won't block CI.
- **INFOS** — AI-generated suggestions. Exit code 0.

---

## What Can Be Improved

**sqlglot is installed but unused.** The deterministic checks are pure regex. That means `SELECT *` in a SQL comment would trigger `SELECT_STAR`. sqlglot would parse the AST and skip it. It would also enable checks that are impossible with regex: type-mismatched comparisons, unbounded window frames, join cardinality analysis.

**Semantic checks run serially.** One API call per file, one after the other. On a PR with 10 changed files that's 10 sequential round-trips. `ThreadPoolExecutor` or `asyncio.gather` would bring total wait time down to roughly one call's latency.

**No retry logic.** A failed API call silently skips the file. The report gives no indication. Exponential backoff (2s, 4s, 8s, three attempts) plus a visible note in the report would fix this.

**Severity is hardcoded.** You can't currently say "treat SELECT_STAR as a warning in this project, not an error." A `.dbt-reviewer.yml` with per-rule severity overrides would cover this without changing the code.

**No `--output json` flag.** The output is terminal-only right now. A JSON mode would let you pipe findings into GitHub PR review comments, Slack alerts, or a dashboard.

**The full file is sent every time.** Even when one line changed. Sending only the diff hunk plus surrounding context would cut token usage 60–80% on incremental reviews.

**No `schema.yml` awareness.** The tool only looks at `.sql` files. A fuller review would also check whether renamed columns have matching entries in `schema.yml`, whether new `source()` calls have definitions in `sources.yml`, and whether any downstream `ref()` references would break.

---

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Clean, or warnings/info only. Safe to merge as far as this tool is concerned. |
| `1` | At least one error-severity finding. Use as a CI gate. |
| `2` | Tool error — diff file not found, unreadable input, etc. |
