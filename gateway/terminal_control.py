"""Safe terminal-control helpers for gateway slash commands.

These helpers intentionally expose a narrow, argument-vector based surface for
operator tasks from messaging gateways.  They avoid shell interpolation and keep
mutating actions limited to tmux input / Ctrl-C.
"""

from __future__ import annotations

import subprocess

_MAX_OUTPUT_CHARS = 3900


def _run(argv: list[str], *, timeout: float = 15.0) -> str:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return f"Command not found: {argv[0]}"
    except subprocess.TimeoutExpired:
        return f"Command timed out: {' '.join(argv)}"

    output = (proc.stdout or proc.stderr or "").strip()
    if not output:
        output = "Command returned no output."
    if proc.returncode != 0:
        output = f"Exit {proc.returncode}: {output}"
    return _truncate(output)


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 80].rstrip() + "\n… output truncated. Narrow the command or request fewer lines."


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def list_hermes_sessions(limit: int = 20) -> str:
    limit = _bounded_int(limit, default=20, minimum=1, maximum=80)
    output = _run(["hermes", "sessions", "list"], timeout=20.0)
    lines = output.splitlines()
    if len(lines) > limit:
        output = "\n".join(lines[:limit])
    return _truncate(output)


def list_tmux_panes() -> str:
    return _run(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{session_name}:#{window_index}.#{pane_index} active=#{pane_active} tty=#{pane_tty} cmd=#{pane_current_command} path=#{pane_current_path}",
        ]
    )


def list_tmux_sessions() -> str:
    return _run(["tmux", "list-sessions", "-F", "#{session_name}"])


def _safe_prefix(prefix: str) -> str:
    prefix = str(prefix or "hermes").strip().lower()
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
    cleaned = "".join(ch for ch in prefix if ch in allowed).strip("-_")
    return cleaned or "hermes"


def normalize_tmux_session_names(prefix: str = "hermes") -> str:
    """Rename tmux sessions to a stable operator-facing convention.

    Numeric sessions become `<prefix>-<number>`. Existing sessions that already
    start with `<prefix>-` are left alone. Non-numeric sessions are left alone to
    avoid destroying explicit human names.
    """
    prefix = _safe_prefix(prefix)
    output = list_tmux_sessions()
    if output.startswith("Exit ") or output.startswith("Command not found:") or output.startswith("Command timed out:"):
        return output
    names = [line.strip() for line in output.splitlines() if line.strip()]
    existing = set(names)
    changes: list[str] = []
    for name in names:
        if name.startswith(f"{prefix}-"):
            continue
        if not name.isdigit():
            continue
        new_name = f"{prefix}-{name}"
        if new_name in existing:
            suffix = 1
            candidate = f"{new_name}-{suffix}"
            while candidate in existing:
                suffix += 1
                candidate = f"{new_name}-{suffix}"
            new_name = candidate
        result = _run(["tmux", "rename-session", "-t", name, new_name])
        if result == "Command returned no output.":
            changes.append(f"{name} -> {new_name}")
            existing.discard(name)
            existing.add(new_name)
        else:
            changes.append(f"{name} -> {new_name}: {result}")
    if not changes:
        return f"tmux sessions already normalized with prefix '{prefix}'"
    return _truncate("renamed tmux sessions:\n" + "\n".join(changes))


def build_operator_dashboard() -> str:
    sections = [
        "## Hermes Operator Dashboard",
        "### Gateway",
        _run(["hermes", "gateway", "status"], timeout=30.0),
        "### Cron",
        _run(["hermes", "cron", "status"], timeout=30.0),
        "### Recent Hermes sessions",
        list_hermes_sessions(8),
        "### tmux panes",
        list_tmux_panes(),
        "### Commands",
        "/sessions 10\n/tmux\n/tmux capture <target> 80\n/send <target> <message>\n/tmux stop <target>\n/tmux normalize hermes\n/resume <session name>",
    ]
    return _truncate("\n\n".join(sections))


def capture_tmux_pane(target: str, lines: int = 80) -> str:
    target = str(target).strip()
    if not target:
        return "Usage: /tmux capture <session[:window.pane]> [lines]"
    lines = _bounded_int(lines, default=80, minimum=1, maximum=300)
    return _run(["tmux", "capture-pane", "-t", target, "-p", "-S", f"-{lines}"])


def send_tmux_keys(target: str, message: str) -> str:
    target = str(target).strip()
    message = str(message)
    if not target or not message.strip():
        return "Usage: /send <session[:window.pane]> <message>"
    output = _run(["tmux", "send-keys", "-t", target, message, "Enter"])
    if output == "Command returned no output.":
        return f"sent to {target}"
    return output


def stop_tmux_pane(target: str) -> str:
    target = str(target).strip()
    if not target:
        return "Usage: /tmux stop <session[:window.pane]>"
    output = _run(["tmux", "send-keys", "-t", target, "C-c"])
    if output == "Command returned no output.":
        return f"sent Ctrl-C to {target}"
    return output
