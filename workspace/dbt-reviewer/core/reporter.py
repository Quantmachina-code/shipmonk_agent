"""Format review findings as a human-readable terminal report."""

from typing import List

from .deterministic import Finding
from .diff_parser import FileDiff

_WIDTH = 62
_SEVERITY_LABEL = {
    "error": "ERROR",
    "warning": "WARNING",
    "info": "INFO",
}
_SEVERITY_ORDER = ["error", "warning", "info"]


def _section(title: str) -> str:
    return f"{'-' * _WIDTH}\n{title}\n{'-' * _WIDTH}"


def format_report(file_diffs: List[FileDiff], findings: List[Finding]) -> str:
    lines: List[str] = []

    lines.append("=" * _WIDTH)
    lines.append("  dbt Code Review Report")
    lines.append("=" * _WIDTH)
    lines.append("")
    lines.append(f"Files reviewed : {len(file_diffs)}")
    lines.append(f"Total findings : {len(findings)}")
    lines.append("")

    if not findings:
        lines.append("No issues found. Looks good!")
        lines.append("")
        lines.append("=" * _WIDTH)
        return "\n".join(lines)

    for severity in _SEVERITY_ORDER:
        group = [f for f in findings if f.severity == severity]
        if not group:
            continue

        label = f"{_SEVERITY_LABEL[severity]}S  ({len(group)})"
        lines.append(_section(label))

        for finding in group:
            lines.append(f"[{_SEVERITY_LABEL[severity]}] {finding.rule}")
            lines.append(f"  File    : {finding.file}")
            if finding.line:
                snippet = finding.line[:80] + ("…" if len(finding.line) > 80 else "")
                line_label = (
                    f"Line {finding.line_number}" if finding.line_number is not None else "Line"
                )
                lines.append(f"  {line_label:<9}: {snippet}")
            lines.append(f"  Message : {finding.message}")
            lines.append("")

    lines.append("=" * _WIDTH)
    lines.append("  Summary")
    lines.append("=" * _WIDTH)

    error_count = sum(1 for f in findings if f.severity == "error")
    warn_count = sum(1 for f in findings if f.severity == "warning")
    info_count = sum(1 for f in findings if f.severity == "info")

    if error_count:
        lines.append(
            f"FAILED  — {error_count} error(s)  |  {warn_count} warning(s)  |  {info_count} info(s)"
        )
    else:
        lines.append(
            f"PASSED  — 0 errors  |  {warn_count} warning(s)  |  {info_count} info(s)"
        )
    lines.append("=" * _WIDTH)

    return "\n".join(lines)


def exit_code(findings: List[Finding]) -> int:
    """Return 1 when any error-severity finding is present, 0 otherwise."""
    return 1 if any(f.severity == "error" for f in findings) else 0
