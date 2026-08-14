#!/usr/bin/env python3
"""
pickle — a free coding agent powered by Big Pickle (opencode.ai/zen)
Usage: python pickle.py            # new session
       python pickle.py load       # resume a saved session
"""

import httpx
import uuid
import json
import os
import random
import inspect
import subprocess
import sys
import shutil
import datetime
from pathlib import Path
from difflib import unified_diff

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.prompt import Prompt, Confirm
from rich.markdown import Markdown
from rich.text import Text
from rich.rule import Rule
from rich import print as rprint

console = Console()

# ── config ────────────────────────────────────────────────────────────────────
# Set these to customize behaviour without touching the rest of the code.

ASK_BEFORE_COMMAND = False    # ask user to confirm every shell command
                             # (rm/rmdir always prompt regardless of this flag)

ASK_BEFORE_WRITE   = False    # ask user to confirm file writes / edits
ASK_BEFORE_DELETE  = True    # ask user to confirm file deletions

REQUEST_TIMEOUT    = 30      # seconds for request_url HTTP calls
COMMAND_TIMEOUT    = 60      # seconds before a shell command is killed
MAX_READ_CHARS     = 40_000  # chars returned to the model per read_file call
MAX_URL_CHARS      = 20_000  # chars returned to the model per request_url call
MAX_GREP_CHARS     = 20_000  # chars returned to the model per search/find call
MAX_COMMAND_CHARS  = 20_000  # chars returned to the model per run_command call

MODEL              = "big-pickle"
USER_NAME          = "Ibrahim"

# Sessions are saved as JSON files inside this folder — one file per
# conversation, named after that session's UUID (session/<uuid>.json).
SESSION_DIR        = "session"

# ── palette ──────────────────────────────────────────────────────────────────
C_ACCENT  = "green"
C_USER    = "bold cyan"
C_AI      = "bold green"
C_TOOL    = "bold yellow"
C_WARN    = "bold red"
C_MUTED   = "dim white"
C_FILE    = "bold blue"

# ── Tool definitions sent to the model ───────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file on the user's system.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_files",
            "description": (
                "Search file contents for a regex pattern. Returns matches as 'path:line: content'. "
                "Use this to find where things are defined, called, or referenced. "
                "Automatically skips binary files and common junk dirs (node_modules, .git, etc.). "
                "Supports include/exclude globs (* and ? wildcards)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":        {"type": "string", "description": "Regex (or plain text) to search for."},
                    "path":           {"type": "string", "description": "File or directory to search. Defaults to the current directory."},
                    "include":        {"type": "array", "items": {"type": "string"}, "description": "Optional glob patterns of files to include, e.g. [\"*.py\", \"*.ts\"]."},
                    "exclude":        {"type": "array", "items": {"type": "string"}, "description": "Optional glob patterns of files to skip, e.g. [\"test_*\"]."},
                    "case_sensitive": {"type": "boolean", "description": "Match case exactly. Default false."},
                    "max_results":    {"type": "integer", "description": "Maximum number of matches to return. Default 200."}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_file",
            "description": (
                "Find files or directories by name or glob pattern. "
                "Supports * and ? wildcards plus ** for recursive globs (e.g. \"**/test_*\"). "
                "If the pattern has no wildcards, does a case-insensitive substring match on the "
                "file/dir name. Returns relative paths, one per line."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":     {"type": "string", "description": "Glob pattern or name substring, e.g. \"*.py\", \"**/test_*\", \"pickel\"."},
                    "path":        {"type": "string", "description": "Directory to search. Defaults to the current directory."},
                    "type":        {"type": "string", "enum": ["file", "dir", "any"], "description": "Filter results to files, dirs, or both. Default any."},
                    "max_results": {"type": "integer", "description": "Maximum number of results. Default 200."}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_range",
            "description": (
                "Read a specific range of lines from a file (1-based, inclusive). "
                "Use this to inspect parts of large files instead of read_file, which always "
                "starts from the top. Line numbers are for reference only — never include them "
                "in edit_file old_str/new_str."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path":       {"type": "string", "description": "Path to the file."},
                    "start_line": {"type": "integer", "description": "First line to read (1-based). Default 1."},
                    "end_line":   {"type": "integer", "description": "Last line to read, inclusive. Defaults to end of file."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or completely overwrite an existing one with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Path to the file."},
                    "content": {"type": "string", "description": "Full content to write."}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact string in a file with new content (like a surgical find-and-replace).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Path to the file."},
                    "old_str": {"type": "string", "description": "Exact string to find (must be unique in the file)."},
                    "new_str": {"type": "string", "description": "String to replace it with."}
                },
                "required": ["path", "old_str", "new_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Permanently delete a file or empty directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to delete."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories at a given path (2 levels deep).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to list. Defaults to current directory."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command on the user's machine. ALWAYS use this for installs, tests, git, builds. Destructive commands (rm/rmdir) always prompt for confirmation; set ASK_BEFORE_COMMAND=True to confirm every command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."},
                    "reason":  {"type": "string", "description": "Short explanation of why this command is needed."}
                },
                "required": ["command", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "request_url",
            "description": (
                "Fetch a URL and return its content. Use for: reading docs, fetching API responses, "
                "downloading raw files, or scraping page text. Returns plain text (HTML tags stripped). "
                "For JSON APIs, the raw JSON is returned as-is."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url":     {"type": "string", "description": "Full URL to fetch (must include http:// or https://)."},
                    "method":  {"type": "string", "description": "HTTP method: GET (default) or POST.", "enum": ["GET", "POST"]},
                    "headers": {"type": "object", "description": "Optional extra HTTP headers (dict)."},
                    "body":    {"type": "string", "description": "Request body for POST requests (JSON string)."}
                },
                "required": ["url"]
            }
        }
    }
]

