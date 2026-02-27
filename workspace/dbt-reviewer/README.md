# dbt Code Reviewer

A lightweight CLI tool that reviews dbt SQL model changes in pull requests and local branches. It combines fast, deterministic rule checks with optional AI-powered semantic analysis to catch common dbt anti-patterns before they reach production.

---

## Table of Contents

- [What It Does](#what-it-does)
- [How It Works](#how-it-works)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [CLI Reference](#cli-reference)
- [Running Without AI (Deterministic Only)](#running-without-ai-deterministic-only)
- [AI Providers and Models](#ai-providers-and-models)
- [Understanding the Output](#understanding-the-output)
- [Adding New Checks](#adding-new-checks)
  - [Adding a Deterministic Rule](#adding-a-deterministic-rule)
  - [Adding a Semantic Category](#adding-a-semantic-category)
- [What Can Be Improved](#what-can-be-improved)
- [Exit Codes](#exit-codes)

---

## What It Does

The reviewer reads a unified git diff, extracts changed `.sql` files, and runs two layers of checks:

**Layer 1 — Deterministic checks** (always run, no API key needed):

| Rule | Severity | What it catches |
|---|---|---|
| `SELECT_STAR` | error | `SELECT *` usage — causes schema drift risk |
| `HARDCODED_SCHEMA` | error | `prod.table` references instead of `{{ ref() }}` or `{{ source() }}` |
| `MISSING_REF` | warning | dbt model names (`stg_`, `fct_`, `dim_`, etc.) referenced without `{{ ref() }}` |

**Layer 2 — Semantic checks** (requires an API key, skippable with `--no-semantic`):

| Rule | Severity | What it catches |
|---|---|---|
| `JOIN_FANOUT` | warning | Joins where the right-hand table is not unique on the join key, causing row multiplication |
| `NAMING` | info | Ambiguous or abbreviated column/alias names (`dt`, `flg`, `cust_id`, `amt`) |
| `MODEL_STRUCTURE` | warning | Staging models with no transformation, mart models reading raw tables, missing CTE structure |
| `PERFORMANCE` | warning | Cartesian products, repeated subqueries, missing filter push-downs |

---

## How It Works

```
git diff (unified format)
        │
        ▼
  diff_parser.py          Parses the diff into FileDiff objects.
  (FileDiff[])            Each object holds: filename, added lines,
                          removed lines, and reconstructed new content.
        │
        ├──► deterministic.py    Regex + pattern matching on added lines.
        │    (Finding[])         No network calls. Always runs.
        │
        └──► semantic.py         Sends each changed SQL file to an LLM.
             (Finding[])         Parses structured JSON findings from response.
                                 Skipped if --no-semantic or no API key.
        │
        ▼
   reporter.py            Groups findings by severity, formats terminal
                          output, determines exit code (0 = pass, 1 = fail).
```

Deterministic checks run against **added lines only** — lines the author introduced. This avoids noise from pre-existing issues in unchanged code.

Semantic checks send the **full reconstructed file content** to the model so it can reason about the SQL as a whole (join logic, CTE structure, naming patterns).

---

## Installation

```bash
# Clone the repo and enter the project directory
cd workspace/dbt-reviewer

# Install dependencies into a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# If using OpenAI as provider, also install:
pip install openai
```

**Dependencies:**
- `anthropic>=0.20.0` — Anthropic API client (Claude models)
- `sqlglot>=20.0.0` — SQL parsing library
- `openai` — optional, only needed for `--provider openai`

---

## Quick Start

### From a diff file

```bash
python reviewer.py \
  --diff ../jaffle_shop_sample/models/sample.diff \
  --project ../jaffle_shop_sample \
  --provider openai \
  --model gpt-4.1-nano
```

### Piped directly from git

```bash
git diff HEAD~1 | python reviewer.py --project .
```

### In a CI pipeline (GitHub Actions example)

```yaml
- name: Review dbt changes
  run: |
    git diff origin/main...HEAD | python reviewer.py \
      --project . \
      --provider anthropic
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

---

## CLI Reference

| Argument | Default | Description |
|---|---|---|
| `--diff FILE` | stdin | Path to a unified diff file. If omitted, reads from stdin (pipe from `git diff`). |
| `--project DIR` | — | Path to the dbt project root. Passed for context; does not affect parsing. |
| `--no-semantic` | off | Skip AI checks entirely. Only deterministic rules run. No API key required. |
| `--provider` | `anthropic` | AI provider: `anthropic` or `openai`. |
| `--api-key KEY` | env var | Explicit API key. Falls back to `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` env var. |
| `--model MODEL` | see below | Model name override. See [AI Providers and Models](#ai-providers-and-models). |

**Setting your API key (recommended):**

```bash
# Set once for the session — no need to inline it every command
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-proj-...
```

---

## Running Without AI (Deterministic Only)

The tool works fully without any API key. Use `--no-semantic` to skip the AI layer:

```bash
python reviewer.py \
  --diff changes.diff \
  --project . \
  --no-semantic
```

This is useful when:
- You have no API key yet and want to test the tool
- You are running in CI and want fast, zero-cost checks
- You only care about hard errors (`SELECT_STAR`, `HARDCODED_SCHEMA`)

The deterministic layer alone catches the most critical anti-patterns and is suitable as a hard gate in CI pipelines (`exit code 1` on any error).

---

## AI Providers and Models

The tool supports two providers. You can use either depending on what API access you have.

### Anthropic (default)

```bash
python reviewer.py --diff changes.diff --provider anthropic
```

| Model | Speed | Quality | Notes |
|---|---|---|---|
| `claude-haiku-4-5-20251001` | Fast | Good | **Default.** Best cost/quality balance for code review. |
| `claude-sonnet-4-6` | Medium | Better | Higher quality reasoning on complex SQL. |
| `claude-opus-4-6` | Slow | Best | Highest accuracy; use for thorough reviews on large files. |

### OpenAI

```bash
python reviewer.py --diff changes.diff --provider openai --model gpt-4.1-nano
```

| Model | Speed | Quality | Notes |
|---|---|---|---|
| `gpt-4o-mini` | Fast | Good | **Default.** Not available on all account tiers. |
| `gpt-4.1-nano` | Fast | Good | Recommended if `gpt-4o-mini` returns 429 errors. |
| `gpt-4.1-mini` | Medium | Better | Stronger reasoning than nano. |
| `gpt-4.1` | Slow | Best | Highest OpenAI quality for SQL analysis. |

> **Note on 429 errors with OpenAI:** The default model (`gpt-4o-mini`) may not be available on new or lower-tier accounts. If you see `insufficient_quota` errors, switch to `--model gpt-4.1-nano`, which is available on tier 1.

### Choosing between providers

- If you already have an Anthropic API key, use `--provider anthropic` with the default model — no extra install needed.
- If you have an OpenAI key, run `pip install openai` first and use `--model gpt-4.1-nano` to avoid quota issues.
- For CI pipelines, Anthropic's Haiku model is fast and cheap. OpenAI's nano models are comparable.

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

...

--------------------------------------------------------------
WARNINGS  (3)
--------------------------------------------------------------
[WARNING] JOIN_FANOUT
  File    : models/marts/customers.sql
  Message : Joining customers to orders without aggregation may cause row duplication...

--------------------------------------------------------------
INFOS  (2)
--------------------------------------------------------------
[INFO] NAMING
  File    : models/staging/stg_orders.sql
  Message : Column alias 'dt' is ambiguous — consider renaming to 'order_date' for clarity

==============================================================
  Summary
==============================================================
FAILED  — 15 error(s)  |  3 warning(s)  |  2 info(s)
==============================================================
```

- **ERRORS** → deterministic, rule-based. Exit code `1`. Block the PR.
- **WARNINGS** → semantic (AI-generated). Exit code `0`. Flag for review.
- **INFOS** → semantic (AI-generated). Exit code `0`. Suggestions only.

Only errors trigger a non-zero exit code, so warnings and info findings do not break CI.

---

## Adding New Checks

### Adding a Deterministic Rule

Deterministic rules live in `core/deterministic.py`. Each rule is a function that takes a `FileDiff` and returns a list of `Finding` objects. To add a new rule:

**Step 1 — Define a regex pattern** at the top of the file:

```python
# Example: catch DISTINCT used as a performance workaround
_DISTINCT_RE = re.compile(r"\bselect\s+distinct\b", re.IGNORECASE)
```

**Step 2 — Write the check function:**

```python
def check_select_distinct(file_diffs: List[FileDiff]) -> List[Finding]:
    findings = []
    seen = set()
    for fd in file_diffs:
        for line in fd.added_lines:
            if _DISTINCT_RE.search(line):
                key = (fd.filename, line.strip())
                if key not in seen:
                    seen.add(key)
                    findings.append(Finding(
                        rule="SELECT_DISTINCT",
                        severity="warning",
                        file=fd.filename,
                        line=line.strip(),
                        message=(
                            "SELECT DISTINCT detected — this often masks a JOIN_FANOUT bug. "
                            "Fix the root cause instead of deduplicating the result."
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
    findings.extend(check_select_distinct(file_diffs))   # ← add here
    return findings
```

**More rule ideas to implement:**

| Rule | Severity | What to detect |
|---|---|---|
| `SELECT_DISTINCT` | warning | `SELECT DISTINCT` masking a fanout bug |
| `LIMIT_IN_CTE` | warning | `LIMIT` inside a CTE (silently truncates downstream) |
| `NO_CTE` | warning | Model has no CTE — mixing logic into a single SELECT |
| `DIRECT_TABLE_IN_MART` | error | Mart model references `source()` or raw schema directly |
| `DEPRECATED_REF_STYLE` | info | `ref('model')` without schema argument (project-specific) |
| `MISSING_UNIQUE_KEY_TEST` | info | New mart model without a corresponding `schema.yml` test file |
| `CARTESIAN_JOIN` | error | `JOIN` clause with no `ON` condition |
| `NULL_COMPARISON` | warning | `= NULL` instead of `IS NULL` |
| `IMPLICIT_CAST` | info | Comparing columns of mismatched types (string vs integer) |

---

### Adding a Semantic Category

Semantic categories are defined in `prompts/review_prompt.txt`. The model follows the instructions in that file and returns structured JSON.

**Step 1 — Add a new category block** to `prompts/review_prompt.txt`:

```
5. **DEPRECATED_LOGIC** (severity: "warning")
   Business logic that is outdated or replaced by a newer approach.
   Look for: references to archived tables, deprecated column names listed
   in the project's known issues, superseded calculation methods.
```

**Step 2 — Add the rule name to the allowed values** in the JSON schema section:

```
"rule": "<one of: JOIN_FANOUT | NAMING | MODEL_STRUCTURE | PERFORMANCE | DEPRECATED_LOGIC>",
```

That is all. The model will start returning findings with `"rule": "DEPRECATED_LOGIC"` and the reporter will display them automatically. No Python changes required.

**Semantic category ideas to add:**

| Category | Severity | What to ask the model to detect |
|---|---|---|
| `DEPRECATED_LOGIC` | warning | Outdated business calculations or archived table references |
| `TEST_COVERAGE` | info | New columns added without corresponding `not_null` or `unique` tests |
| `INCREMENTAL_RISK` | warning | Incremental model missing a proper `is_incremental()` filter |
| `WINDOW_FUNCTION` | info | Unbounded window frame that could cause performance issues |
| `DOCUMENTATION` | info | Public mart model missing column descriptions |

---

## What Can Be Improved

### 1. Token usage and prompt efficiency

Currently the full reconstructed file content is sent to the model on every run. For large files this increases cost and latency. Improvements:

- **Send only the diff chunk** (added lines + surrounding context) rather than the full file. This reduces token usage by up to 80% on incremental changes.
- **Truncate files** above a configurable token limit and warn the user.
- **Batch multiple small files** into a single API call to reduce round-trips.

### 2. Rate limiting and retries

The semantic layer makes one API call per file sequentially with no retry logic. If a call fails (network hiccup, transient 429), the file is silently skipped. Improvements:

- Add exponential backoff retry (e.g., 3 retries: 2s, 4s, 8s).
- Add a configurable `--concurrency` flag to send calls in parallel (using `asyncio` or `ThreadPoolExecutor`), which would significantly speed up reviews of large PRs.
- Surface skipped files clearly in the report summary.

### 3. Configurable severity levels

Currently severity is hardcoded in the prompt and in each deterministic rule. Teams have different tolerances — some may want `SELECT_STAR` to be a warning rather than a blocker. Improvements:

- Add a `reviewer.yml` config file where severity overrides can be set per rule.
- Support `.dbt-reviewer.yml` at the project root so teams can version-control their preferences.

### 4. GitHub / GitLab PR integration

The tool currently only outputs to the terminal. A PR-native integration would enable inline comments on the changed lines. Improvements:

- Add a `--output json` flag to emit machine-readable findings.
- Build a thin GitHub Action wrapper that posts findings as PR review comments using the GitHub Checks API.
- Add a `--baseline` flag to compare findings against the base branch, so only net-new issues are reported.

### 5. sqlglot is imported but not used

`sqlglot` is listed in `requirements.txt` and is a powerful SQL AST parser, but the deterministic checks currently use plain regex. Using sqlglot would make checks more accurate and reduce false positives. For example:

- `SELECT_STAR` via regex can match `SELECT *` in a comment; sqlglot parses the AST and would not.
- `JOIN_FANOUT` could be detected deterministically by inspecting the join tree for missing aggregations.
- Type mismatch comparisons, implicit casts, and unbounded window frames could all be caught without an LLM.

### 6. Support for `schema.yml` and `sources.yml`

The tool currently only looks at `.sql` files in the diff. A complete review would also check:

- Whether new columns in a model have corresponding entries in `schema.yml`.
- Whether a new `{{ source() }}` call has a matching definition in `sources.yml`.
- Whether a renamed column breaks any downstream `ref()` references.

### 7. Model context injection

The semantic prompt currently has no knowledge of the broader dbt project (other models, sources, tests). Injecting a summary of the project graph into the prompt would enable much richer analysis — for example, detecting when a mart model is being referenced directly by another mart (breaking the layering convention).

---

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | No errors found. Warnings and info findings may exist but do not block. |
| `1` | One or more error-severity findings. Use this as a CI gate. |
| `2` | Tool error (e.g., diff file not found). |
