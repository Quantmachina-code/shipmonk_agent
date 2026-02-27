"""Rule-based dbt SQL checks that require no LLM."""

import re
from dataclasses import dataclass
from typing import List, Optional

from .diff_parser import FileDiff


@dataclass
class Finding:
    rule: str
    severity: str       # "error" | "warning" | "info"
    file: str
    message: str
    line: Optional[str] = None


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------

_JINJA_RE = re.compile(r"\{\{[^}]+\}\}")
_SELECT_STAR_RE = re.compile(r"\bselect\s+\*", re.IGNORECASE)
# Match schema.table or db.schema.table *inside* FROM / JOIN clauses
_FROM_JOIN_SCHEMA_RE = re.compile(
    r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)
# Direct dbt-model-looking names (stg_, fct_, dim_, int_, mart_, base_) without ref()
_DBT_MODEL_BARE_RE = re.compile(
    r"\b(?:from|join)\s+((?:stg|fct|dim|int|mart|base)_[a-zA-Z0-9_]+)\b",
    re.IGNORECASE,
)


def check_select_star(file_diff: FileDiff) -> List[Finding]:
    findings: List[Finding] = []
    seen: set[str] = set()
    for line in file_diff.added_lines:
        stripped = line.strip()
        if stripped in seen:
            continue
        if _SELECT_STAR_RE.search(stripped):
            seen.add(stripped)
            findings.append(
                Finding(
                    rule="SELECT_STAR",
                    severity="error",
                    file=file_diff.filename,
                    message="SELECT * detected — enumerate columns explicitly to avoid schema drift",
                    line=stripped,
                )
            )
    return findings


def check_hardcoded_schema(file_diff: FileDiff) -> List[Finding]:
    """Flag schema.table references inside FROM / JOIN that are not wrapped in Jinja."""
    findings: List[Finding] = []
    seen: set[str] = set()
    for line in file_diff.added_lines:
        stripped = line.strip()
        if stripped in seen:
            continue
        if stripped.startswith("--"):
            continue
        # Erase Jinja expressions so ref()/source() calls are invisible to the regex
        clean = _JINJA_RE.sub("__JINJA__", line)
        match = _FROM_JOIN_SCHEMA_RE.search(clean)
        if match:
            seen.add(stripped)
            ref = f"{match.group(1)}.{match.group(2)}"
            findings.append(
                Finding(
                    rule="HARDCODED_SCHEMA",
                    severity="error",
                    file=file_diff.filename,
                    message=(
                        f'Hardcoded schema reference "{ref}" — '
                        "use {{ ref() }} for dbt models or {{ source() }} for raw tables"
                    ),
                    line=stripped,
                )
            )
    return findings


def check_missing_ref(file_diff: FileDiff) -> List[Finding]:
    """Flag dbt model names used directly in FROM/JOIN without ref()."""
    findings: List[Finding] = []
    seen: set[str] = set()
    for line in file_diff.added_lines:
        stripped = line.strip()
        if stripped in seen:
            continue
        if "ref(" in line:
            continue
        clean = _JINJA_RE.sub("", line)
        for match in _DBT_MODEL_BARE_RE.finditer(clean):
            name = match.group(1)
            seen.add(stripped)
            findings.append(
                Finding(
                    rule="MISSING_REF",
                    severity="warning",
                    file=file_diff.filename,
                    message=(
                        f'Direct reference to dbt model "{name}" — '
                        f"use {{{{ ref('{name}') }}}} to track lineage"
                    ),
                    line=stripped,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all(file_diffs: List[FileDiff]) -> List[Finding]:
    findings: List[Finding] = []
    for fd in file_diffs:
        findings.extend(check_select_star(fd))
        findings.extend(check_hardcoded_schema(fd))
        findings.extend(check_missing_ref(fd))
    return findings
