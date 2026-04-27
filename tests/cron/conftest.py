from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_cron_tick_lock(tmp_path, monkeypatch):
    """Keep cron tick tests away from the user's real Hermes scheduler lock."""
    import cron.scheduler as scheduler

    lock_dir = tmp_path / "cron-lock"
    lock_dir.mkdir()
    monkeypatch.setattr(scheduler, "_LOCK_DIR", lock_dir)
    monkeypatch.setattr(scheduler, "_LOCK_FILE", lock_dir / ".tick.lock")