# ── Tool implementations ──────────────────────────────────────────────────────

def _status(msg: str, end: bool = False):
    """Write msg on the current line, overwriting what was there. If end=True, finalize with a newline."""
    cols = shutil.get_terminal_size().columns
    line = f"  {msg}"
    # pad with spaces to clear any leftover characters from a longer previous line
    line = line[:cols - 1].ljust(cols - 1)
    sys.stderr.write(f"\r{line}")
    if end:
        sys.stderr.write("\n")
    sys.stderr.flush()


def tool_read_file(path: str) -> str:
    p = Path(path).expanduser()
    _status(f"reading  {p}")
    if not p.exists():
        _status(f"✗ not found: {p}", end=True)
        return f"ERROR: file not found: {path}"
    if not p.is_file():
        _status(f"✗ not a file: {p}", end=True)
        return f"ERROR: not a file: {path}"
    if _looks_binary(p):
        _status(f"✗ binary: {p}", end=True)
        return f"ERROR: file appears to be binary: {path}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        text = content[:MAX_READ_CHARS] + ("\n… (truncated)" if len(content) > MAX_READ_CHARS else "")
        _status(f"✓ read  {p}  ({len(content)} chars)", end=True)
        return text
    except Exception as e:
        _status(f"✗ error: {e}", end=True)
        return f"ERROR: {e}"


# ── Search / find helpers ─────────────────────────────────────────────────────

_DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", ".env",
    "dist", "build", ".next", "target", ".cache", "egg-info",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}


def _glob_to_regex(pattern: str) -> str:
    """Convert a glob pattern (with ** support) into a regex string."""
    import re as _re
    rx = ""
    for i, part in enumerate(pattern.split("**")):
        if i > 0:
            rx += ".*"                       # ** crosses directory boundaries
        esc = _re.escape(part)
        esc = esc.replace(r"\*", "[^/]*")    # *  single segment wildcard
        esc = esc.replace(r"\?", "[^/]")     # ?  single char wildcard
        rx += esc
    return rx + "$"


def _glob_match(pattern: str, name: str) -> bool:
    """Case-insensitive glob match against a path/name string (supports **)."""
    import re as _re
    return _re.match(_glob_to_regex(pattern), name, _re.IGNORECASE) is not None


def _glob_match_any(path_str: str, globs: list) -> bool:
    """True if path_str — or any suffix of it (e.g. 'util/app.py' from
    'src/util/app.py') — matches any of the glob patterns. Suffix matching
    makes patterns like 'util/*' or 'test_*' work regardless of depth."""
    if not globs:
        return True
    parts    = path_str.split("/")
    suffixes = ["/".join(parts[i:]) for i in range(len(parts))]
    for g in globs:
        for s in suffixes:
            if _glob_match(g, s):
                return True
    return False


def _looks_binary(path: Path) -> bool:
    """Return True if the first 4KB of the file contains a null byte."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        return b"\x00" in head
    except OSError:
        return True  # unreadable → treat as binary so callers skip it


def _prune_ignored(dirnames: list) -> list:
    """Return dirnames minus the default-ignored ones (sorted)."""
    return sorted(d for d in dirnames if d not in _DEFAULT_IGNORE_DIRS)


def _walk_files(root: Path):
    """Yield every file under root, skipping default-ignored dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = _prune_ignored(dirnames)
        for fn in filenames:
            yield Path(dirpath) / fn


