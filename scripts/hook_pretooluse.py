"""A teaching `preToolUse` hook: deny risky tool calls before they run.

Contract (from GitHub's hooks reference, checked 2026-10-09):
  * The hook receives ONE JSON document on stdin. camelCase events carry `toolName` and `toolArgs`;
    the PascalCase/VS Code form carries `tool_name` and `tool_input`. `toolArgs` may be a JSON
    string or an object, so both are handled.
  * It answers on stdout with one JSON document, for example
        {"permissionDecision": "deny", "permissionDecisionReason": "why"}
    A reason is REQUIRED when denying. Printing nothing means "use the default behavior".
  * In the cloud agent, "ask" is treated as "deny" (no human is there to answer).
  * A command hook that crashes or exits non-zero FAILS CLOSED (the tool call is denied). A hook
    that TIMES OUT fails OPEN (the tool call goes ahead). So keep this fast and offline.

Design choices, on purpose:
  * It only ever DENIES. It never prints "allow", because an explicit allow could short-circuit
    other permission checks. Silence lets the normal policy decide.
  * It reads a shell command the way a shell would instead of matching text. Matching the text
    `git push` misses `git -C . push`; matching `rm` plus a protected word blocks a harmless
    `cp scripts/x.py /tmp`. So each command's path arguments get a ROLE: removed or changed
    (a scope), written to (a destination), or merely read (ignored).
  * It is a heuristic guard, not a sandbox. Pair it with least-privilege tokens, protected
    branches, the pull-request policy gate (which reads the real diff) and review.
  * Tool-argument field names and tool names were NOT verified against a live cloud-agent session.
    The hook therefore scans every string inside the arguments and recognizes tools by words in
    their names. Log real payloads first (hook_audit.py), then tighten the rules.

What it understands: quotes, `;` `&&` `||` `|`, `sh -c '...'`, `$(...)`, `VAR=1 cmd`, `sudo`/`env`/
`timeout` wrappers, `cd DIR && ...`, redirects (`>`, `>>`, `&>`, `2>f`), `git -C dir ...`, and
rm / mv / cp / install / ln / tee / touch / mkdir / truncate / chmod / sed -i / dd / find -delete /
curl -o / wget -O / git rm / git mv / git checkout -- PATH / git restore.

What it cannot see (pinned by tests so nobody mistakes it for a wall): a command name hidden in a
variable, another program doing the write (python -c, node, perl, awk), scripts run from a file
(`bash run.sh`), patches applied from a file (`git apply`, `patch`), archives (`tar -C`, `unzip -d`),
and paths that arrive on stdin (`find ... | xargs rm`). The pull-request gate catches those by
reading the actual diff.

Exit codes: 0 = decision made (or default), 1 = could not read the payload (fail closed).
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

from labcommon import first_match, load_patterns, reaches_protected, writes_into_protected

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_PATH = ROOT / "policy" / "protected-paths.txt"

WRITE_TOOLS = {"edit", "create", "write", "multiedit", "str_replace_editor", "apply_patch", "notebookedit"}
WRITE_TOOL_WORDS = ("edit", "write", "create", "patch", "replace", "insert", "delete", "remove", "rename", "move", "append")
PATCH_TOOLS = {"apply_patch", "applypatch", "patch"}
SHELL_TOOLS = {"bash", "powershell", "shell"}
SHELL_TOOL_WORDS = ("bash", "shell", "terminal", "exec", "command")

# A hook that times out fails OPEN, so refuse to analyze inputs that could take too long.
MAX_COMMAND_CHARS = 100_000
MAX_PATH_CHARS = 4_096
MAX_DEPTH = 4

SEPARATORS = {"$", "<(", ">(", "{", "}"}
SEPARATOR_CHARS = set(";&|()")
# Words that can sit in front of the real command: shell keywords, then wrappers.
KEYWORDS = {"then", "do", "else", "elif", "if", "while", "until", "!"}
WRAPPERS = {"sudo", "env", "command", "exec", "nohup", "time", "xargs", "nice", "stdbuf", "timeout", "setsid", "ionice", "watch"}
# Wrappers whose first non-option word is a value (a duration), not the command to run.
WRAPPERS_WITH_POSITIONAL_VALUE = {"timeout"}
# Wrapper options that consume the NEXT token (`sudo -u root git push` runs git, not "root").
WRAPPER_VALUE_OPTIONS = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t"},
    "env": {"-u", "-C", "-S"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-t"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-a", "-E", "-s"},
    "stdbuf": {"-i", "-o", "-e"},
}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
# Global options that consume the NEXT token, so `git -C dir push` or `kubectl -n x delete` is still the real verb.
OPTIONS_WITH_VALUE = {
    "git": {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"},
    "kubectl": {"-n", "--namespace", "--context", "--cluster", "--user", "--kubeconfig", "-s", "--server", "--as"},
    "terraform": {"-chdir"},
    "tofu": {"-chdir"},
}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PIPE_INTO_SHELL = re.compile(r"\|\s*(sudo\s+)?(ba|z|da|k)?sh\b")
FD_NUMBER = re.compile(r"^\d+$")
REDIRECT_OUT = {">", ">>", "&>", "&>>", ">|", ">&"}
REDIRECT_IN = {"<", "<<", "<<<", "<&", "<>"}

PUSH_REASON = "direct pushes are not allowed; the agent proposes changes through a pull request"

# Path arguments by ROLE. Anything a command only READS is in none of these.
REMOVERS = {"rm", "rmdir", "unlink", "shred"}
EVERY_ARGUMENT_IS_WRITTEN = {"tee", "touch", "mkdir", "truncate"}
EVERY_ARGUMENT_IS_MODIFIED = {"chmod", "chown", "chgrp"}
COPIERS = {"cp", "install", "ln"}
DESTRUCTIVE_EXEC = REMOVERS | EVERY_ARGUMENT_IS_MODIFIED | EVERY_ARGUMENT_IS_WRITTEN | {"mv", "cp", "install", "ln", "dd", "sed"}
# Options that consume the next token as a VALUE, so the value is not mistaken for a file.
VALUE_OPTIONS = {
    "mv": {"-t", "--target-directory", "-S", "--suffix"},
    "cp": {"-t", "--target-directory", "-S", "--suffix"},
    "ln": {"-t", "--target-directory", "-S", "--suffix"},
    "install": {"-t", "--target-directory", "-m", "--mode", "-o", "--owner", "-g", "--group", "-S", "--suffix"},
    "touch": {"-d", "--date", "-t", "-r", "--reference"},
    "mkdir": {"-m", "--mode"},
    "truncate": {"-s", "--size", "-r", "--reference"},
    "sed": {"-e", "--expression", "-f", "--file"},
    "curl": {"-o", "--output"},
    "wget": {"-O", "--output-document", "-P", "--directory-prefix"},
}

PATCH_HEADER = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File: (?P<a>.+?)\s*$"
    r"|^\*\*\* Move to: (?P<b>.+?)\s*$"
    r"|^(?:---|\+\+\+) (?:[ab]/)?(?P<c>\S+)"
    r"|^diff --git a/(?P<d>\S+) b/(?P<e>\S+)",
    re.MULTILINE,
)


# ---------- reading a command line ----------

def _tokenize(command: str) -> list[str]:
    """Split a command line the way a POSIX shell would. Never raises: unbalanced quotes get closed first."""
    text = command.replace("\r\n", "\n").replace("\n", " ; ")
    for suffix in ("", "'", '"'):
        lexer = shlex.shlex(text + suffix, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        try:
            return list(lexer)
        except ValueError:
            continue
    return text.replace("'", " ").replace('"', " ").split()


def _is_separator(token: str) -> bool:
    return token in SEPARATORS or (bool(token) and set(token) <= SEPARATOR_CHARS)


def _split_segments(tokens: list[str]) -> list[list[str]]:
    """Cut a token list into simple commands at ; && || | & ( ) { } $( and backticks."""
    segments: list[list[str]] = [[]]
    for token in tokens:
        if _is_separator(token) or set(token) == {"`"}:
            segments.append([])
            continue
        if token.startswith("`"):
            segments.append([])
        word = token.strip("`")
        if word:
            segments[-1].append(word)
        if token.endswith("`") and len(token) > 1:
            segments.append([])
    return [segment for segment in segments if segment]


def _split_redirects(words: list[str]) -> tuple[list[str], list[str]]:
    """Separate a command's own words from the files its redirects write to."""
    kept: list[str] = []
    targets: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        following = words[index + 1] if index + 1 < len(words) else None
        if word in REDIRECT_OUT:
            if following is not None and not (word == ">&" and (FD_NUMBER.match(following) or following == "-")):
                targets.append(following)
            index += 2
        elif word in REDIRECT_IN:
            index += 2
        elif FD_NUMBER.match(word) and following in (REDIRECT_OUT | REDIRECT_IN):
            index += 1  # the '2' in `cmd 2>err.txt` is a file descriptor, not an argument
        else:
            kept.append(word)
            index += 1
    return kept, targets


def _strip_leading_noise(words: list[str]) -> list[str]:
    """Drop keywords, VAR=value prefixes and wrapper commands so `words[0]` is the real program."""
    while words:
        head = words[0]
        if ASSIGNMENT.match(head) or head in KEYWORDS:
            words = words[1:]
        elif head in WRAPPERS:
            words = words[1:]
            value_options = WRAPPER_VALUE_OPTIONS.get(head, set())
            while words and words[0].startswith("-"):
                words = words[2:] if words[0] in value_options else words[1:]
            if head in WRAPPERS_WITH_POSITIONAL_VALUE and words:
                words = words[1:]
        else:
            break
    return words


def _inline_script(words: list[str]) -> str | None:
    """The text after `-c` in `sh -c '...'` (also `bash -lc '...'`), or None."""
    for index, word in enumerate(words[1:], start=1):
        if word.startswith("-") and not word.startswith("--") and "c" in word[1:] and index + 1 < len(words):
            return words[index + 1]
    return None


# ---------- paths: where a command runs, and what role each argument plays ----------

def _join(base: str, path: str) -> str:
    """Resolve `path` against the directory a preceding `cd` moved to. Absolute and unresolvable paths pass through."""
    if not path or not base or path.startswith(("/", "~", "$")):
        return path
    return base.rstrip("/") + "/" + path


def _change_directory(args: list[str], cwd: str) -> str:
    targets = [a for a in args if not a.startswith("-")]
    if not targets or targets[0] == "-" or targets[0].startswith(("~", "$")):
        return ""  # home or unknown: assume the repository root, where a cloud-agent session starts
    return _join(cwd, targets[0])


def _shown(path: str) -> str:
    return path.strip().strip("'\"")


def _values(found: dict[str, list[str]], *names: str) -> list[str]:
    return [value for name in names for value in found.get(name, [])]


def _split_options(args: list[str], value_options: set[str]) -> tuple[list[str], dict[str, list[str]]]:
    """Separate positional arguments from options. Options in `value_options` take a value."""
    positionals: list[str] = []
    found: dict[str, list[str]] = {}
    index = 0
    options_done = False
    while index < len(args):
        arg = args[index]
        index += 1
        if options_done or arg == "-" or not arg.startswith("-"):
            positionals.append(arg)
        elif arg == "--":
            options_done = True
        elif arg.startswith("--"):
            name, _, attached = arg.partition("=")
            if attached:
                found.setdefault(name, []).append(attached)
            elif name in value_options and index < len(args):
                found.setdefault(name, []).append(args[index])
                index += 1
        else:
            first, last = arg[:2], "-" + arg[-1]
            if len(arg) > 2 and first in value_options:
                found.setdefault(first, []).append(arg[2:])  # -tscripts
            elif last in value_options and index < len(args):
                found.setdefault(last, []).append(args[index])  # -t scripts, or the t of -rt
                index += 1
    return positionals, found


def _find_scopes(args: list[str]) -> list[str]:
    """`find START -delete` and `find START -exec rm {} ;` remove or change everything under START."""
    index = 0
    while index < len(args) and args[index] in {"-H", "-L", "-P"}:
        index += 1
    starts: list[str] = []
    while index < len(args) and not args[index].startswith("-") and args[index] not in {"(", "!"}:
        starts.append(args[index])
        index += 1
    destructive = "-delete" in args
    for position, arg in enumerate(args[:-1]):
        if arg in {"-exec", "-execdir", "-ok", "-okdir"}:
            program = Path(args[position + 1]).name
            in_place = any(a.startswith("-i") or a == "--in-place" for a in args[position + 2:])
            if program in DESTRUCTIVE_EXEC and (program != "sed" or in_place):
                destructive = True
    return (starts or ["."]) if destructive else []


def _roles(name: str, args: list[str]) -> tuple[list[str], list[str]]:
    """Return (scopes, writes) for a command's path arguments.

    scopes: paths the command REMOVES or CHANGES in place. A folder or a wildcard counts for
            everything it contains, so `rm -rf scripts` and `rm -rf *` both reach `scripts/`.
    writes: paths the command WRITES a file to. Writing into a protected folder counts.
    A path the command only READS (the source of `cp`, the file `cat` prints) is in neither list.
    """
    options = VALUE_OPTIONS.get(name, set())
    if name in REMOVERS or name in EVERY_ARGUMENT_IS_MODIFIED:
        return _split_options(args, options)[0], []
    if name in EVERY_ARGUMENT_IS_WRITTEN:
        return [], _split_options(args, options)[0]
    if name == "mv":
        positionals, found = _split_options(args, options)
        target = _values(found, "-t", "--target-directory")
        if target:
            return positionals, target
        return (positionals[:-1], positionals[-1:]) if len(positionals) > 1 else (positionals, [])
    if name in COPIERS:
        positionals, found = _split_options(args, options)
        if name == "install" and any(a in {"-d", "--directory"} for a in args):
            return [], positionals
        return [], _values(found, "-t", "--target-directory") or positionals[-1:]
    if name == "dd":
        return [], [a[3:] for a in args if a.startswith("of=")]
    if name == "sed":
        in_place = any(
            a == "--in-place" or a.startswith("--in-place=") or (a.startswith("-") and not a.startswith("--") and "i" in a[1:])
            for a in args
        )
        return (_split_options(args, options)[0], []) if in_place else ([], [])
    if name == "find":
        return _find_scopes(args), []
    if name == "curl":
        return [], _values(_split_options(args, options)[1], "-o", "--output")
    if name == "wget":
        return [], _values(_split_options(args, options)[1], "-O", "--output-document", "-P", "--directory-prefix")
    return [], []


def _git_roles(words: list[str]) -> tuple[list[str], list[str]]:
    """Return (scopes, writes) for `git [-C dir] <verb> ...`, with each path joined to any -C directory."""
    with_value = OPTIONS_WITH_VALUE["git"]
    workdir, index = "", 1
    while index < len(words):
        word = words[index]
        if word == "-C" and index + 1 < len(words):
            workdir = _join(workdir, words[index + 1])
            index += 2
        elif word in with_value:
            index += 2
        elif word.startswith("-"):
            index += 1
        else:
            break
    if index >= len(words):
        return [], []
    verb, rest = words[index], words[index + 1:]
    scopes: list[str] = []
    writes: list[str] = []
    if verb == "rm":
        scopes = _split_options(rest, set())[0]
    elif verb == "mv":
        positionals = _split_options(rest, set())[0]
        scopes, writes = (positionals[:-1], positionals[-1:]) if len(positionals) > 1 else (positionals, [])
    elif verb == "checkout" and "--" in rest:
        scopes = rest[rest.index("--") + 1:]
    elif verb == "restore":
        scopes = _split_options(rest, {"-s", "--source"})[0]
    return [_join(workdir, p) for p in scopes], [_join(workdir, p) for p in writes]


# ---------- irreversible actions ----------

def _verb(words: list[str]) -> tuple[str, list[str]] | None:
    """Return (verb, remaining words) for `tool [global options] verb ...`."""
    with_value = OPTIONS_WITH_VALUE.get(Path(words[0]).name, set())
    index = 1
    while index < len(words):
        word = words[index]
        if word in with_value:
            index += 2
        elif word.startswith("-"):
            index += 1
        else:
            return word, words[index + 1:]
    return None


def _asks_for_help(rest: list[str]) -> bool:
    return any(word in {"--help", "-h", "help"} for word in rest)


def _irreversible_reason(words: list[str]) -> str | None:
    """Decide from ONE simple command (already split on ; && | and so on)."""
    words = _strip_leading_noise(words)
    if not words:
        return None
    name = Path(words[0]).name

    if name == "rm":
        flags = "".join(w.lstrip("-") for w in words[1:] if w.startswith("-") and not w.startswith("--"))
        targets = [w for w in words[1:] if not w.startswith("-")]
        if "r" in flags.lower() and "f" in flags and any(t in {"/", "~", "$HOME", "/*", "~/"} for t in targets):
            return "recursive delete of a root or home path"
        return None

    found = _verb(words)
    if not found:
        return None
    verb, rest = found
    if _asks_for_help(rest):
        return None
    if name == "git":
        if verb == "push":
            return PUSH_REASON
        if verb == "reset" and "--hard" in rest:
            return "destructive git commands are not allowed"
        if verb == "clean" and any(r.startswith("-") and not r.startswith("--") and "f" in r for r in rest):
            return "destructive git commands are not allowed"
    elif name in {"terraform", "tofu"} and verb in {"apply", "destroy"}:
        return "infrastructure changes need a human-controlled path"
    elif name == "kubectl" and verb in {"delete", "apply"}:
        return "cluster changes need a human-controlled path"
    elif name == "az" and any(w in {"delete", "create", "update"} for w in words[1:]):
        return "cloud resource changes need a human-controlled path"
    elif name == "gh" and [verb, *rest[:1]] in (["repo", "delete"], ["release", "delete"], ["secret", "set"]):
        return "repository administration needs a human"
    return None


# ---------- the whole command line ----------

def _scope_reason(name: str, path: str, rule: str, kind: str) -> str:
    if kind == "inside":
        return f"{name} would change protected path '{_shown(path)}' (rule '{rule}')"
    return (
        f"{name} scope '{_shown(path)}' is broad enough to include protected paths (rule '{rule}'); "
        "name the exact files you mean"
    )


def _scan(command: str, protected: list[str], depth: int = 0, cwd: str = "") -> tuple[str, str] | None:
    """Return (kind, reason) for the first problem in a shell command line, or None.

    kind is 'irreversible' (an action that cannot be undone), 'protected' (changes something the
    policy protects) or 'uninspectable' (too large to analyze before the hook would time out).
    """
    if depth > MAX_DEPTH:
        return None
    if len(command) > MAX_COMMAND_CHARS:
        return "uninspectable", f"the command is longer than {MAX_COMMAND_CHARS:,} characters, too long to inspect in time"

    for segment in _split_segments(_tokenize(command)):
        words, redirects = _split_redirects(segment)
        for target in redirects:
            if writes_into_protected(_join(cwd, target), protected):
                return "protected", f"shell redirect writes to protected path '{_shown(target)}'"
        words = _strip_leading_noise(words)
        if not words:
            continue
        name = Path(words[0]).name

        if name in SHELLS:
            inner = _inline_script(words)
            found = _scan(inner, protected, depth + 1, cwd) if inner is not None else None
            if found:
                return found
            continue
        if name in {"cd", "pushd"}:
            cwd = _change_directory(words[1:], cwd)
            continue

        reason = _irreversible_reason(words)
        if reason:
            return "irreversible", reason

        scopes, writes = _git_roles(words) if name == "git" else _roles(name, words[1:])
        for path in scopes:
            hit = reaches_protected(_join(cwd, path), protected)
            if hit:
                return "protected", _scope_reason(name, path, *hit)
        for path in writes:
            rule = writes_into_protected(_join(cwd, path), protected)
            if rule:
                return "protected", f"{name} would write into protected path '{_shown(path)}' (rule '{rule}')"

        # A quoted token such as "$(git push)" is itself a command line; look inside it.
        for word in words:
            if " " in word and ("$(" in word or "`" in word):
                inner = word.replace("$(", " ; ").replace(")", " ; ").replace("`", " ; ")
                found = _scan(inner, protected, depth + 1, cwd)
                if found:
                    return found

    if PIPE_INTO_SHELL.search(command):
        return "irreversible", "piping content into a shell runs unreviewed code and is not allowed"
    return None


# ---------- tool calls ----------

def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _is_write_tool(tool: str) -> bool:
    return tool in WRITE_TOOLS or any(word in tool for word in WRITE_TOOL_WORDS)


def _is_shell_tool(tool: str) -> bool:
    return tool in SHELL_TOOLS or any(word in tool for word in SHELL_TOOL_WORDS)


def _patch_paths(text: str):
    """File paths named in a patch: apply_patch headers ('*** Update File: x') and unified-diff headers."""
    for match in PATCH_HEADER.finditer(text):
        for value in match.groups():
            if value:
                yield value


def _protected_target(text: str, protected: list[str]) -> str | None:
    """The rule a WRITE to `text` would break, if `text` looks like a path rather than file contents."""
    if not text.strip() or len(text) > MAX_PATH_CHARS or "\n" in text:
        return None
    return writes_into_protected(text, protected)


def decide(payload: dict, protected: list[str]) -> tuple[str, str] | None:
    """Return ('deny', reason) or None for 'no opinion, use default behavior'."""
    tool = str(payload.get("toolName") or payload.get("tool_name") or "").lower()
    args = payload.get("toolArgs", payload.get("tool_input"))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args}

    if _is_write_tool(tool):
        texts = list(_strings(args))
        if tool in PATCH_TOOLS:
            texts += [path for text in texts for path in _patch_paths(text)]
        for text in texts:
            rule = _protected_target(text, protected)
            if rule:
                return "deny", (
                    f"'{_shown(text)}' is a protected path (rule '{rule}'). "
                    "Agents may not change workflows, agent profiles, policy, schemas, scripts, plans or the "
                    "acceptance tests that define done. "
                    "Describe the change you need in the pull request and ask a human to make it."
                )
    elif _is_shell_tool(tool):
        for command in _strings(args):
            found = _scan(command, protected)
            if not found:
                continue
            kind, reason = found
            if kind == "irreversible":
                return "deny", f"blocked command: {reason}. Escalate to a human instead."
            if kind == "uninspectable":
                return "deny", f"blocked command: {reason}. Split it into smaller steps."
            return "deny", f"{reason}. Ask a human to make this change."
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        protected = load_patterns(PROTECTED_PATH)
    except (OSError, ValueError) as exc:
        print(f"hook_pretooluse: cannot evaluate request ({exc}); failing closed", file=sys.stderr)
        return 1

    verdict = decide(payload, protected)
    if verdict:
        decision, reason = verdict
        print(json.dumps({"permissionDecision": decision, "permissionDecisionReason": reason}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
