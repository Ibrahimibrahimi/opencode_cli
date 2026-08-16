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
import inspect
import subprocess
import sys
import shutil
import datetime
import base64
import hashlib
import secrets
import socket
import string
import tarfile
import urllib.parse
import zipfile
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
MAX_WEB_CHARS      = 20_000  # chars returned to the model per websearch call

TODO_FILE          = "TODO.md"   # file the agent writes its todo list to
MAX_GREP_CHARS     = 20_000  # chars returned to the model per search/find call
MAX_COMMAND_CHARS  = 20_000  # chars returned to the model per run_command call

MODEL              = "big-pickle"
USER_NAME          = "Ibrahim"

# Absolute path under ~/UserDirs/session (independent of the working directory).
SESSION_DIR        = str(Path.home() / "UserDirs" / "session")

# ── palette ──────────────────────────────────────────────────────────────────
C_ACCENT  = "green"
C_USER    = "bold cyan"
C_AI      = "bold green"
C_TOOL    = "bold yellow"
C_INFO    = "yellow"
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
    },
    {
        "type": "function",
        "function": {
            "name": "websearch",
            "description": (
                "Search the web using DuckDuckGo (no API key required). "
                "Use this to find current information, docs, answers, and resources "
                "beyond your training data. Returns titles, URLs, and snippets. "
                "Use request_url afterwards to fetch the full content of a promising link."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string", "description": "Search query."},
                    "max_results": {"type": "integer", "description": "Max results to return (1–10). Default 5."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todowrite",
            "description": (
                "Write the FULL todo list for the current task to a markdown file "
                "(default TODO.md in the working directory) and return the current state. "
                "Call this BEFORE starting implementation, and update it as you make progress. "
                "Each item is {\"content\": str, \"status\": \"pending\"|\"in_progress\"|\"completed\"} — "
                "the whole list replaces whatever is in the file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {"type": "array", "description": "Full list of todo items (replaces the current list).",
                              "items": {"type": "object",
                                        "properties": {
                                            "content": {"type": "string", "description": "What needs to be done."},
                                            "status":  {"type": "string", "enum": ["pending", "in_progress", "completed"]}
                                        },
                                        "required": ["content"]}},
                    "path":   {"type": "string", "description": "File to write todos to. Default TODO.md in cwd."}
                },
                "required": ["todos"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todolist",
            "description": "Read the current todo list back from the todo file (default TODO.md).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Todo file to read. Default TODO.md in cwd."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "question",
            "description": (
                "Ask the user a question with multiple-choice options and wait for their answer. "
                "Use this to clarify requirements, preferences, or get decisions mid-task. "
                "The user can pick an option or type a custom answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question to ask."},
                    "options":  {"type": "array", "items": {"type": "string"}, "description": "Optional choices to offer."},
                    "header":   {"type": "string", "description": "Optional short context heading shown above the question."}
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "archive_create",
            "description": "Create a ZIP or TAR archive from files/directories. Format is auto-detected from the output_path extension (.zip, .tar, .tar.gz/.tgz, .tar.bz2/.tbz2).",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_paths": {"type": "array", "items": {"type": "string"}, "description": "Files/directories to include."},
                    "output_path":  {"type": "string", "description": "Destination archive path, e.g. 'project.zip' or 'backup.tar.gz'."}
                },
                "required": ["source_paths", "output_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "archive_extract",
            "description": "Extract a ZIP or TAR archive into a directory (format auto-detected).",
            "parameters": {
                "type": "object",
                "properties": {
                    "archive_path": {"type": "string", "description": "Path to the archive file."},
                    "extract_to":   {"type": "string", "description": "Destination directory."}
                },
                "required": ["archive_path", "extract_to"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hash_text",
            "description": "Compute the md5/sha256/sha512 hash of a string. Useful for checksums and integrity checks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text":      {"type": "string", "description": "String to hash."},
                    "algorithm": {"type": "string", "enum": ["md5", "sha256", "sha512"], "description": "Hash algorithm. Default sha256."}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "hash_file",
            "description": "Compute the md5/sha256/sha512 hash of a file's contents (streams large files).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file."},
                    "algorithm": {"type": "string", "enum": ["md5", "sha256", "sha512"], "description": "Hash algorithm. Default sha256."}
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "encode_text",
            "description": "Encode a string: base64, url (percent-encoding), or hex.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data":     {"type": "string", "description": "Text to encode."},
                    "encoding": {"type": "string", "enum": ["base64", "url", "hex"], "description": "Encoding scheme. Default base64."}
                },
                "required": ["data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "decode_text",
            "description": "Decode a base64/url/hex encoded string back to text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data":     {"type": "string", "description": "Encoded text."},
                    "encoding": {"type": "string", "enum": ["base64", "url", "hex"], "description": "Encoding scheme. Default base64."}
                },
                "required": ["data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_token",
            "description": "Generate a cryptographically secure random string (e.g. API keys, secrets).",
            "parameters": {
                "type": "object",
                "properties": {
                    "length":        {"type": "integer", "description": "Length 1–1000. Default 32."},
                    "character_set": {"type": "string", "enum": ["alphanumeric", "letters", "digits", "ascii"], "description": "Pool of characters. Default alphanumeric."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_uuid",
            "description": "Generate a UUID (version 1 or 4).",
            "parameters": {
                "type": "object",
                "properties": {
                    "version": {"type": "integer", "enum": [1, 4], "description": "UUID version. Default 4 (random)."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "directory_tree",
            "description": "Render a recursive directory tree (like the `tree` command) with a depth limit; skips hidden files and common junk dirs (node_modules, .git, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":           {"type": "string", "description": "Root directory. Default '.'."},
                    "max_depth":      {"type": "integer", "description": "Max depth to descend. Default 4."},
                    "include_hidden": {"type": "boolean", "description": "Include dotfiles. Default false."},
                    "skip_ignored":   {"type": "boolean", "description": "Skip node_modules/.git/etc. Default true."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_info",
            "description": "Get metadata about a file or directory: size, type, modified time, permissions, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file or directory."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_hostname",
            "description": "Resolve a hostname to its IPv4/IPv6 addresses (DNS lookup).",
            "parameters": {
                "type": "object",
                "properties": {
                    "hostname": {"type": "string", "description": "Hostname to resolve, e.g. 'example.com'."}
                },
                "required": ["hostname"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reverse_dns",
            "description": "Reverse DNS lookup: map an IP address to its hostname (PTR record).",
            "parameters": {
                "type": "object",
                "properties": {
                    "ip_address": {"type": "string", "description": "IP address, e.g. '8.8.8.8'."}
                },
                "required": ["ip_address"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_port",
            "description": "Check whether a TCP port is open on a host.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host":    {"type": "string", "description": "Hostname or IP."},
                    "port":    {"type": "integer", "description": "TCP port 1–65535."},
                    "timeout": {"type": "integer", "description": "Timeout seconds 1–30. Default 5."}
                },
                "required": ["host", "port"]
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

        req_kwargs = {}
        if method.upper() == "POST" and body:
            req_kwargs["content"] = body.encode()

        with httpx.Client(headers=req_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True, trust_env=False) as client:
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


# ── Web search (DuckDuckGo, no API key) ───────────────────────────────────────

def _ddg_real_url(href: str) -> str:
    """Decode DuckDuckGo's redirect wrapper (?uddg=...) into the real URL."""
    import re as _re
    from urllib.parse import unquote as _unquote
    if "uddg=" in href:
        m = _re.search(r"[?&]uddg=([^&]+)", href)
        if m:
            return _unquote(m.group(1))
    return href


def tool_websearch(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo's HTML endpoint (no API key needed)."""
    from html.parser import HTMLParser

    max_results = max(1, min(int(max_results or 5), 10))
    _status(f"searching  {query}")

    class _DuckParser(HTMLParser):
        """Pull (title, href, snippet) triples out of the DDG results page."""

        def __init__(self):
            super().__init__()
            self.results   = []
            self._cur      = None   # last seen result, so the snippet can attach
            self._in_title = False
            self._in_snip  = False
            self._buf      = []

        def handle_starttag(self, tag, attrs):
            cls = dict(attrs).get("class", "").split()
            if tag == "a" and "result__a" in cls:
                self._cur      = {"href": dict(attrs).get("href", "")}
                self._in_title = True
                self._buf      = []
            elif tag == "a" and "result__snippet" in cls:
                self._in_snip = True
                self._buf     = []

        def handle_data(self, data):
            if self._in_title or self._in_snip:
                self._buf.append(data)

        def handle_endtag(self, tag):
            if tag == "a" and self._in_title:
                title = " ".join("".join(self._buf).split())
                self._in_title = False
                if title and self._cur is not None:
                    self._cur["title"] = title
                    self.results.append(self._cur)
            elif tag == "a" and self._in_snip and self._cur is not None:
                self._cur["snippet"] = " ".join("".join(self._buf).split())
                self._in_snip = False

    try:
        with httpx.Client(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            trust_env=False,
            headers={
                "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            resp = client.get("https://html.duckduckgo.com/html/", params={"q": query})
        resp.raise_for_status()
    except httpx.TimeoutException:
        _status("✗ timeout", end=True)
        return f"ERROR: websearch timed out after {REQUEST_TIMEOUT}s"
    except Exception as e:
        _status(f"✗ error: {e}", end=True)
        return f"ERROR: {e}"

    parser = _DuckParser()
    parser.feed(resp.text)
    results = parser.results[:max_results]

    if not results:
        _status(f"✓ no results for {query!r}", end=True)
        return f"(no web results for {query!r})"

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', '')}")
        lines.append(f"   {_ddg_real_url(r.get('href', ''))}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
        lines.append("")
    out = "\n".join(lines).strip()
    if len(out) > MAX_WEB_CHARS:
        out = out[:MAX_WEB_CHARS] + "\n… (truncated)"

    plural = "" if len(results) == 1 else "s"
    console.print(Panel(
        out[:3000] + ("…" if len(out) > 3000 else ""),
        title=f"[{C_FILE}]websearch[/] · {len(results)} result{plural} for {query!r}",
        border_style=C_ACCENT
    ))
    _status(f"✓ {len(results)} result{plural} for {query!r}", end=True)
    return out


# ── Todo list (persisted to a file) ───────────────────────────────────────────

def _read_todos(path: str) -> list:
    """Parse a markdown checklist file into [{content, status}, ...]."""
    import re as _re
    p = Path(path).expanduser()
    if not p.exists():
        return []
    todos = []
    marks = {" ": "pending", "-": "in_progress", "~": "in_progress",
             "x": "completed", "X": "completed"}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _re.match(r"^\s*[-*]\s*\[([ xX~-])\]\s*(.+?)\s*$", line)
        if m:
            todos.append({"content": m.group(2), "status": marks.get(m.group(1), "pending")})
    return todos


def _normalize_todos(todos) -> list:
    """Normalize raw model input into [{content, status}, ...]."""
    out = []
    for t in todos or []:
        if isinstance(t, str):
            content, status = t.strip(), "pending"
        elif isinstance(t, dict):
            content = str(t.get("content", "")).strip()
            status  = str(t.get("status", "pending")).lower()
            if t.get("done") is not None:
                status = "completed" if t.get("done") else "pending"
            elif t.get("completed") is not None:
                status = "completed" if t.get("completed") else "pending"
            if status not in ("pending", "in_progress", "completed"):
                status = "pending"
        else:
            continue
        if content:
            out.append({"content": content, "status": status})
    return out


def _render_todos(todos: list) -> str:
    if not todos:
        return "(empty todo list)"
    marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
    return "\n".join(
        f"{i}. {marks[t['status']]} {t['content']}" for i, t in enumerate(todos, 1)
    )


def tool_todowrite(todos: list = None, path: str = TODO_FILE) -> str:
    """Write the full todo list to a markdown file and return the current state."""
    p = Path(path).expanduser()
    items = _normalize_todos(todos) if todos is not None else _read_todos(path)

    lines = ["# TODO", ""]
    marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
    for t in items:
        lines.append(f"- {marks[t['status']]} {t['content']}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rendered = _render_todos(items)
    console.print(Panel(
        rendered[:3000],
        title=f"[{C_FILE}]{p}[/]  [{C_MUTED}](todos)[/]",
        border_style=C_ACCENT
    ))
    _status(f"✓ todos saved to {p} ({len(items)} items)", end=True)
    return f"todos saved to {path}:\n{rendered}"


def tool_todolist(path: str = TODO_FILE) -> str:
    """Read the current todo list back from the file."""
    p = Path(path).expanduser()
    items = _read_todos(path)
    if not items:
        _status(f"✓ no todos in {p}", end=True)
        return f"(no todos in {path})"
    rendered = _render_todos(items)
    _status(f"✓ read todos from {p}", end=True)
    return f"todos in {path}:\n{rendered}"


# ── Ask the user a question ───────────────────────────────────────────────────

def tool_question(question: str, options: list = None, header: str = "") -> str:
    """Ask the user a multiple-choice question and return their answer."""
    console.print()
    if header:
        console.print(f"[bold {C_ACCENT}]{header}[/]")
    console.print(f"[{C_USER}]❓ {question}[/]")
    options = [str(o) for o in (options or [])]
    if options:
        for i, opt in enumerate(options, 1):
            console.print(f"  [cyan]{i}.[/] {opt}")
        console.print(f"  [{C_MUTED}]pick a number or type your own answer[/]")
    try:
        answer = Prompt.ask("  answer").strip()
    except (KeyboardInterrupt, EOFError):
        return "user cancelled the question (no answer)"
    if options and answer.isdigit():
        n = int(answer)
        if 1 <= n <= len(options):
            answer = options[n - 1]
    return f"user answered: {answer}"


# ── Archive tools (zip / tar / gzip — stdlib only) ────────────────────────────

def _detect_archive_format(path: str) -> str:
    """Return 'zip' / 'tar' / 'tar.gz' / 'tar.bz2' from a filename, or ''."""
    p = str(path).lower()
    for ext, fmt in ((".tar.gz", "tar.gz"), (".tgz", "tar.gz"),
                     (".tar.bz2", "tar.bz2"), (".tbz2", "tar.bz2"),
                     (".zip", "zip"), (".tar", "tar")):
        if p.endswith(ext):
            return fmt
    return ""


def tool_archive_create(source_paths: list, output_path: str) -> str:
    """Create a ZIP or TAR archive (format auto-detected from the extension)."""
    if not source_paths or not isinstance(source_paths, list):
        return "ERROR: source_paths must be a non-empty list of paths"
    fmt = _detect_archive_format(output_path)
    if not fmt:
        return ("ERROR: unsupported archive extension on output_path — "
                "use .zip, .tar, .tar.gz, .tgz, .tar.bz2 or .tbz2")
    out = Path(output_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and ASK_BEFORE_WRITE and not Confirm.ask(
            f"  [{C_WARN}]Overwrite existing archive {out}?[/]", default=False):
        return "CANCELLED by user."
    try:
        added = []
        if fmt == "zip":
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
                for src in source_paths:
                    s = Path(src).expanduser()
                    if s.is_file():
                        zf.write(s, s.name)
                        added.append(str(s))
                    elif s.is_dir():
                        base = s.parent
                        for f in sorted(s.rglob("*")):
                            if f.is_file():
                                zf.write(f, f.relative_to(base).as_posix())
                                added.append(str(f))
        else:
            mode = {"tar": "w", "tar.gz": "w:gz", "tar.bz2": "w:bz2"}[fmt]
            with tarfile.open(out, mode) as tf:
                for src in source_paths:
                    s = Path(src).expanduser()
                    if s.exists():
                        tf.add(s, arcname=s.name)
                        added.append(str(s))
        size = out.stat().st_size
        msg = f"created {fmt} archive {out} with {len(added)} file(s) ({size} bytes)"
        _status(f"✓ {msg}", end=True)
        return msg
    except Exception as e:
        _status(f"✗ error: {e}", end=True)
        return f"ERROR: {e}"


def tool_archive_extract(archive_path: str, extract_to: str) -> str:
    """Extract a ZIP or TAR archive (format auto-detected, path-traversal safe)."""
    p = Path(archive_path).expanduser()
    if not p.exists():
        return f"ERROR: archive not found: {archive_path}"
    fmt = _detect_archive_format(str(p))
    if not fmt:
        return f"ERROR: unsupported archive type: {archive_path}"
    out = Path(extract_to).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    try:
        if fmt == "zip":
            with zipfile.ZipFile(p, "r") as zf:
                base = str(out.resolve())
                for member in zf.namelist():
                    if not str((out / member).resolve()).startswith(base):
                        raise ValueError(f"unsafe path in archive: {member}")
                zf.extractall(out)
                n = len(zf.namelist())
        else:
            with tarfile.open(p, "r:*") as tf:
                tf.extractall(out)
                n = len(tf.getnames())
        msg = f"extracted {n} entr{'y' if n == 1 else 'ies'} from {p} to {out}"
        _status(f"✓ {msg}", end=True)
        return msg
    except Exception as e:
        _status(f"✗ error: {e}", end=True)
        return f"ERROR: {e}"


# ── Crypto tools (hashing / encoding / generation) ────────────────────────────

_ALGORITHMS = ("md5", "sha256", "sha512")


def tool_hash_text(text: str, algorithm: str = "sha256") -> str:
    """Compute md5/sha256/sha512 hash of a string."""
    alg = (algorithm or "sha256").lower()
    if alg not in _ALGORITHMS:
        return "ERROR: algorithm must be one of: md5, sha256, sha512"
    h = hashlib.new(alg, text.encode("utf-8")).hexdigest()
    console.print(Panel(
        f"[{C_MUTED}]{len(text)} chars → {alg}[/]\n[h]{h}[/]",
        title=f"[{C_FILE}]hash_text[/]",
        border_style=C_ACCENT
    ))
    return f"{alg} of {len(text)} chars:\n{h}"


def tool_hash_file(file_path: str, algorithm: str = "sha256") -> str:
    """Compute md5/sha256/sha512 hash of a file's contents (streams large files)."""
    p = Path(file_path).expanduser()
    if not p.is_file():
        return f"ERROR: file not found: {file_path}"
    alg = (algorithm or "sha256").lower()
    if alg not in _ALGORITHMS:
        return "ERROR: algorithm must be one of: md5, sha256, sha512"
    h = hashlib.new(alg)
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    size = p.stat().st_size
    msg = f"{alg} of {p} ({size} bytes):\n{h.hexdigest()}"
    _status(f"✓ hashed {p} ({size} bytes)", end=True)
    return msg


def tool_encode_text(data: str, encoding: str = "base64") -> str:
    """Encode a string: base64, url (percent-encoding), or hex."""
    enc = (encoding or "base64").lower()
    try:
        if enc == "base64":
            out = base64.b64encode(data.encode("utf-8")).decode("ascii")
        elif enc in ("url", "percent"):
            out = urllib.parse.quote(data, safe="")
        elif enc == "hex":
            out = data.encode("utf-8").hex()
        else:
            return "ERROR: encoding must be one of: base64, url, hex"
    except Exception as e:
        return f"ERROR: {e}"
    return f"{enc}-encoded ({len(data)} chars → {len(out)} chars):\n{out}"


def tool_decode_text(data: str, encoding: str = "base64") -> str:
    """Decode a base64/url/hex encoded string back to text."""
    enc = (encoding or "base64").lower()
    try:
        if enc == "base64":
            out = base64.b64decode(data.encode("ascii")).decode("utf-8", errors="replace")
        elif enc in ("url", "percent"):
            out = urllib.parse.unquote(data)
        elif enc == "hex":
            out = bytes.fromhex(data.strip()).decode("utf-8", errors="replace")
        else:
            return "ERROR: encoding must be one of: base64, url, hex"
    except Exception as e:
        return f"ERROR: failed to {enc}-decode: {e}"
    return f"{enc}-decoded ({len(data)} chars → {len(out)} chars):\n{out}"


def tool_generate_token(length: int = 32, character_set: str = "alphanumeric") -> str:
    """Generate a cryptographically secure random string."""
    try:
        length = int(length)
    except (TypeError, ValueError):
        return "ERROR: length must be an integer"
    if length < 1 or length > 1000:
        return "ERROR: length must be between 1 and 1000"
    cs = (character_set or "alphanumeric").lower()
    pools = {
        "alphanumeric": string.ascii_letters + string.digits,
        "letters":      string.ascii_letters,
        "digits":       string.digits,
        "ascii":        string.ascii_letters + string.digits + string.punctuation,
    }
    if cs not in pools:
        return "ERROR: character_set must be one of: alphanumeric, letters, digits, ascii"
    tok = "".join(secrets.choice(pools[cs]) for _ in range(length))
    return f"random {cs} token ({length} chars):\n{tok}"


def tool_generate_uuid(version: int = 4) -> str:
    """Generate a UUID (version 1 or 4)."""
    try:
        version = int(version)
    except (TypeError, ValueError):
        return "ERROR: version must be 1 or 4"
    if version == 1:
        u = uuid.uuid1()
    elif version == 4:
        u = uuid.uuid4()
    else:
        return "ERROR: version must be 1 or 4"
    return f"uuid{version}: {u}"


# ── Network tools (DNS / ports — stdlib socket) ───────────────────────────────

def tool_resolve_hostname(hostname: str) -> str:
    """Resolve a hostname to its IPv4/IPv6 addresses."""
    hostname = hostname.strip()
    if not hostname:
        return "ERROR: hostname is required"
    try:
        ipv4, ipv6 = [], []
        for info in socket.getaddrinfo(hostname, None):
            family, _, _, _, sockaddr = info
            ip = sockaddr[0]
            if family == socket.AF_INET and ip not in ipv4:
                ipv4.append(ip)
            elif family == socket.AF_INET6 and ip not in ipv6:
                ipv6.append(ip)
    except socket.gaierror as e:
        return f"ERROR: failed to resolve {hostname!r}: {e}"
    except Exception as e:
        return f"ERROR: {e}"
    out = (f"{hostname}:\n"
           f"  ipv4: {', '.join(ipv4) or '(none)'}\n"
           f"  ipv6: {', '.join(ipv6) or '(none)'}")
    _status(f"✓ resolved {hostname}", end=True)
    return out


def tool_reverse_dns(ip_address: str) -> str:
    """Reverse DNS lookup: IP → hostname (PTR record)."""
    ip = ip_address.strip()
    if not ip:
        return "ERROR: ip_address is required"
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
        _status(f"✓ PTR for {ip}", end=True)
        return f"PTR for {ip}:\n{hostname}"
    except socket.herror:
        return f"no reverse DNS record for {ip}"
    except Exception as e:
        return f"ERROR: {e}"


def tool_check_port(host: str, port: int, timeout: int = 5) -> str:
    """Check whether a TCP port is open on a host."""
    try:
        port = int(port)
        timeout = int(timeout)
    except (TypeError, ValueError):
        return "ERROR: port and timeout must be integers"
    if port < 1 or port > 65535:
        return "ERROR: port must be between 1 and 65535"
    timeout = max(1, min(timeout, 30))
    host = host.strip()
    if not host:
        return "ERROR: host is required"
    import time as _time
    start = _time.time()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            result = s.connect_ex((host, port))
        ms = round((_time.time() - start) * 1000, 2)
    except socket.timeout:
        return f"port {port} on {host}: timeout after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"
    state = "open" if result == 0 else "closed/filtered"
    _status(f"✓ port check {host}:{port} ({state})", end=True)
    return f"port {port} on {host}: {state} ({ms} ms)"


# ── File system extras (tree + info) ──────────────────────────────────────────

def _human_size(n: int) -> str:
    """Format a byte count as B/KB/MB/GB/TB."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def tool_directory_tree(path: str = ".", max_depth: int = 4,
                        include_hidden: bool = False, skip_ignored: bool = True) -> str:
    """Render a recursive directory tree (like the `tree` command), depth-limited."""
    root = Path(path or ".").expanduser()
    if not root.is_dir():
        return f"ERROR: directory not found: {path}"
    if max_depth is None:
        max_depth = 99
    try:
        max_depth = int(max_depth)
    except (TypeError, ValueError):
        return "ERROR: max_depth must be an integer"
    max_depth = max(0, max_depth)

    lines = [root.name + "/"]

    def _walk(current: Path, prefix: str, depth: int):
        if depth >= max_depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as e:
            lines.append(f"{prefix}[error: {e}]")
            return
        visible = []
        for e in entries:
            if not include_hidden and e.name.startswith("."):
                continue
            if skip_ignored and e.is_dir() and e.name in _DEFAULT_IGNORE_DIRS:
                continue
            visible.append(e)
        for i, e in enumerate(visible):
            last = i == len(visible) - 1
            branch = "└── " if last else "├── "
            lines.append(f"{prefix}{branch}{e.name}{'/' if e.is_dir() else ''}")
            if e.is_dir():
                _walk(e, prefix + ("    " if last else "│   "), depth + 1)

    _walk(root, "", 0)
    out = "\n".join(lines)
    if len(out) > MAX_GREP_CHARS:
        out = out[:MAX_GREP_CHARS] + "\n… (truncated)"
    console.print(Panel(
        out[:3000] + ("…" if len(out) > 3000 else ""),
        title=f"[{C_FILE}]directory_tree[/] · {root}",
        border_style=C_ACCENT
    ))
    _status(f"✓ tree of {root} ({len(lines)} lines)", end=True)
    return out


def tool_file_info(path: str) -> str:
    """Get metadata about a file or directory."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"ERROR: path not found: {path}"
    try:
        st = p.stat()
    except OSError as e:
        return f"ERROR: {e}"
    kind = "directory" if p.is_dir() else ("file" if p.is_file() else "other")
    lines = [
        f"name:        {p.name or str(p)}",
        f"type:        {kind}" + (" (symlink)" if p.is_symlink() else ""),
        f"size:        {_human_size(st.st_size)} ({st.st_size} bytes)",
        f"modified:    {datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds')}",
        f"absolute:    {p.resolve()}",
        f"parent:      {p.parent}",
        f"permissions: {oct(st.st_mode & 0o777)}",
    ]
    if p.is_file():
        lines.insert(3, f"suffix:      {p.suffix or '(none)'}")
    _status(f"✓ info {p}", end=True)
    return "\n".join(lines)


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
    "websearch":       tool_websearch,
    "todowrite":       tool_todowrite,
    "todolist":        tool_todolist,
    "question":        tool_question,
    "archive_create":  tool_archive_create,
    "archive_extract": tool_archive_extract,
    "hash_text":       tool_hash_text,
    "hash_file":       tool_hash_file,
    "encode_text":     tool_encode_text,
    "decode_text":     tool_decode_text,
    "generate_token":  tool_generate_token,
    "generate_uuid":   tool_generate_uuid,
    "directory_tree":  tool_directory_tree,
    "file_info":       tool_file_info,
    "resolve_hostname": tool_resolve_hostname,
    "reverse_dns":     tool_reverse_dns,
    "check_port":      tool_check_port,
}

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
                "run_command, request_url, websearch, todowrite, todolist, question, "
                "archive_create, archive_extract, hash_text, hash_file, encode_text, "
                "decode_text, generate_token, generate_uuid, directory_tree, file_info, "
                "resolve_hostname, reverse_dns, check_port.\n"
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

        Retries on transient failures (429, 5xx, network timeouts) with a
        short backoff.
        """
        import time
        last_exc: Exception = None
        attempts = 0
        while True:
            attempts += 1
            try:
                with console.status(f"[{C_ACCENT}]thinking…[/]", spinner="dots"):
                    with httpx.Client(timeout=120, trust_env=False) as client:
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
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                if attempts >= 3:
                    break
                console.print(
                    f"  [{C_WARN}]network error: {e} — retrying ({attempts}/3)…[/]"
                )
                time.sleep(2 * attempts)
                continue

            # ── transient API errors → backoff retry ────────────────────
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
                if attempts >= 3:
                    break
                console.print(
                    f"  [{C_WARN}]API error {resp.status_code} — retrying ({attempts}/3)…[/]"
                )
                time.sleep(2 * attempts)
                continue

            resp.raise_for_status()
            return resp.json()

        if last_exc is not None:
            raise last_exc
        raise httpx.TransportError("API call failed after 3 attempts")

    def run_turn(self, user_input: str):
        """Run one full agent turn (may involve multiple tool calls)."""
        self._append({"role": "user", "content": user_input})

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
  [cyan]/help[/]    show this message
  [cyan]/exit[/]    quit

[bold]tips:[/]
  • Ask it to read, write, edit, or delete files
  • search_in_files / find_file / read_range find & inspect code fast
  • websearch finds things online · todowrite keeps a TODO.md · question asks you
  • archive_create / hash / encode / network / directory_tree utilities included
  • Destructive commands (rm/rmdir) always ask for confirmation
  • Works best with clear, specific requests
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
    banner()

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
