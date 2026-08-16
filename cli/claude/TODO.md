# TODO — pickle coding agent (`pickel.py`)

> Status legend: `[ ]` todo · `[~]` working on · `[x]` done
>
> Analysis date: this file was generated from a full review of `pickel.py` (1224 lines).

## 1. Naming & docs
- [ ] Rename `pickel.py` → `pickle.py` (or update all docs to match the actual filename — docstring, `HELP_TEXT`, comments all say `python pickle.py`, which doesn't exist) — *needs user decision*
- [ ] Update `update.sh` to reference the new filename
- [x] Fix misleading `HELP_TEXT`: *"It will always ask before running shell commands"* — but `ASK_BEFORE_COMMAND = False` (only `rm`/`rmdir` prompt). Help text should describe the real behaviour.

## 2. Dependencies & setup
- [x] Add `requirements.txt` (`httpx`, `rich`) — nothing declares the deps today
- [x] `update.sh`: print the dependency install command (and preserve the `SESSION_DIR` customization that a plain `cp` used to clobber)

## 3. Crash safety (agent loop, `run_turn`)
- [x] Guard `json.loads(tc["function"]["arguments"])` — malformed JSON currently crashes the whole CLI
- [x] Fallback when `tc["id"]` is missing from a tool call
- [x] Validate API response shape (`choices[0].message`) — KeyError/IndexError crash today on unexpected responses
- [x] Cap the tool-call loop (`MAX_TOOL_TURNS`) — `while True` + `continue` can loop forever
- [x] Handle `finish_reason="tool_calls"` with an empty `tool_calls` list (currently → infinite loop)
- [x] Malformed tool calls should produce a tool-result error message back to the model, never a crash

## 4. Correctness
- [x] Refresh the system message on session resume (saved cwd goes stale after `/cd`)
- [x] `read_file`: skip binary files like `read_range` does
- [x] `/cd`: match exactly (`/cd` or `/cd <path>`) so typos like `/cda` aren't swallowed

## 5. Resource limits
- [x] Cap `run_command` output fed back to the model and persisted to session JSON (new `MAX_COMMAND_CHARS`)

## 6. Reliability
- [x] Retry with backoff on transient API errors (429 / 5xx / network timeouts)

## 7. Session management
- [ ] Add a way to delete / prune old session files (they accumulate in `session/` forever)

## 8. Polish
- [x] Per-request `x-opencode-request` header (currently one id reused for every call in a session)
- [ ] `README.md` with install + usage
- [ ] Friendly error if `rich` / `httpx` are missing (instead of a raw traceback)
