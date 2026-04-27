"""Tests for gateway terminal-control slash commands."""

from unittest.mock import MagicMock

import pytest

from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource
from gateway.config import Platform
from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS, resolve_command


def _event(text: str) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            user_id="u1",
            chat_id="c1",
            user_name="tester",
            chat_type="dm",
        ),
        message_id="m1",
    )


def _runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner.config = MagicMock()
    return runner


def test_terminal_control_commands_are_gateway_known():
    assert resolve_command("sessions").gateway_only is True
    assert resolve_command("tmux").gateway_only is True
    assert resolve_command("send").gateway_only is True
    assert resolve_command("dashboard").gateway_only is True
    assert resolve_command("resume") is not None
    assert "sessions" in GATEWAY_KNOWN_COMMANDS
    assert "tmux" in GATEWAY_KNOWN_COMMANDS
    assert "send" in GATEWAY_KNOWN_COMMANDS
    assert "dashboard" in GATEWAY_KNOWN_COMMANDS


@pytest.mark.asyncio
async def test_sessions_command_uses_safe_helper(monkeypatch):
    runner = _runner()

    def fake_sessions(limit=20):
        assert limit == 7
        return "session rows"

    monkeypatch.setattr("gateway.terminal_control.list_hermes_sessions", fake_sessions)

    result = await runner._handle_sessions_command(_event("/sessions 7"))

    assert result == "session rows"


@pytest.mark.asyncio
async def test_tmux_command_lists_by_default(monkeypatch):
    runner = _runner()
    monkeypatch.setattr("gateway.terminal_control.list_tmux_panes", lambda: "pane rows")

    result = await runner._handle_tmux_command(_event("/tmux"))

    assert result == "pane rows"


@pytest.mark.asyncio
async def test_tmux_capture_requires_target_and_limits_lines(monkeypatch):
    runner = _runner()

    def fake_capture(target, lines=80):
        assert target == "5:0.0"
        assert lines == 25
        return "captured"

    monkeypatch.setattr("gateway.terminal_control.capture_tmux_pane", fake_capture)

    result = await runner._handle_tmux_command(_event("/tmux capture 5:0.0 25"))

    assert result == "captured"


@pytest.mark.asyncio
async def test_tmux_normalize_renames_sessions_through_helper(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(
        "gateway.terminal_control.normalize_tmux_session_names",
        lambda prefix="hermes": f"normalized with {prefix}",
    )

    result = await runner._handle_tmux_command(_event("/tmux normalize luna"))

    assert result == "normalized with luna"


@pytest.mark.asyncio
async def test_send_command_sends_message_to_tmux(monkeypatch):
    runner = _runner()

    def fake_send(target, message):
        assert target == "5:0.0"
        assert message == "현재 작업 요약해줘"
        return "sent"

    monkeypatch.setattr("gateway.terminal_control.send_tmux_keys", fake_send)

    result = await runner._handle_send_command(_event("/send 5:0.0 현재 작업 요약해줘"))

    assert result == "sent"


@pytest.mark.asyncio
async def test_dashboard_command_uses_safe_helper(monkeypatch):
    runner = _runner()
    monkeypatch.setattr("gateway.terminal_control.build_operator_dashboard", lambda: "dashboard rows")

    result = await runner._handle_dashboard_command(_event("/dashboard"))

    assert result == "dashboard rows"


def test_build_operator_dashboard_combines_sessions_tmux_and_gateway(monkeypatch):
    from gateway import terminal_control

    monkeypatch.setattr(terminal_control, "list_hermes_sessions", lambda limit=8: "session rows")
    monkeypatch.setattr(terminal_control, "list_tmux_panes", lambda: "pane rows")
    monkeypatch.setattr(terminal_control, "_run", lambda argv, timeout=15.0: "gateway running")

    dashboard = terminal_control.build_operator_dashboard()

    assert "Hermes Operator Dashboard" in dashboard
    assert "gateway running" in dashboard
    assert "session rows" in dashboard
    assert "pane rows" in dashboard


def test_normalize_tmux_session_names_renames_numeric_sessions(monkeypatch):
    from gateway import terminal_control

    calls = []

    def fake_run(argv, timeout=15.0):
        calls.append(argv)
        if argv[:2] == ["tmux", "list-sessions"]:
            return "0\nhermes-main\n5"
        if argv[:2] == ["tmux", "rename-session"]:
            return "Command returned no output."
        raise AssertionError(argv)

    monkeypatch.setattr(terminal_control, "_run", fake_run)

    result = terminal_control.normalize_tmux_session_names("hermes")

    assert "0 -> hermes-0" in result
    assert "5 -> hermes-5" in result
    assert ["tmux", "rename-session", "-t", "0", "hermes-0"] in calls
    assert ["tmux", "rename-session", "-t", "5", "hermes-5"] in calls