def tool_search_in_files(pattern: str, path: str = ".",
                         include: list = None, exclude: list = None,
                         case_sensitive: bool = False, max_results: int = 200) -> str:
    import re as _re
    root = Path(path or ".").expanduser()
    if not root.exists():
        _status(f"✗ not found: {root}", end=True)
        return f"ERROR: path not found: {path}"
    if not pattern:
        return "ERROR: pattern is required."

    try:
        flags = 0 if case_sensitive else _re.IGNORECASE
        rx = _re.compile(pattern, flags)
    except _re.error as e:
        _status(f"✗ invalid regex: {e}", end=True)
        return f"ERROR: invalid regex: {e}"

    # accept a single string in include/exclude too (models sometimes send one)
    if isinstance(include, str):
        include = [include]
    if isinstance(exclude, str):
        exclude = [exclude]
    include = include or []
    exclude = exclude or []
    max_results = int(max_results or 200)

    def wanted(f: Path) -> bool:
        rel = f.relative_to(root).as_posix() if root.is_dir() else f.name
        if include and not _glob_match_any(rel, include):
            return False
        if exclude and _glob_match_any(rel, exclude):
            return False
        return True

    results = []
    files = [root] if root.is_file() else _walk_files(root)
    for f in files:
        if not wanted(f) or _looks_binary(f):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                results.append((f, lineno, line.strip()[:500]))
                if len(results) >= max_results:
                    break
        if len(results) >= max_results:
            break

    total = len(results)
    if total == 0:
        _status(f"✓ grep 0 matches for /{pattern}/ in {root}", end=True)
        return f"(no matches for /{pattern}/ in {root})"

    out = "\n".join(f"{f}:{lineno}: {line}" for f, lineno, line in results)
    if total >= max_results:
        out += (f"\n(results capped at max_results={max_results} — "
                f"narrow with include/exclude or raise max_results)")
    if len(out) > MAX_GREP_CHARS:
        out = out[:MAX_GREP_CHARS] + "\n… (truncated)"

    plural = "" if total == 1 else "es"
    _status(f"✓ grep {total} match{plural} for /{pattern}/ in {root}", end=True)
    console.print(Panel(
        out[:3000] + ("…" if len(out) > 3000 else ""),
        title=f"[{C_FILE}]search_in_files[/] · {total} match{plural}",
        border_style=C_ACCENT
    ))
    return out


def _match_find(pattern: str, rel: str, name: str, has_wildcard: bool) -> bool:
    if has_wildcard:
        return _glob_match_any(rel, [pattern])
    return pattern.lower() in name.lower()


def tool_find_file(pattern: str, path: str = ".", type: str = "any",
                   max_results: int = 200) -> str:
    root = Path(path or ".").expanduser()
    if not root.exists():
        return f"ERROR: path not found: {path}"
    if not root.is_dir():
        return f"ERROR: not a directory: {path}"
    if not pattern:
        return "ERROR: pattern is required."

    max_results  = int(max_results or 200)
    has_wildcard = any(ch in pattern for ch in "*?")
    results = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = _prune_ignored(dirnames)
        if type in ("dir", "any"):
            for d in dirnames:
                p   = Path(dirpath) / d
                rel = p.relative_to(root).as_posix()
                if _match_find(pattern, rel, p.name, has_wildcard):
                    results.append(rel)
                    if len(results) >= max_results:
                        break
        if type in ("file", "any"):
            for fn in filenames:
                p   = Path(dirpath) / fn
                rel = p.relative_to(root).as_posix()
                if _match_find(pattern, rel, p.name, has_wildcard):
                    results.append(rel)
                    if len(results) >= max_results:
                        break
        if len(results) >= max_results:
            break

    if not results:
        _status(f"✓ find 0 results for {pattern!r} in {root}", end=True)
        return f"(no files match {pattern!r} in {root})"

    out = "\n".join(results)
    if len(out) > MAX_GREP_CHARS:
        out = out[:MAX_GREP_CHARS] + "\n… (truncated)"

    plural = "" if len(results) == 1 else "s"
    _status(f"✓ find {len(results)} result{plural} for {pattern!r} in {root}", end=True)
    console.print(f"  [{C_FILE}]find[/] {len(results)} result{plural} for {pattern!r} in {root}")
    return out


def tool_read_range(path: str, start_line: int = 1, end_line: int = None) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        _status(f"✗ not found: {p}", end=True)
        return f"ERROR: file not found: {path}"
    if not p.is_file():
        _status(f"✗ not a file: {p}", end=True)
        return f"ERROR: not a file: {path}"
    if _looks_binary(p):
        _status(f"✗ binary: {p}", end=True)
        return f"ERROR: file appears to be binary: {path}"

    try:
        start_line = int(start_line)
        if end_line is not None:
            end_line = int(end_line)
        if start_line < 1:
            return f"ERROR: start_line must be >= 1 (got {start_line})."
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR: {e}"

    lines = text.splitlines()
    total = len(lines)
    if start_line > total:
        _status(f"✗ start beyond EOF: {p}", end=True)
        return f"ERROR: file has only {total} lines (requested start_line={start_line})."
    if end_line is None:
        end_line = total
    elif end_line < start_line:
        return f"ERROR: end_line ({end_line}) must be >= start_line ({start_line})."
    end_line = min(end_line, total)

    body = "".join(f"{i:>6}: {ln}\n"
                   for i, ln in enumerate(lines[start_line - 1:end_line], start_line))
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS] + "\n… (truncated)"

    header = f"lines {start_line}–{end_line} of {p} ({end_line - start_line + 1} lines)"
    _status(f"✓ read {start_line}–{end_line} of {p}", end=True)
    console.print(Panel(
        Syntax(body[:3000] + ("…" if len(body) > 3000 else ""),
               p.suffix.lstrip(".") or "text", theme="monokai", line_numbers=False),
        title=f"[{C_FILE}]{p}[/]  [{C_MUTED}](lines {start_line}–{end_line})[/]",
        border_style=C_ACCENT
    ))
    return f"{header}\n{'-' * 50}\n{body}"


