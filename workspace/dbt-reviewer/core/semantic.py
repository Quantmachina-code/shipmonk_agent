"""Claude-powered semantic analysis of changed dbt models."""

import json
import os
import re
from pathlib import Path
from typing import List, Optional

import anthropic

from .deterministic import Finding
from .diff_parser import FileDiff

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "review_prompt.txt"

# Matches a JSON array inside optional ```json ... ``` fencing
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```")


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _extract_json(text: str) -> str:
    """Return the first JSON array found in text, stripping markdown fences."""
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        return fence.group(1).strip()
    # Try to find a raw JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1:
        return text[start : end + 1]
    return text.strip()


def run_semantic_checks(
    file_diffs: List[FileDiff],
    api_key: Optional[str] = None,
    model: str = "claude-haiku-4-5-20251001",
) -> List[Finding]:
    """Send each changed SQL file to Claude and parse structured findings.

    Returns an empty list if ANTHROPIC_API_KEY is not set.
    """
    resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not resolved_key:
        print(
            "Warning: ANTHROPIC_API_KEY not set — skipping semantic checks. "
            "Set the env var or pass --api-key to enable AI analysis."
        )
        return []

    client = anthropic.Anthropic(api_key=resolved_key)
    prompt_template = _load_prompt()

    findings: List[Finding] = []

    for fd in file_diffs:
        if not fd.new_content.strip():
            continue

        # Simple placeholder substitution — avoids issues with {{ }} in SQL
        prompt = prompt_template.replace("{filename}", fd.filename).replace(
            "{sql_content}", fd.new_content
        )

        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
            json_text = _extract_json(raw)
            raw_findings = json.loads(json_text)

            for rf in raw_findings:
                rule = rf.get("rule", "UNKNOWN")
                severity = rf.get("severity", "info")
                message = rf.get("message", "")
                if not message:
                    continue
                findings.append(
                    Finding(
                        rule=rule,
                        severity=severity,
                        file=fd.filename,
                        message=message,
                    )
                )

        except json.JSONDecodeError as exc:
            print(f"Warning: Could not parse Claude response for {fd.filename}: {exc}")
        except anthropic.APIError as exc:
            print(f"Warning: Anthropic API error for {fd.filename}: {exc}")

    return findings
