#!/usr/bin/env python3
"""
pickle — a free coding agent powered by Big Pickle (opencode.ai/zen)
Usage: python pickle.py
"""

import httpx
import uuid
import json
import os
import subprocess
import sys
import shutil
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
            "description": "Run a shell command on the user's machine. ALWAYS use this for installs, tests, git, builds. The user will be asked to confirm before execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."},
                    "reason":  {"type": "string", "description": "Short explanation of why this command is needed."}
                },
                "required": ["command", "reason"]
            }
        }
    }
]

# ── Tool implementations ──────────────────────────────────────────────────────

def tool_read_file(path: str) -> str:
    p = Path(path).expanduser()
    console.print(f"  [{C_FILE}]reading[/] {p}")
    if not p.exists():
        return f"ERROR: file not found: {path}"
    if not p.is_file():
        return f"ERROR: not a file: {path}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
        console.print(f"  [{C_MUTED}]{len(content)} chars read[/]")
        return content
    except Exception as e:
        return f"ERROR: {e}"


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

    if not Confirm.ask(f"  [{C_WARN}]Write this file?[/]", default=True):
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

    if not Confirm.ask(f"  [{C_WARN}]Apply this edit?[/]", default=True):
        return "CANCELLED by user."

    p.write_text(updated, encoding="utf-8")
    console.print(f"  [{C_ACCENT}]✓ edited:[/] {p}")
    return f"OK: edit applied to {path}"


def tool_delete_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"ERROR: path not found: {path}"

    console.print(f"  [{C_WARN}]DELETE[/] {p}")
    if not Confirm.ask(f"  [{C_WARN}]Permanently delete {path}?[/]", default=False):
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


def tool_run_command(command: str, reason: str = "") -> str:
    console.print(Panel(
        f"[bold]{command}[/]\n\n[{C_MUTED}]{reason}[/]",
        title=f"[{C_TOOL}]shell command[/]",
        border_style="yellow"
    ))
    if not Confirm.ask(f"  [{C_WARN}]Execute this command?[/]", default=False):
        return "CANCELLED by user."

    console.print(f"  [{C_TOOL}]running…[/]")
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        output = output.strip() or "(no output)"
        console.print(Panel(
            output[:3000] + ("…" if len(output) > 3000 else ""),
            title=f"[{C_MUTED}]exit code {result.returncode}[/]",
            border_style="green" if result.returncode == 0 else "red"
        ))
        return f"exit_code={result.returncode}\n{output}"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 60s"
    except Exception as e:
        return f"ERROR: {e}"


TOOL_MAP = {
    "read_file":   tool_read_file,
    "write_file":  tool_write_file,
    "edit_file":   tool_edit_file,
    "delete_file": tool_delete_file,
    "list_dir":    tool_list_dir,
    "run_command": tool_run_command,
}

# ── Big Pickle client ─────────────────────────────────────────────────────────

class PickleAgent:
    def __init__(self):
        self.model    = "big-pickle"
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
        self.messages = [
            {
                "role": "system",
                "content": (
                    "You are a coding agent running on the user's machine.\n"
                    f"Current working directory: {cwd}\n"
                    f"OS: {sys.platform}\n"
                    "You have access to tools: read_file, write_file, edit_file, "
                    "delete_file, list_dir, run_command.\n"
                    "Always use tools to interact with the filesystem. "
                    "Never guess file contents — read them first.\n"
                    "For run_command, always provide a clear reason.\n"
                    "The user is called Ibrahim."
                )
            }
        ]

    def _call_api(self) -> dict:
        """Single API call, returns the response dict."""
        with console.status(f"[{C_ACCENT}]thinking…[/]", spinner="dots"):
            resp = httpx.post(
                self.base_url,
                headers=self.headers,
                json={
                    "model":    self.model,
                    "messages": self.messages,
                    "tools":    TOOLS,
                    "tool_choice": "auto",
                },
                timeout=120,
            )
        resp.raise_for_status()
        return resp.json()

    def run_turn(self, user_input: str):
        """Run one full agent turn (may involve multiple tool calls)."""
        self.messages.append({"role": "user", "content": user_input})

        while True:
            data    = self._call_api()
            message = data["choices"][0]["message"]
            reason  = data["choices"][0].get("finish_reason", "")

            # always record the assistant message
            self.messages.append(message)

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
                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    fn_args = json.loads(tc["function"]["arguments"])
                    tc_id   = tc["id"]

                    console.print()
                    console.print(Rule(f"[{C_TOOL}]tool · {fn_name}[/]", style="yellow"))

                    fn = TOOL_MAP.get(fn_name)
                    if fn:
                        result = fn(**fn_args)
                    else:
                        result = f"ERROR: unknown tool {fn_name}"

                    # feed result back
                    self.messages.append({
                        "role":         "tool",
                        "tool_call_id": tc_id,
                        "content":      str(result),
                    })

                # loop back — let the model react to tool results
                continue

            # ── done ─────────────────────────────────────────────────────
            break

    def reset(self):
        self.messages = [self.messages[0]]  # keep system prompt
        console.print(f"  [{C_MUTED}]session cleared.[/]")


# ── CLI loop ──────────────────────────────────────────────────────────────────

HELP_TEXT = """
[bold green]pickle[/] — free coding agent · Big Pickle via opencode.ai/zen

[bold]commands:[/]
  [cyan]/reset[/]   clear conversation history
  [cyan]/cwd[/]     show current working directory
  [cyan]/cd[/] [dim]<path>[/]  change working directory
  [cyan]/help[/]    show this message
  [cyan]/exit[/]    quit

[bold]tips:[/]
  • Ask it to read, write, edit, or delete files
  • It will always ask before running shell commands
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
    agent = PickleAgent()

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
        elif user_input.startswith("/cd"):
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