def tool_write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    exists = p.exists()
    action = "overwrite" if exists else "create"

    # Show a preview
    lang = p.suffix.lstrip(".") or "text"
    console.print(Panel(
        Syntax(content[:2000] + ("…" if len(content) > 2000 else ""),
               lang, theme="monokai", line_numbers=True),
        title=f"[{C_FILE}]{p}[/]  [{C_MUTED}]({action})[/]",
        border_style=C_ACCENT
    ))

    if ASK_BEFORE_WRITE and not Confirm.ask(f"  [{C_WARN}]Write this file?[/]", default=True):
        return "CANCELLED by user."

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    console.print(f"  [{C_ACCENT}]✓ written:[/] {p}")
    return f"OK: wrote {len(content)} chars to {path}"


def tool_edit_file(path: str, old_str: str, new_str: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"ERROR: file not found: {path}"

    original = p.read_text(encoding="utf-8", errors="replace")
    if old_str not in original:
        return f"ERROR: old_str not found in {path}. Make sure it matches exactly."
    if original.count(old_str) > 1:
        return f"ERROR: old_str matches {original.count(old_str)} places — needs to be unique."

    updated = original.replace(old_str, new_str, 1)

    # Show diff
    diff_lines = list(unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3
    ))
    diff_text = "".join(diff_lines) or "(no diff)"
    console.print(Panel(
        Syntax(diff_text, "diff", theme="monokai"),
        title=f"[{C_FILE}]{p}[/]  [{C_MUTED}](edit)[/]",
        border_style=C_ACCENT
    ))

    if ASK_BEFORE_WRITE and not Confirm.ask(f"  [{C_WARN}]Apply this edit?[/]", default=True):
        return "CANCELLED by user."

    p.write_text(updated, encoding="utf-8")
    console.print(f"  [{C_ACCENT}]✓ edited:[/] {p}")
    return f"OK: edit applied to {path}"


def tool_delete_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"ERROR: path not found: {path}"

    console.print(f"  [{C_WARN}]DELETE[/] {p}")
    if ASK_BEFORE_DELETE and not Confirm.ask(f"  [{C_WARN}]Permanently delete {path}?[/]", default=False):
        return "CANCELLED by user."

    if p.is_file():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)
    console.print(f"  [{C_ACCENT}]✓ deleted:[/] {p}")
    return f"OK: deleted {path}"


def tool_list_dir(path: str = ".") -> str:
    p = Path(path or ".").expanduser()
    if not p.exists():
        return f"ERROR: path not found: {path}"
    lines = []
    try:
        for item in sorted(p.iterdir()):
            prefix = "📁 " if item.is_dir() else "📄 "
            lines.append(f"{prefix}{item.name}")
            if item.is_dir():
                try:
                    for sub in sorted(item.iterdir())[:10]:
                        sp = "  📁 " if sub.is_dir() else "  📄 "
                        lines.append(f"{sp}{sub.name}")
                except PermissionError:
                    pass
    except PermissionError as e:
        return f"ERROR: {e}"
    console.print(f"  [{C_FILE}]listing[/] {p}  [{C_MUTED}]({len(lines)} items)[/]")
    return "\n".join(lines) or "(empty directory)"


def _is_destructive(command: str) -> bool:
    """Return True if the command contains rm/rmdir — always needs confirmation."""
    import re
    return bool(re.search(r"\brm\b|\brmdir\b", command))


def tool_run_command(command: str, reason: str = "") -> str:
    destructive = _is_destructive(command)
    needs_ask   = ASK_BEFORE_COMMAND or destructive

    console.print(Panel(
        f"[bold]{command}[/]\n\n[{C_MUTED}]{reason}[/]"
        + (f"\n\n[{C_WARN}]⚠ destructive command (rm)[/]" if destructive else ""),
        title=f"[{C_TOOL}]shell command[/]",
        border_style="red" if destructive else "yellow"
    ))

    if needs_ask:
        if not Confirm.ask(f"  [{C_WARN}]Execute this command?[/]", default=False):
            return "CANCELLED by user."

    console.print(f"  [{C_TOOL}]running…[/]")
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        output = output.strip() or "(no output)"
        if len(output) > MAX_COMMAND_CHARS:
            output = output[:MAX_COMMAND_CHARS] + f"\n… (output truncated at {MAX_COMMAND_CHARS} chars)"
        console.print(Panel(
            output[:3000] + ("…" if len(output) > 3000 else ""),
            title=f"[{C_MUTED}]exit code {result.returncode}[/]",
            border_style="green" if result.returncode == 0 else "red"
        ))
        return f"exit_code={result.returncode}\n{output}"
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {COMMAND_TIMEOUT}s"
    except Exception as e:
        return f"ERROR: {e}"


