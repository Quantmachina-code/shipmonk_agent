"""AI-powered semantic analysis of changed dbt models.

Supports both Anthropic (Claude) and OpenAI (GPT) as providers.
"""

import json
import os
import re
from pathlib import Path
from typing import List, Optional

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
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1:
        return text[start : end + 1]
    return text.strip()


def _call_anthropic(client, model: str, prompt: str) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _call_openai(client, model: str, prompt: str) -> str:
    response = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def run_semantic_checks(
    file_diffs: List[FileDiff],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    provider: str = "anthropic",
) -> List[Finding]:
    """Send each changed SQL file to an AI model and parse structured findings.

    Supports provider='anthropic' (Claude) or provider='openai' (GPT).
    Returns an empty list if the required API key is not set.
    """
    provider = provider.lower()

    if provider == "openai":
        resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
        default_model = "gpt-4o-mini"
        env_var = "OPENAI_API_KEY"
    else:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        default_model = "claude-haiku-4-5-20251001"
        env_var = "ANTHROPIC_API_KEY"

    effective_model = model or default_model

    if not resolved_key:
        print(
            f"Warning: {env_var} not set — skipping semantic checks. "
            f"Set the env var or pass --api-key to enable AI analysis."
        )
        return []

    if provider == "openai":
        try:
            from openai import OpenAI, OpenAIError
        except ImportError:
            print("Warning: openai package not installed. Run: pip install openai")
            return []
        client = OpenAI(api_key=resolved_key)
        call_fn = _call_openai
        api_error = OpenAIError
    else:
        try:
            import anthropic
        except ImportError:
            print("Warning: anthropic package not installed. Run: pip install anthropic")
            return []
        client = anthropic.Anthropic(api_key=resolved_key)
        call_fn = _call_anthropic
        api_error = anthropic.APIError

    prompt_template = _load_prompt()
    findings: List[Finding] = []

    for fd in file_diffs:
        if not fd.new_content.strip():
            continue

        prompt = prompt_template.replace("{filename}", fd.filename).replace(
            "{sql_content}", fd.new_content
        )

        try:
            raw = call_fn(client, effective_model, prompt)
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
            print(f"Warning: Could not parse AI response for {fd.filename}: {exc}")
        except api_error as exc:
            print(f"Warning: {provider.capitalize()} API error for {fd.filename}: {exc}")

    return findings
