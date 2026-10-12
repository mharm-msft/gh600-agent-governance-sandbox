"""Lint GitHub Copilot customization files against the rules GitHub documents.

What it checks
  * custom agents   .github/agents/*.md     frontmatter, tool least privilege, read-only roles
  * hooks           .github/hooks/*.json    version, event names, keys the cloud agent honors
  * MCP settings    any JSON via --mcp      required keys, secret handling, wildcard tools
  * setup steps     .github/workflows/copilot-setup-steps.yml, or any file via --setup-steps
                                            job name, honored keys, timeout, runner, secrets

Why a linter at all: GitHub silently IGNORES unrecognized tool names and unsupported keys. A
typo in a `tools:` list does not fail. It quietly changes what the agent can do. This catches
that before merge. It models GitHub's *documented* rules (checked 2026-10-09); GitHub's own
behavior is the source of truth, so re-test anything important in a real session.

Exit codes: 0 = no errors, 1 = errors found (or warnings with --strict), 2 = bad usage.

Examples:
    python scripts/lint_agent_config.py
    python scripts/lint_agent_config.py --mcp path/to/mcp-settings.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from labcommon import split_front_matter

ROOT = Path(__file__).resolve().parents[1]

TOOL_ALIASES = {
    "execute", "shell", "bash", "powershell",
    "read", "notebookread",
    "edit", "multiedit", "write", "notebookedit",
    "search", "grep", "glob",
    "agent", "custom-agent", "task",
    "web", "todo",
}
IDE_ONLY_TOOLS = {"web", "todo"}
WRITE_CAPABLE = {"execute", "shell", "bash", "powershell", "edit", "multiedit", "write", "notebookedit"}
READ_ONLY_NAME_HINTS = ("plan", "review")
READ_ONLY_ROLES = {"planner", "reviewer", "read-only"}
# A prompt that CLAIMS the agent cannot change files. "Do not change files outside X" is a scope rule,
# not a read-only claim, so a scope word right after the noun cancels the match.
READ_ONLY_CLAIM = re.compile(
    r"read[- ]only"
    r"|\bonly\s+reads?\b"
    r"|\b(?:never|must\s+not|do\s+not|don't|cannot|can't|not\s+allowed\s+to)\s+(?:\w+\s+)?"
    r"(?:change|edit|modify|write|touch|alter)s?\s+(?:any\s+|the\s+|your\s+)?(?:files?|code|anything|repo(?:sitory)?|source)\b"
    r"(?!\s+(?:outside|other|except|unless|unrelated|beyond|under|in|within|inside|that|which|not|without)\b)",
    re.IGNORECASE,
)
# Names the CLI and SDK use for tools. Custom agent profiles document the aliases instead.
SDK_TOOL_NAMES = {"view": "read"}
MAX_PROMPT_CHARS = 30_000

SETUP_JOB = "copilot-setup-steps"
SETUP_HONORED_KEYS = {"steps", "permissions", "runs-on", "services", "snapshot", "timeout-minutes"}
SETUP_MAX_TIMEOUT_MINUTES = 59
ACTIONS_SECRET = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")
FILENAME_OK = re.compile(r"^[A-Za-z0-9._-]+$")

HOOK_EVENTS = {
    "sessionStart", "sessionEnd", "userPromptSubmitted", "userPromptTransformed", "preToolUse",
    "postToolUse", "postToolUseFailure", "errorOccurred", "agentStop", "subagentStart",
    "subagentStop", "permissionRequest", "notification", "preCompact",
}
HOOK_EVENTS_PASCAL = {e[0].upper() + e[1:] for e in HOOK_EVENTS} | {"Stop", "SubagentStop"}
NO_EFFECT_ON_CLOUD_AGENT = {
    "permissionRequest": "tool calls are pre-approved in the cloud agent; use preToolUse to decide",
    "notification": "the cloud agent does not surface notifications, so this never fires",
}

MCP_TYPES = {"local", "stdio", "http", "sse"}
SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
]
VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}|\$([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class Finding:
    severity: str  # "ERROR" or "WARN"
    file: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity:<5} {self.file}: {self.message}"


def normalize_tools(value: object) -> list[str] | None:
    """Return the tool list, or None when the property is unset (which means ALL tools)."""
    if value is None:
        return None
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        items = [str(part).strip() for part in value]
    else:
        raise ValueError("'tools' must be a list or a comma-separated string")
    return [item for item in items if item]


def lint_agent(name: str, text: str) -> list[Finding]:
    """Lint one custom agent profile (.md or .agent.md)."""
    findings: list[Finding] = []

    def add(severity: str, message: str) -> None:
        findings.append(Finding(severity, name, message))

    stem = Path(name).name
    for suffix in (".agent.md", ".md"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if not FILENAME_OK.match(stem):
        add("ERROR", "file name may only use letters, digits, '.', '-' and '_'")

    try:
        meta, body = split_front_matter(text)
    except ValueError as exc:
        return [Finding("ERROR", name, str(exc))]

    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        add("ERROR", "'description' is required and must be a non-empty string")
    if len(body) > MAX_PROMPT_CHARS:
        add("ERROR", f"prompt body is {len(body)} characters; the documented maximum is {MAX_PROMPT_CHARS}")
    if not body.strip():
        add("WARN", "the prompt body is empty, so the agent has no instructions")
    if "infer" in meta:
        add("WARN", "'infer' is retired; use 'disable-model-invocation' and 'user-invocable'")
    for key in ("argument-hint", "handoffs"):
        if key in meta:
            add("WARN", f"'{key}' is ignored by the cloud agent on GitHub.com")
    if "applyTo" in meta:
        add("WARN", "'applyTo' is documented for .instructions.md files, not agent profiles; "
                    "Learn's custom-agent example uses it, GitHub's reference does not list it")
    if meta.get("target") not in (None, "vscode", "github-copilot"):
        add("ERROR", "'target' must be 'vscode' or 'github-copilot' when set")
    for key in ("disable-model-invocation", "user-invocable"):
        if key in meta and not isinstance(meta[key], bool):
            add("ERROR", f"'{key}' must be true or false")
    if "model" in meta and not isinstance(meta["model"], str):
        add("ERROR", "'model' must be a string")

    metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    declared_role = str(metadata.get("role", "")).lower() in READ_ONLY_ROLES
    claim = READ_ONLY_CLAIM.search(f"{description if isinstance(description, str) else ''}\n{body}")
    # "Claims" means the profile itself says it cannot change files. That is what the exam tests:
    # words that promise one thing while the tool list allows another. A name alone is only a hint.
    claims_read_only = declared_role or claim is not None
    named_like_read_only = any(hint in stem.lower() for hint in READ_ONLY_NAME_HINTS)
    if declared_role:
        why = f"metadata.role is '{metadata.get('role')}'"
    elif claim is not None:
        why = f"its prompt says \"{' '.join(claim.group(0).split())[:60]}\""
    else:
        why = ""

    try:
        tools = normalize_tools(meta.get("tools"))
    except ValueError as exc:
        add("ERROR", str(exc))
        return findings

    if tools is None or "*" in tools:
        if claims_read_only or named_like_read_only:
            add("ERROR", f"'{stem}' looks like a read-only role but has ALL tools enabled "
                         "(tools omitted or '*'). Name the read-only tools explicitly.")
        else:
            add("WARN", "all tools are enabled; prefer an explicit allowlist (least privilege)")
        return findings

    lowered = [tool.lower() for tool in tools]
    for tool, low in zip(tools, lowered):
        if "/" in tool or low in TOOL_ALIASES:
            if low in IDE_ONLY_TOOLS:
                add("WARN", f"tool '{tool}' has no effect on the cloud agent (IDE only)")
            continue
        if low in SDK_TOOL_NAMES:
            add("WARN", f"tool '{tool}' is the CLI/SDK name for '{SDK_TOOL_NAMES[low]}'. Custom agent profiles "
                        f"document the alias '{SDK_TOOL_NAMES[low]}'; use it unless you have verified '{tool}' works in your target")
            continue
        add("WARN", f"unrecognized tool '{tool}' is silently ignored by GitHub. Typo, or a product-specific tool?")
    risky = sorted({tool for tool, low in zip(tools, lowered) if low in WRITE_CAPABLE})
    if risky and claims_read_only:
        add("ERROR", f"read-only role '{stem}' lists write-capable tools: {', '.join(risky)} ({why})")
    elif risky and named_like_read_only:
        add("WARN", f"'{stem}' is named like a read-only role but lists write-capable tools: {', '.join(risky)}. "
                    "That can be right when it must write a plan file (GitHub's own implementation-planner example does), "
                    "but then say so in the prompt, review what it writes, and keep protected paths protected")
    return findings


def lint_setup_steps(name: str, text: str) -> list[Finding]:
    """Lint a copilot-setup-steps workflow against the rules GitHub documents for it."""
    import yaml  # lazy, like labcommon: only this linter needs it

    findings: list[Finding] = []

    def add(severity: str, message: str) -> None:
        findings.append(Finding(severity, name, message))

    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [Finding("ERROR", name, f"not valid YAML: {str(exc).splitlines()[0]}")]
    jobs = document.get("jobs") if isinstance(document, dict) else None
    if not isinstance(jobs, dict) or not jobs:
        return [Finding("ERROR", name, "no 'jobs' mapping found")]

    if SETUP_JOB not in jobs:
        found = ", ".join(sorted(str(key) for key in jobs))
        add("ERROR", f"no job named '{SETUP_JOB}' (found: {found}). GitHub only uses a job with exactly that name, "
                     "so nothing would be set up, and nothing would fail either")
        return findings
    others = sorted(str(key) for key in jobs if key != SETUP_JOB)
    if others:
        add("WARN", f"only the '{SETUP_JOB}' job is used; these jobs are ignored: {', '.join(others)}")

    job = jobs[SETUP_JOB]
    if not isinstance(job, dict):
        return findings + [Finding("ERROR", name, f"job '{SETUP_JOB}' must be a mapping")]
    ignored = sorted(str(key) for key in job if key not in SETUP_HONORED_KEYS)
    if ignored:
        add("WARN", f"job key(s) {', '.join(ignored)} are ignored; GitHub honors only {', '.join(sorted(SETUP_HONORED_KEYS))}")
    if not isinstance(job.get("steps"), list) or not job.get("steps"):
        add("WARN", "the job has no steps, so it prepares nothing")

    timeout = job.get("timeout-minutes")
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > SETUP_MAX_TIMEOUT_MINUTES:
        add("ERROR", f"timeout-minutes is {timeout}; the documented maximum is {SETUP_MAX_TIMEOUT_MINUTES}")

    runs_on = job.get("runs-on")
    for label in (runs_on if isinstance(runs_on, list) else [runs_on]):
        low = str(label).lower()
        if low.startswith("macos"):
            add("ERROR", f"runs-on '{label}': the cloud agent has no macOS runners")
        elif low.startswith("windows"):
            add("WARN", f"runs-on '{label}': Windows is documented only for larger or self-hosted runners")

    permissions = job.get("permissions")
    if permissions == "write-all" or (isinstance(permissions, dict) and "write" in {str(v).lower() for v in permissions.values()}):
        add("WARN", "the setup job's token has write access. Setup steps rarely need it: use the least permissions that work")

    secrets = sorted({s for s in ACTIONS_SECRET.findall(yaml.safe_dump(job.get("steps") or [])) if s != "GITHUB_TOKEN"})
    if secrets:
        add("WARN", f"steps use secrets.{', secrets.'.join(secrets)}. Actions secrets do not reach the agent's setup steps "
                    "or sessions; only Agents secrets and variables do, so this would be empty unless you also created one")
    return findings


def lint_hooks(name: str, text: str) -> list[Finding]:
    """Lint one .github/hooks/*.json file."""
    findings: list[Finding] = []

    def add(severity: str, message: str) -> None:
        findings.append(Finding(severity, name, message))

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [Finding("ERROR", name, f"invalid JSON: {exc}")]
    if not isinstance(data, dict):
        return [Finding("ERROR", name, "top level must be a JSON object")]
    if data.get("version") != 1:
        add("ERROR", "'version' must be 1")
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        add("ERROR", "'hooks' must be an object keyed by event name")
        return findings

    for event, entries in hooks.items():
        if event not in HOOK_EVENTS and event not in HOOK_EVENTS_PASCAL:
            add("WARN", f"unknown hook event '{event}'")
        if event in NO_EFFECT_ON_CLOUD_AGENT:
            add("WARN", f"'{event}': {NO_EFFECT_ON_CLOUD_AGENT[event]}")
        if not isinstance(entries, list):
            add("ERROR", f"'{event}' must be an array of hook entries")
            continue
        for index, entry in enumerate(entries):
            where = f"{event}[{index}]"
            if not isinstance(entry, dict):
                add("ERROR", f"{where} must be an object")
                continue
            kind = entry.get("type", "command")
            timeout = entry.get("timeoutSec", entry.get("timeout"))
            if timeout is not None and (not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0):
                add("ERROR", f"{where}: timeout must be a positive number of seconds")
            if kind == "command":
                honored = entry.get("bash") or entry.get("command")
                if not honored:
                    if entry.get("powershell") or entry.get("exec"):
                        add("WARN", f"{where}: only powershell/exec is set. The cloud agent runs Linux and honors only bash/command, so this entry is ignored there")
                    else:
                        add("ERROR", f"{where}: a command hook needs bash, powershell or command")
                if "exec" in entry and any(key in entry for key in ("bash", "powershell", "command")):
                    add("ERROR", f"{where}: do not combine 'exec' with bash/powershell/command")
                if event == "preToolUse" and isinstance(timeout, (int, float)) and timeout > 30:
                    add("WARN", f"{where}: a command hook that TIMES OUT fails open (the tool call proceeds). Keep guards fast")
            elif kind == "http":
                url = entry.get("url")
                if not isinstance(url, str) or not url:
                    add("ERROR", f"{where}: an http hook needs 'url'")
                elif event in {"preToolUse", "permissionRequest"} and not url.startswith("https://"):
                    add("ERROR", f"{where}: {event} http hooks must use https://")
                if event == "preToolUse":
                    add("WARN", f"{where}: http preToolUse hooks fail OPEN on network errors; use a command hook if you need fail-closed")
            elif kind == "prompt":
                if event != "sessionStart":
                    add("ERROR", f"{where}: prompt hooks are only supported on sessionStart")
                else:
                    add("WARN", f"{where}: cloud agent jobs are non-interactive, so prompt hooks may not fire")
            else:
                add("ERROR", f"{where}: unknown hook type '{kind}'")
    return findings


def _walk_strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)


def lint_mcp(name: str, text: str) -> list[Finding]:
    """Lint a cloud-agent MCP configuration (the JSON pasted into repository settings)."""
    findings: list[Finding] = []

    def add(severity: str, message: str) -> None:
        findings.append(Finding(severity, name, message))

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [Finding("ERROR", name, f"invalid JSON: {exc}")]
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        found = ", ".join(data) if isinstance(data, dict) and data else "nothing"
        return [Finding("ERROR", name, f"top-level 'mcpServers' object is required (found: {found})")]

    for server_name, server in servers.items():
        label = f"server '{server_name}'"
        if not isinstance(server, dict):
            add("ERROR", f"{label} must be an object")
            continue
        kind = server.get("type")
        if kind not in MCP_TYPES:
            add("ERROR", f"{label}: 'type' is required and must be one of {sorted(MCP_TYPES)}")
        tools = server.get("tools")
        if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
            add("ERROR", f"{label}: 'tools' is required and must be a list of tool names")
        elif "*" in tools:
            add("WARN", f"{label}: tools ['*'] exposes every tool. MCP tools run without an approval prompt, so allowlist specific, preferably read-only, tools")
        if kind in {"local", "stdio"}:
            if not isinstance(server.get("command"), str) or not server.get("command"):
                add("ERROR", f"{label}: a local server needs 'command'")
            if not isinstance(server.get("args"), list):
                add("ERROR", f"{label}: a local server needs 'args' (use [] for none)")
        url = server.get("url")
        if kind in {"http", "sse"} and (not isinstance(url, str) or not url):
            add("ERROR", f"{label}: a remote server needs 'url'")
        if isinstance(url, str) and url.startswith("http://") and not re.match(r"http://(localhost|127\.)", url):
            add("WARN", f"{label}: non-TLS URL {url}")

        own_env = server.get("env") if isinstance(server.get("env"), dict) else {}
        for section in ("env", "headers", "args", "url"):
            for text_value in _walk_strings(server.get(section)):
                if not VAR_REF.search(text_value) and any(p.search(text_value) for p in SECRET_PATTERNS):
                    add("ERROR", f"{label}: '{section}' contains what looks like a literal secret. Use a $COPILOT_MCP_* reference")
                for match in VAR_REF.finditer(text_value):
                    var = match.group(1) or match.group(2)
                    if not var.startswith("COPILOT_MCP_") and var not in own_env:
                        add("WARN", f"{label}: '${var}' will not resolve. Only COPILOT_MCP_* secrets/variables (or this server's own env keys) reach MCP servers")
    return findings


def _label(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_absolute() and root in path.parents else path.as_posix()


def misnamed_setup_files(root: Path) -> list[Finding]:
    """A workflow that defines the setup job under another file name is never used."""
    import yaml

    findings: list[Finding] = []
    workflows = root / ".github" / "workflows"
    for path in sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")]):
        if path.name == "copilot-setup-steps.yml":
            continue
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError):
            continue
        jobs = document.get("jobs") if isinstance(document, dict) else None
        if isinstance(jobs, dict) and SETUP_JOB in jobs:
            findings.append(Finding("WARN", _label(root, path), f"defines the '{SETUP_JOB}' job, but GitHub documents the "
                                                                "file name copilot-setup-steps.yml, so this may never be used"))
    return findings


def collect(root: Path, mcp_files: list[Path], setup_files: list[Path] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted((root / ".github" / "agents").glob("*.md")):
        findings += lint_agent(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
    for path in sorted((root / ".github" / "hooks").glob("*.json")):
        findings += lint_hooks(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
    setup = root / ".github" / "workflows" / "copilot-setup-steps.yml"
    if setup.is_file():
        findings += lint_setup_steps(_label(root, setup), setup.read_text(encoding="utf-8"))
    findings += misnamed_setup_files(root)
    for path in setup_files or []:
        findings += lint_setup_steps(_label(root, path), path.read_text(encoding="utf-8"))
    for path in mcp_files:
        findings += lint_mcp(_label(root, path), path.read_text(encoding="utf-8"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root to lint")
    parser.add_argument("--mcp", type=Path, action="append", default=[], help="MCP settings JSON to lint (repeatable)")
    parser.add_argument("--setup-steps", type=Path, action="append", default=[],
                        help="an extra copilot-setup-steps workflow to lint (repeatable); the real one is always linted")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = parser.parse_args(argv)

    try:
        findings = collect(args.root, args.mcp, args.setup_steps)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for finding in findings:
        print(finding)
    errors = sum(f.severity == "ERROR" for f in findings)
    warnings = sum(f.severity == "WARN" for f in findings)
    print(f"{errors} error(s), {warnings} warning(s)")
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