def tool_request_url(url: str, method: str = "GET", headers: dict = None, body: str = None) -> str:
    import re
    _status(f"fetching  {url}")
    try:
        req_headers = {"User-Agent": "pickle-agent/1.0"}
        if headers:
            req_headers.update(headers)

        client_kwargs = dict(headers=req_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True)
        entry = _pick_proxy()
        if entry:
            mounts = _build_httpx_mounts(entry)
            if mounts:
                client_kwargs["mounts"] = mounts

        req_kwargs = {}
        if method.upper() == "POST" and body:
            req_kwargs["content"] = body.encode()

        with httpx.Client(**client_kwargs) as client:
            resp = client.request(method.upper(), url, **req_kwargs)

        content_type = resp.headers.get("content-type", "")

        # JSON — return as-is
        if "json" in content_type:
            text = resp.text
        # HTML — strip tags to save context
        elif "html" in content_type:
            # remove scripts, styles, and tags
            text = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", "", resp.text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"[ \t]+", " ", text)
            text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
        else:
            text = resp.text

        text = text[:MAX_URL_CHARS] + ("\n… (truncated)" if len(text) > MAX_URL_CHARS else "")
        _status(f"✓ fetched  {url}  ({resp.status_code}, {len(text)} chars)", end=True)
        return f"status={resp.status_code}\ncontent-type={content_type}\n\n{text}"

    except httpx.TimeoutException:
        _status(f"✗ timeout: {url}", end=True)
        return f"ERROR: request timed out after {REQUEST_TIMEOUT}s"
    except Exception as e:
        _status(f"✗ error: {e}", end=True)
        return f"ERROR: {e}"


def _call_tool(fn, fn_args: dict):
    """Call a tool function, ignoring any keyword args it doesn't declare.

    The model sometimes sends extra/legacy parameters (e.g. offset, limit)
    that a tool doesn't accept — filter them out so we don't crash with
    'unexpected keyword argument' TypeErrors.
    """
    sig = inspect.signature(fn)
    for p in sig.parameters.values():
        if p.kind == inspect.Parameter.VAR_KEYWORD:
            return fn(**fn_args)          # accepts anything (**kwargs)
    accepted = set(sig.parameters)
    filtered = {k: v for k, v in fn_args.items() if k in accepted}
    return fn(**filtered)


TOOL_MAP = {
    "read_file":       tool_read_file,
    "read_range":      tool_read_range,
    "search_in_files": tool_search_in_files,
    "find_file":       tool_find_file,
    "write_file":      tool_write_file,
    "edit_file":       tool_edit_file,
    "delete_file":     tool_delete_file,
    "list_dir":        tool_list_dir,
    "run_command":     tool_run_command,
    "request_url":     tool_request_url,
}

# ── Proxy support ─────────────────────────────────────────────────────────────

PROXY_LIST = None    # list of {"http": .., "https": ..} dicts, loaded via setup_proxy() or the /proxy command
USE_PROXY  = False   # master switch — True once the user enables proxying


