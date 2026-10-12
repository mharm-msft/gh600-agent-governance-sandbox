"""A teaching `postToolUse` audit hook: record WHAT happened without recording secrets.

Why: after a failure you need a trail (which tool, when, how big, did it work), but audit logs
that capture full arguments and outputs become a secret store. This hook keeps the tool name,
the argument NAMES, short redacted previews, and sizes. It never fails the session: it prints
nothing to stdout and always exits 0.

Where the log goes: one JSON line per call, appended to `logs/agent-hook-audit.jsonl` (override
with AGENT_AUDIT_LOG) and echoed to stderr. In the cloud agent the sandbox filesystem is
EPHEMERAL, so the file disappears when the job ends. To keep it, send the same record to an
allow-listed HTTPS endpoint with an `http` hook, or upload an artifact from a workflow.
Where stderr output appears in the session log was not verified.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = ROOT / "logs" / "agent-hook-audit.jsonl"
PREVIEW_CHARS = 120
SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(r"(?i)(password|secret|token|api[_-]?key)\s*[=:]\s*\S+"),
]


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def preview(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = redact(text.replace("\n", " "))
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "..."


def build_record(payload: dict, now: float | None = None) -> dict:
    args = payload.get("toolArgs", payload.get("tool_input"))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args}
    result = payload.get("toolResult") or payload.get("tool_result") or {}
    text_result = ""
    if isinstance(result, dict):
        text_result = str(result.get("textResultForLlm") or result.get("text_result_for_llm") or "")
    return {
        "ts": round(now if now is not None else time.time(), 3),
        "session": payload.get("sessionId") or payload.get("session_id"),
        "tool": payload.get("toolName") or payload.get("tool_name"),
        "arg_names": sorted(args) if isinstance(args, dict) else [],
        "arg_preview": preview(args) if args is not None else "",
        "result_chars": len(text_result),
        "result_preview": preview(text_result) if text_result else "",
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            return 0
        line = json.dumps(build_record(payload), separators=(",", ":"))
        log_path = Path(os.environ.get("AGENT_AUDIT_LOG", DEFAULT_LOG))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        print(line, file=sys.stderr)
    except Exception as exc:  # an audit hook must never break the session
        print(f"hook_audit: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
