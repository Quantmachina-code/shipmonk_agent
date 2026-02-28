# shipmonk_agent

A dbt SQL code reviewer that runs on git diffs. Feed it a diff, get back a structured list of findings grouped by severity. Works without an API key for the rule-based checks, and plugs into Claude or GPT for the deeper semantic analysis.

---

## Table of Contents

- [Repository Layout](#repository-layout)
- [Getting Started](#getting-started)
- [Tutorial: Adding New Checks](#tutorial-adding-new-checks)
  - [Part 1 — Adding a Deterministic Rule](#part-1--adding-a-deterministic-rule)
  - [Part 2 — Adding a Semantic Category](#part-2--adding-a-semantic-category)
  - [Part 3 — Adding a File-Level Check](#part-3--adding-a-file-level-check)
  - [Part 4 — When to Use Which Layer](#part-4--when-to-use-which-layer)
- [What Can Be Improved](#what-can-be-improved)
- [Further Reading](#further-reading)

---

## Repository Layout

```
shipmonk_agent/
├── README.md                          ← you are here
└── workspace/
    ├── dbt-reviewer/                  ← the CLI tool
    │   ├── reviewer.py                entry point
    │   ├── requirements.txt
    │   ├── core/
    │   │   ├── diff_parser.py         unified diff → FileDiff[]
    │   │   ├── deterministic.py       regex checks → Finding[]
    │   │   ├── semantic.py            LLM checks → Finding[]
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

# No API key needed — deterministic checks only
python reviewer.py \
  --diff ../jaffle_shop_sample/models/sample.diff \
  --no-semantic

# With Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
python reviewer.py --diff ../jaffle_shop_sample/models/sample.diff

# With OpenAI
pip install openai
export OPENAI_API_KEY=sk-proj-...
python reviewer.py \
  --diff ../jaffle_shop_sample/models/sample.diff \
  --provider openai \
  --model gpt-4.1-nano
```

Full CLI reference, all supported models, and the improvement roadmap are in `workspace/dbt-reviewer/README.md`.

---

## Tutorial: Adding New Checks

The reviewer has two independent layers. Deterministic rules run regex against the lines you added. Semantic categories are instructions you give to the model in plain text. You can add to either without touching the other.

---

### Part 1 — Adding a Deterministic Rule

All rules live in `workspace/dbt-reviewer/core/deterministic.py`. Each one is a plain function — no imports beyond `re`, no network calls, always runs.

**The pattern every rule follows:**

```
fd.added_lines  ←  only lines the author introduced in this diff
      │
      ▼
  regex match?
      │
      ▼
Finding(rule, severity, file, line, message)
```

Checking only `added_lines` means the tool never yells at you for issues that were already there before your PR.

The actual code takes a single `FileDiff` per function, and `run_all()` handles the loop:

```python
def run_all(file_diffs: List[FileDiff]) -> List[Finding]:
    findings: List[Finding] = []
    for fd in file_diffs:
        findings.extend(check_select_star(fd))
        findings.extend(check_hardcoded_schema(fd))
        findings.extend(check_missing_ref(fd))
        # your new rule goes here
    return findings
```

---

#### Example A — `SELECT DISTINCT` (usually a masked fanout bug)

**Step 1 — Add the pattern** near the other compiled regexes at the top of `deterministic.py`:

```python
_DISTINCT_RE = re.compile(r"\bselect\s+distinct\b", re.IGNORECASE)
```

**Step 2 — Write the function:**

```python
def check_select_distinct(file_diff: FileDiff) -> List[Finding]:
    findings: List[Finding] = []
    seen: set = set()
    for line in file_diff.added_lines:
        stripped = line.strip()
        if stripped in seen:
            continue
        if _DISTINCT_RE.search(stripped):
            seen.add(stripped)
            findings.append(Finding(
                rule="SELECT_DISTINCT",
                severity="warning",
                file=file_diff.filename,
                line=stripped,
                message=(
                    "SELECT DISTINCT often hides a JOIN_FANOUT problem — "
                    "fix the join instead of deduplicating the output."
                ),
            ))
    return findings
```

**Step 3 — Register it:**

```python
def run_all(file_diffs: List[FileDiff]) -> List[Finding]:
    findings: List[Finding] = []
    for fd in file_diffs:
        findings.extend(check_select_star(fd))
        findings.extend(check_hardcoded_schema(fd))
        findings.extend(check_missing_ref(fd))
        findings.extend(check_select_distinct(fd))   # ← new
    return findings
```

That's it. Rebuild nothing, restart nothing.

---

#### Example B — `LIMIT` inside a CTE (silently truncates everything downstream)

```python
_LIMIT_RE = re.compile(r"\blimit\s+\d+", re.IGNORECASE)

def check_limit_in_cte(file_diff: FileDiff) -> List[Finding]:
    findings: List[Finding] = []
    seen: set = set()
    lines = file_diff.added_lines
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped in seen:
            continue
        if not _LIMIT_RE.search(stripped):
            continue
        # Only flag it if there is another SELECT after this line
        # (meaning this LIMIT is inside a CTE, not on the final output)
        remaining = lines[idx + 1:]
        if any(re.search(r"\bselect\b", l, re.IGNORECASE) for l in remaining):
            seen.add(stripped)
            findings.append(Finding(
                rule="LIMIT_IN_CTE",
                severity="warning",
                file=file_diff.filename,
                line=stripped,
                message=(
                    "LIMIT inside a CTE truncates every model that reads from it. "
                    "Move the LIMIT to the final SELECT or remove it."
                ),
            ))
    return findings
```

---

#### Example C — `JOIN` without an `ON` clause (Cartesian product)

```python
_JOIN_RE = re.compile(r"\bjoin\b",  re.IGNORECASE)
_ON_RE   = re.compile(r"\bon\b",    re.IGNORECASE)

def check_cartesian_join(file_diff: FileDiff) -> List[Finding]:
    findings: List[Finding] = []
    seen: set = set()
    lines = file_diff.added_lines
    for idx, line in enumerate(lines):
        if not _JOIN_RE.search(line):
            continue
        next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
        if not _ON_RE.search(line + " " + next_line):
            stripped = line.strip()
            if stripped not in seen:
                seen.add(stripped)
                findings.append(Finding(
                    rule="CARTESIAN_JOIN",
                    severity="error",
                    file=file_diff.filename,
                    line=stripped,
                    message=(
                        "JOIN with no ON clause — every row times every row. "
                        "Add a join condition."
                    ),
                ))
    return findings
```

---

#### More ideas

| Rule | Severity | What to match | Why it matters |
|---|---|---|---|
| `SELECT_DISTINCT` | warning | `SELECT DISTINCT` | Masks fanout; fix the join |
| `LIMIT_IN_CTE` | warning | `LIMIT` before a downstream SELECT | Silently truncates |
| `CARTESIAN_JOIN` | error | `JOIN` with no `ON` | Full cross-product |
| `NULL_COMPARISON` | warning | `= NULL` or `!= NULL` | Always evaluates to NULL; use `IS NULL` |
| `NO_CTE` | warning | File has no `with … as (` | Business logic buried in one big SELECT |
| `DIRECT_SOURCE_IN_MART` | error | `source(` in a `marts/` path | Mart is bypassing the staging layer |
| `UNPREFIXED_TEMP_TABLE` | warning | `create temp table` | Temp tables leak between sessions |

---

### Part 2 — Adding a Semantic Category

Semantic categories live in `workspace/dbt-reviewer/prompts/review_prompt.txt` as plain text. The model reads those instructions and returns a JSON array. You never touch Python to add a new category.

The prompt currently defines four categories in this format:

```
1. **JOIN_FANOUT** (severity: "warning")
   One-liner description of what this catches.
   Look for:
   - bullet
   - bullet
```

---

#### Step 1 — Add a numbered block to the prompt file

Follow the exact same format. Example — adding `INCREMENTAL_RISK`:

```
5. **INCREMENTAL_RISK** (severity: "warning")
   Incremental model logic that can silently drop rows or re-process
   the entire table on every run.
   Look for:
   - config(materialized='incremental') with no {% if is_incremental() %} WHERE guard
   - WHERE clause filtering on a column that is not the incremental key
   - Missing unique_key in the config (causes full re-insert instead of upsert)
   - Late-arriving row scenarios not handled
```

#### Step 2 — Add the name to the allowed-values comment

Find this line near the bottom of the prompt:

```
"rule": "<one of: JOIN_FANOUT | NAMING | MODEL_STRUCTURE | PERFORMANCE>",
```

Change it to:

```
"rule": "<one of: JOIN_FANOUT | NAMING | MODEL_STRUCTURE | PERFORMANCE | INCREMENTAL_RISK>",
```

Next run the model will return findings like:

```json
[
  {
    "rule": "INCREMENTAL_RISK",
    "severity": "warning",
    "message": "Model has config(materialized='incremental') but no {% if is_incremental() %} filter — every run scans the full table."
  }
]
```

The reporter already handles any rule name, so it shows up in WARNINGS automatically.

---

#### More semantic category ideas

| Category | Severity | What to ask the model to find |
|---|---|---|
| `INCREMENTAL_RISK` | warning | No `is_incremental()` guard; missing `unique_key` |
| `TEST_COVERAGE` | info | New columns with no `not_null` or `unique` test in `schema.yml` |
| `DEPRECATED_LOGIC` | warning | References to archived tables or superseded business calculations |
| `WINDOW_FUNCTION` | info | Unbounded window frames (`ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING`) |
| `SECURITY` | error | PII columns (`email`, `ssn`, `card_number`) exposed unmasked in a mart |
| `IDEMPOTENCY` | warning | Non-deterministic functions like `NOW()` or `RANDOM()` that produce different results on each run |

---

### Part 3 — Adding a File-Level Check

Some rules need to look at the whole file, not just added lines. The diff parser reconstructs the new file content in `fd.new_content` — use that when the check requires full context.

Example — warn when a mart selects more than 20 columns:

```python
_MAX_COLUMNS = 20

def check_wide_mart(file_diff: FileDiff) -> List[Finding]:
    if "/marts/" not in file_diff.filename:
        return []

    # Grab everything between SELECT and FROM
    block = re.search(
        r"\bselect\b(.+?)\bfrom\b",
        file_diff.new_content,
        re.IGNORECASE | re.DOTALL,
    )
    if not block:
        return []

    columns = [c.strip() for c in block.group(1).split(",") if c.strip()]
    if len(columns) <= _MAX_COLUMNS:
        return []

    return [Finding(
        rule="WIDE_MART",
        severity="warning",
        file=file_diff.filename,
        line="",
        message=(
            f"This mart selects {len(columns)} columns — "
            f"consider splitting it into smaller, focused models "
            f"(threshold: {_MAX_COLUMNS})."
        ),
    )]
```

Note: the current sample diff won't trigger this because both mart models use `SELECT *` after the changes — there are no explicit columns to count. You'd need a model listing 20+ named columns to see it fire.

---

### Part 4 — When to Use Which Layer

| What you want to catch | Use |
|---|---|
| A specific keyword or pattern that is always wrong | Deterministic rule |
| Something that depends on context or intent | Semantic category |
| A structural property of the whole file | File-level deterministic rule using `fd.new_content` |
| Something that is wrong in *some* models but fine in others | Semantic — let the model decide |

The practical split: deterministic rules block the PR (exit code 1 on errors), semantic findings are surfaced as commentary (exit code 0). This means only clear-cut violations gate a merge, while the AI layer flags things worth a discussion.

---

## What Can Be Improved

**sqlglot is a dependency but never used.** The deterministic checks are pure regex, which means `SELECT *` in a SQL comment would trigger `SELECT_STAR`. sqlglot parses the actual AST, so it wouldn't. It would also unlock checks that regex can't express at all — unbounded window frames, type-mismatched comparisons, join cardinality.

**Semantic checks run one file at a time, sequentially.** On a big PR touching 10 files that's 10 serial API round-trips. Parallelising with `ThreadPoolExecutor` or `asyncio.gather` would bring total latency down to roughly one round-trip regardless of how many files changed.

**No retry logic.** If an API call fails the file is silently skipped and the report says nothing about it. Exponential backoff (three retries: 2s, 4s, 8s) would fix this for CI.

**Severity is hardcoded.** Some teams want `SELECT_STAR` to warn rather than block. Others want `NAMING` to fail CI. A `.dbt-reviewer.yml` in the project root with per-rule overrides would handle this without touching the code.

**No `--output json` flag.** Terminal output only makes it hard to feed findings into GitHub PR comments, Slack, or dashboards. A structured JSON output mode would make the tool composable.

**The prompt sends the full file on every call.** Even if one line changed. Sending only the diff hunk plus a few lines of context would cut token usage 60–80% on incremental reviews.

---

## Further Reading

- `workspace/dbt-reviewer/README.md` — CLI reference, model guide, improvement roadmap
- `workspace/dbt-reviewer/prompts/review_prompt.txt` — full prompt that governs all semantic checks
- `workspace/jaffle_shop_sample/models/sample.diff` — intentionally bad diff to test against