def _normalize_proxy_url(url: str) -> str:
    """Ensure a proxy URL has a scheme (defaults to http://)."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    return url


def _validate_proxy_list(data) -> str:
    """Return an error message if data is not [{"http": ..., "https": ...}, ...], else ''."""
    if not isinstance(data, list):
        return ('top level must be a JSON list, e.g. '
                '[{"http": "1.2.3.4:8080", "https": "5.6.7.8:8080"}, ...]')
    if not data:
        return "the list is empty — add at least one proxy entry"
    for i, entry in enumerate(data, 1):
        if not isinstance(entry, dict):
            return f"entry #{i} must be an object (depth-2 dict), got {type(entry).__name__}"
        if not entry:
            return f"entry #{i} is empty — expected keys like 'http' and 'https'"
        for key, val in entry.items():
            if isinstance(val, (int, float)):
                continue  # tolerate bare numbers, coerced to str later
            if not isinstance(val, str):
                return f"entry #{i} key {key!r} must be a string, got {type(val).__name__}"
    return ""


def _build_httpx_mounts(entry: dict) -> dict:
    """Turn one proxy entry into an httpx mounts dict (per-scheme proxies).

    httpx >= 0.26 dropped the old `proxies=` dict; per-scheme proxies now go
    through Client(mounts={"http://": Proxy(...), "https://": Proxy(...)}).
    Malformed proxy URLs are skipped.
    """
    mounts = {}
    for scheme in ("http", "https"):
        raw = entry.get(scheme)
        if raw is None:
            continue
        url = _normalize_proxy_url(str(raw))
        if not url:
            continue
        try:
            mounts[f"{scheme}://"] = httpx.Proxy(url)
        except Exception:
            continue  # skip malformed proxy URLs
    return mounts


def _pick_proxy() -> dict:
    """Return a random proxy entry when proxying is enabled, else None."""
    if not USE_PROXY or not PROXY_LIST:
        return None
    return random.choice(PROXY_LIST)


def _load_proxy_file() -> list:
    """Prompt for a proxy JSON file path and validate it in a loop.

    The file must contain a list of depth-2 dicts, e.g.:
        [{"http": "111.111.11", "https": "11.222.55.66"}, ...]

    Loops on errors (file not found, bad JSON, wrong structure) until the
    user provides a valid file or cancels (empty path → returns None).
    """
    while True:
        raw = Prompt.ask(f"  [{C_USER}]path to proxy JSON file[/]").strip().strip('"').strip("'")
        if not raw:
            console.print(f"  [{C_WARN}]no path given — proxy disabled.[/]")
            return None

        path = Path(raw).expanduser()
        if not path.is_file():
            console.print(f"  [{C_WARN}]✗ file not found: {path} — try again (Enter to disable).[/]")
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            console.print(f"  [{C_WARN}]✗ invalid JSON: {e} — try again.[/]")
            continue
        except OSError as e:
            console.print(f"  [{C_WARN}]✗ cannot read file: {e} — try again.[/]")
            continue

        error = _validate_proxy_list(data)
        if error:
            console.print(f"  [{C_WARN}]✗ invalid proxy file: {error} — try again.[/]")
            continue

        console.print(
            f"  [{C_ACCENT}]✓ loaded {len(data)} entr{'y' if len(data) == 1 else 'ies'} from {path}[/]"
        )
        return data


def setup_proxy() -> list:
    """Startup question: ask whether to use a proxy, then load the file if yes.

    Returns the list of proxy entries, or None when the user opts out/cancels.
    """
    console.print()
    if not Confirm.ask(f"  [{C_USER}]use a proxy for network requests?[/]", default=False):
        return None
    return _load_proxy_file()


# ── Big Pickle client ─────────────────────────────────────────────────────────

class PickleAgent:
    def __init__(self, session_data: dict = None):
        self.model    = MODEL
        self.base_url = "https://opencode.ai/zen/v1/chat/completions"
        sid           = uuid.uuid4().hex[:20]
        self.headers  = {
            "Authorization":      "Bearer public",
            "Content-Type":       "application/json",
            "x-opencode-client":  "cli",
            "x-opencode-project": "global",
            "x-opencode-request": f"msg_{sid}",
            "x-opencode-session": f"ses_{sid}",
            "User-Agent":         "opencode/1.15.0",
        }
        cwd = os.getcwd()
        system_message = {
            "role": "system",
            "content": (
                "You are a coding agent running on the user's machine.\n"
                f"Current working directory: {cwd}\n"
                f"OS: {sys.platform}\n"
                "You have access to tools: read_file, read_range, search_in_files, "
                "find_file, write_file, edit_file, delete_file, list_dir, "
                "run_command, request_url.\n"
                "Always use tools to interact with the filesystem. "
                "Never guess file contents — read them first.\n"
                "For run_command, always provide a clear reason.\n"
                "Use request_url to fetch documentation, APIs, or any URL the user mentions.\n"
                f"The user is called {USER_NAME}."
            )
        }

        if session_data is not None:
            # resume an existing session from its JSON file
            self.session_id   = session_data.get("session_id") or str(uuid.uuid4())
            self.session_file = Path(SESSION_DIR) / f"{self.session_id}.json"
            messages          = session_data.get("messages") or [system_message]
            # refresh the system prompt — the saved one may hold a stale cwd
            if messages and messages[0].get("role") == "system":
                messages = [system_message] + messages[1:]
            else:
                messages = [system_message] + messages
            self.messages     = messages
            self.title        = session_data.get("title") or self._title_from_messages()
        else:
            # fresh session → new UUID → session/<uuid>.json
            self.session_id   = str(uuid.uuid4())
            self.session_file = Path(SESSION_DIR) / f"{self.session_id}.json"
            self.messages     = [system_message]
            self.title        = "untitled"
        self._save()

    def _save(self):
        """Persist the current conversation to this session's JSON file."""
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": self.session_id,
            "title":      self.title,
            "model":      self.model,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "messages":   self.messages,
        }
        self.session_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _append(self, message: dict):
        """Append a message to the conversation and persist it right away."""
        self.messages.append(message)
        if message.get("role") == "user" and self.title == "untitled":
            self.title = self._title_from_messages()
        self._save()

    def _title_from_messages(self) -> str:
        """Derive a short title from the first user message."""
        for m in self.messages:
            if m.get("role") == "user":
                text = " ".join(str(m.get("content", "")).split())
                return text[:60] or "untitled"
        return "untitled"

    @staticmethod
    def list_sessions() -> list:
        """Return all saved sessions as [{session_id, title, created_at}, ...] (newest first)."""
        folder = Path(SESSION_DIR)
        if not folder.exists():
            return []
        sessions = []
        for f in folder.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            sessions.append({
                "session_id": data.get("session_id") or f.stem,
                "title":      data.get("title") or "untitled",
                "created_at": data.get("created_at") or "",
            })
        sessions.sort(key=lambda s: s["created_at"], reverse=True)
        return sessions

    @staticmethod
    def load_session(session_id: str) -> dict:
        """Load a session JSON by its UUID; returns None if missing/corrupt."""
        f = Path(SESSION_DIR) / f"{session_id}.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _call_api(self) -> dict:
        """Single API call, returns the response dict.

        Retries up to 3 times with backoff on transient failures (429, 5xx,
        network timeouts) before giving up.
        """
        import time
        last_exc: Exception = None
        for attempt in range(1, 4):
            try:
                with console.status(f"[{C_ACCENT}]thinking…[/]", spinner="dots"):
                    client_kwargs = {"timeout": 120}
                    entry = _pick_proxy()
                    if entry:
                        mounts = _build_httpx_mounts(entry)
                        if mounts:
                            client_kwargs["mounts"] = mounts
                    with httpx.Client(**client_kwargs) as client:
                        resp = client.post(
                            self.base_url,
                            # fresh request id per call (the session id stays constant)
                            headers={**self.headers, "x-opencode-request": f"msg_{uuid.uuid4().hex[:20]}"},
                            json={
                                "model":        self.model,
                                "messages":     self.messages,
                                "tools":        TOOLS,
                                "tool_choice":  "auto",
                            },
                        )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                    console.print(
                        f"  [{C_WARN}]API error {resp.status_code} — retrying ({attempt}/3)…[/]"
                    )
                    time.sleep(2 * attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                console.print(
                    f"  [{C_WARN}]network error: {e} — retrying ({attempt}/3)…[/]"
                )
                time.sleep(2 * attempt)
        if last_exc is not None:
            raise last_exc
        raise httpx.TransportError("API call failed after 3 attempts")

    MAX_TOOL_TURNS = 25  # guard against the model looping forever on tool calls

    def run_turn(self, user_input: str):
        """Run one full agent turn (may involve multiple tool calls)."""
        self._append({"role": "user", "content": user_input})

        tool_turns = 0
        while True:
            data = self._call_api()
            try:
                choice  = data["choices"][0]
                message = choice["message"]
            except (KeyError, IndexError, TypeError) as e:
                # unexpected API response shape — never crash the CLI over it
                console.print(f"[{C_WARN}]unexpected API response:[/] {e}")
                console.print(f"[{C_MUTED}]{str(data)[:500]}[/]")
                return
            reason = choice.get("finish_reason", "")

            # always record the assistant message
            self._append(message)

            # ── plain text reply ──────────────────────────────────────────
            if message.get("content"):
                console.print()
                console.print(Panel(
                    Markdown(message["content"]),
                    title=f"[{C_AI}]🥒 big-pickle[/]",
                    border_style=C_ACCENT,
                    padding=(1, 2)
                ))

            # ── tool calls ───────────────────────────────────────────────
            if reason == "tool_calls" or message.get("tool_calls"):
                tool_calls = message.get("tool_calls", [])
                if not tool_calls:
                    # finish_reason says tool_calls but none present —
                    # stop instead of looping forever
                    break

                tool_turns += 1
                if tool_turns > self.MAX_TOOL_TURNS:
                    console.print(
                        f"  [{C_WARN}]stopping after {self.MAX_TOOL_TURNS} tool-call rounds "
                        "— possible loop.[/]"
                    )
                    break

                for tc in tool_calls:
                    try:
                        fn_name = tc["function"]["name"]
                        fn_args = json.loads(tc["function"]["arguments"] or "{}")
                        tc_id   = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                    except (KeyError, TypeError, json.JSONDecodeError) as e:
                        # malformed tool call — report it back as a tool result
                        # instead of crashing the whole CLI
                        fn_name = "?"
                        fn_args = {}
                        tc_id   = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                        result  = f"ERROR: malformed tool call: {type(e).__name__}: {e}"
                        console.print()
                        console.print(Rule(f"[{C_TOOL}]tool · {fn_name}[/]", style="yellow"))
                        self._append({
                            "role":         "tool",
                            "tool_call_id": tc_id,
                            "content":      str(result),
                        })
                        continue

                    console.print()
                    console.print(Rule(f"[{C_TOOL}]tool · {fn_name}[/]", style="yellow"))

                    fn = TOOL_MAP.get(fn_name)
                    if fn:
                        try:
                            result = _call_tool(fn, fn_args)
                        except Exception as e:
                            # never let one bad tool call crash the whole agent
                            result = f"ERROR: {type(e).__name__}: {e}"
                    else:
                        result = f"ERROR: unknown tool {fn_name}"

                    # feed result back
                    self._append({
                        "role":         "tool",
                        "tool_call_id": tc_id,
                        "content":      str(result),
                    })

                # loop back — let the model react to tool results
                continue

            # ── done ─────────────────────────────────────────────────────
            break

    def reset(self):
        # keep the old session file on disk and start a fresh session
        self.session_id   = str(uuid.uuid4())
        self.session_file = Path(SESSION_DIR) / f"{self.session_id}.json"
        self.messages     = [self.messages[0]]  # keep system prompt
        self.title        = "untitled"
        self._save()
        console.print(f"  [{C_MUTED}]session cleared. new session: {self.session_id}[/]")


# ── Session picker (python pickle.py load) ─────────────────────────────────────

def choose_session() -> dict:
    """Show all saved sessions and let the user strictly pick one to load.

    Returns the chosen session dict (parsed from its JSON file), or None if
    the user cancels or there are no sessions to choose from.
    """
    sessions = PickleAgent.list_sessions()
    if not sessions:
        console.print(f"  [{C_WARN}]no saved sessions found in {SESSION_DIR}/[/]")
        return None

    console.print()
    console.print(Rule("[bold]saved sessions[/]", style="yellow"))
    for i, s in enumerate(sessions, 1):
        console.print(
            f"  [cyan]{i:>2})[/] [{C_FILE}]{s['session_id']}[/]  "
            f"{s['title']}  [{C_MUTED}]({s['created_at']})[/]"
        )
    console.print()

    while True:
        choice = Prompt.ask("choose a session (number or uuid, Enter to cancel)").strip()
        if not choice:
            return None
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(sessions):
                return PickleAgent.load_session(sessions[n - 1]["session_id"])
        else:
            # accept a full or partial uuid
            for s in sessions:
                if s["session_id"].startswith(choice.lower()):
                    return PickleAgent.load_session(s["session_id"])
        console.print(f"  [{C_WARN}]invalid choice — pick one of the listed sessions.[/]")


# ── CLI loop ──────────────────────────────────────────────────────────────────

HELP_TEXT = """
[bold green]pickle[/] — free coding agent · Big Pickle via opencode.ai/zen

[bold]cli:[/]
  [cyan]python pickle.py[/]        start a new session
  [cyan]python pickle.py load[/]   pick a saved session to resume

[bold]commands:[/]
  [cyan]/reset[/]   clear conversation history (starts a new session)
  [cyan]/session[/]  show current session id + saved file
  [cyan]/cwd[/]     show current working directory
  [cyan]/cd[/] [dim]<path>[/]  change working directory
  [cyan]/proxy[/]   enable proxy — asks for the proxy JSON file path
  [cyan]/proxy off[/]  disable the proxy
  [cyan]/help[/]    show this message
  [cyan]/exit[/]    quit

[bold]tips:[/]
  • Ask it to read, write, edit, or delete files
  • search_in_files / find_file / read_range find & inspect code fast
  • Destructive commands (rm/rmdir) always ask for confirmation
  • Works best with clear, specific requests
  • Proxy file is a JSON list of dicts like
    [{"http": "host:port", "https": "host:port"}, ...] — enable it at
    startup or anytime with /proxy
"""


def banner():
    console.print(Panel(
        "[bold green]🥒  pickle[/]  [dim]coding agent · Big Pickle model[/]\n"
        "[dim]free · opencode.ai/zen · type /help for commands[/]",
        border_style="green",
        padding=(0, 2)
    ))
    console.print()


def main():
    global PROXY_LIST, USE_PROXY
    banner()

    # ask about the proxy first — applies to all outbound requests
    PROXY_LIST = setup_proxy()
    if PROXY_LIST:
        USE_PROXY = True

    # python pickle.py load → pick a saved session to resume
    session_data = None
    if len(sys.argv) > 1 and sys.argv[1] == "load":
        session_data = choose_session()
        if session_data is None:
            console.print(f"  [{C_MUTED}]no session chosen — starting a fresh one.[/]")

    agent = PickleAgent(session_data)
    if session_data is not None:
        console.print(f"  [{C_MUTED}]resumed session {agent.session_id}  →  {agent.session_file}[/]")
    else:
        console.print(f"  [{C_MUTED}]session {agent.session_id}  →  {agent.session_file}[/]")

    while True:
        try:
            console.print()
            user_input = Prompt.ask(f"[{C_USER}]you[/]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print(f"\n[{C_MUTED}]bye.[/]")
            break

        if not user_input:
            continue

        # ── built-in commands ─────────────────────────────────────────────
        if user_input == "/exit":
            console.print(f"[{C_MUTED}]bye.[/]")
            break
        elif user_input == "/help":
            console.print(HELP_TEXT)
            continue
        elif user_input == "/reset":
            agent.reset()
            continue
        elif user_input == "/cwd":
            console.print(f"[{C_FILE}]{os.getcwd()}[/]")
            continue
        elif user_input == "/session":
            console.print(f"[{C_FILE}]{agent.session_id}[/]  [{C_MUTED}](saved to {agent.session_file})[/]")
            continue
        elif user_input == "/proxy off":
            USE_PROXY = False
            console.print(f"  [{C_WARN}]proxy OFF.[/]")
            continue
        elif user_input == "/proxy":
            proxies = _load_proxy_file()
            if proxies is not None:
                USE_PROXY = True
                PROXY_LIST = proxies
                console.print(
                    f"  [{C_ACCENT}]✓ proxy ON: {len(proxies)} entr{'y' if len(proxies) == 1 else 'ies'}[/]"
                )
            else:
                USE_PROXY = False
                console.print(f"  [{C_WARN}]proxy OFF — no proxy loaded.[/]")
            continue
        elif user_input == "/cd" or user_input.startswith("/cd "):
            parts = user_input.split(maxsplit=1)
            target = parts[1] if len(parts) > 1 else str(Path.home())
            try:
                os.chdir(target)
                console.print(f"[{C_FILE}]{os.getcwd()}[/]")
            except FileNotFoundError:
                console.print(f"[{C_WARN}]not found: {target}[/]")
            continue

        # ── agent turn ────────────────────────────────────────────────────
        try:
            agent.run_turn(user_input)
        except httpx.HTTPStatusError as e:
            console.print(f"[{C_WARN}]API error {e.response.status_code}:[/] {e.response.text[:300]}")
        except httpx.RequestError as e:
            console.print(f"[{C_WARN}]network error:[/] {e}")
        except KeyboardInterrupt:
            console.print(f"\n[{C_MUTED}]interrupted.[/]")


if __name__ == "__main__":
    main()
