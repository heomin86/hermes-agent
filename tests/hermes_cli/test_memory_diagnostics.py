from types import SimpleNamespace

import yaml


def _write_config(home, memory_config):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"memory": memory_config}),
        encoding="utf-8",
    )


def test_collect_memory_diagnostics_reports_builtin_files(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(
        hermes_home,
        {
            "memory_enabled": True,
            "user_profile_enabled": False,
            "memory_char_limit": 100,
            "user_char_limit": 80,
            "provider": "",
        },
    )
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text("first\n§\nsecond", encoding="utf-8")

    from hermes_cli.memory_setup import collect_memory_diagnostics

    diag = collect_memory_diagnostics()

    assert diag["built_in"]["memory_enabled"] is True
    assert diag["built_in"]["user_profile_enabled"] is False
    assert diag["built_in"]["stores"]["memory"]["exists"] is True
    assert diag["built_in"]["stores"]["memory"]["entry_count"] == 2
    assert diag["built_in"]["stores"]["user"]["exists"] is False
    assert diag["external_provider"]["configured"] is False
    assert diag["session_search"]["core_tool"] is True
    assert diag["session_search"]["prefetch_bridge_enabled"] is False
    assert diag["session_search"]["prefetch_bridge_limit"] == 3


def test_collect_memory_diagnostics_reports_provider_availability(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": "demo"})

    fake_provider = SimpleNamespace(is_available=lambda: False)

    import hermes_cli.memory_setup as memory_setup

    monkeypatch.setattr(
        memory_setup,
        "_get_available_providers",
        lambda: [("demo", "Demo provider", fake_provider)],
    )

    diag = memory_setup.collect_memory_diagnostics()

    assert diag["external_provider"] == {
        "configured": True,
        "name": "demo",
        "installed": True,
        "available": False,
        "description": "Demo provider",
    }


def test_collect_memory_diagnostics_includes_runtime_operation_status(
    monkeypatch, tmp_path
):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": ""})

    class FakeManager:
        def get_operation_status(self):
            return {
                "external": {
                    "prefetch": {
                        "status": "failed",
                        "error_type": "RuntimeError",
                        "error": "network error",
                    }
                }
            }

    from hermes_cli.memory_setup import collect_memory_diagnostics

    diag = collect_memory_diagnostics(memory_manager=FakeManager())

    assert diag["runtime_operation_status"]["external"]["prefetch"]["status"] == "failed"


def test_collect_memory_diagnostics_reports_prefetch_bridge_config(
    monkeypatch, tmp_path
):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(
        hermes_home,
        {
            "provider": "",
            "prefetch_session_search_bridge": True,
            "prefetch_session_search_limit": 5,
        },
    )

    from hermes_cli.memory_setup import collect_memory_diagnostics

    diag = collect_memory_diagnostics()

    assert diag["session_search"]["prefetch_bridge_enabled"] is True
    assert diag["session_search"]["prefetch_bridge_limit"] == 5


def test_collect_memory_diagnostics_flags_near_full_stores(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(
        hermes_home,
        {
            "memory_enabled": True,
            "user_profile_enabled": True,
            "memory_char_limit": 10,
            "user_char_limit": 100,
            "provider": "",
        },
    )
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text("123456789", encoding="utf-8")

    from hermes_cli.memory_setup import collect_memory_diagnostics

    diag = collect_memory_diagnostics()

    memory_store = diag["built_in"]["stores"]["memory"]
    user_store = diag["built_in"]["stores"]["user"]
    assert memory_store["health"] == "near_full"
    assert user_store["health"] == "missing"
    assert any("MEMORY.md is 90% full" in warning for warning in diag["warnings"])


def test_collect_memory_review_lists_largest_and_duplicates(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": ""})
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text(
        "short\n§\nthis is the longest memory entry\n§\nshort",
        encoding="utf-8",
    )

    from hermes_cli.memory_setup import collect_memory_review

    review = collect_memory_review(target="memory", limit=2)

    memory = review["stores"]["memory"]
    assert set(review["stores"]) == {"memory"}
    assert memory["entry_count"] == 3
    assert memory["duplicate_count"] == 1
    assert memory["entries"][0]["content"] == "this is the longest memory entry"
    assert memory["entries"][0]["chars"] == len("this is the longest memory entry")
    assert memory["entries"][1]["duplicate"] is True
    assert "Review duplicate entries" in review["recommendations"][0]


def test_collect_memory_review_target_all_includes_user(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": ""})
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    (mem_dir / "USER.md").write_text("prefers concise answers", encoding="utf-8")

    from hermes_cli.memory_setup import collect_memory_review

    review = collect_memory_review(target="all", limit=10)

    assert review["stores"]["memory"]["entry_count"] == 0
    assert review["stores"]["user"]["entry_count"] == 1
    assert review["stores"]["user"]["entries"][0]["content"] == "prefers concise answers"


def test_collect_memory_compaction_plan_is_read_only(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(
        hermes_home,
        {
            "memory_char_limit": 20,
            "user_char_limit": 100,
            "provider": "",
        },
    )
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    memory_path = mem_dir / "MEMORY.md"
    original = "duplicate\n§\nlarge stale entry\n§\nduplicate"
    memory_path.write_text(original, encoding="utf-8")

    from hermes_cli.memory_setup import collect_memory_compaction_plan

    plan = collect_memory_compaction_plan(target="memory", max_actions=3)

    assert memory_path.read_text(encoding="utf-8") == original
    assert plan["dry_run"] is True
    assert set(plan["stores"]) == {"memory"}
    assert plan["stores"]["memory"]["projected_chars_saved"] > 0
    assert plan["stores"]["memory"]["actions"][0]["action"] == "remove_duplicate"
    assert plan["stores"]["memory"]["actions"][0]["entry_index"] == 3
    assert (
        plan["stores"]["memory"]["actions"][0]["dry_run_command"]
        == "hermes memory prune --target memory --entry-index 3 --dry-run"
    )
    assert (
        plan["stores"]["memory"]["actions"][0]["apply_command"]
        == "hermes memory prune --target memory --entry-index 3 --yes"
    )
    assert any("dry-run" in note for note in plan["notes"])


def test_collect_memory_compaction_plan_targets_near_full_largest_entries(
    monkeypatch, tmp_path
):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(
        hermes_home,
        {
            "memory_char_limit": 10,
            "user_char_limit": 100,
            "provider": "",
        },
    )
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text("123456789", encoding="utf-8")

    from hermes_cli.memory_setup import collect_memory_compaction_plan

    plan = collect_memory_compaction_plan(target="memory", max_actions=2)

    actions = plan["stores"]["memory"]["actions"]
    assert actions[0]["action"] == "review_large_entry"
    assert actions[0]["entry_index"] == 1
    assert actions[0]["chars"] == 9


def test_prune_memory_entry_dry_run_does_not_modify_file(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": ""})
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    memory_path = mem_dir / "MEMORY.md"
    original = "keep\n§\nremove me"
    memory_path.write_text(original, encoding="utf-8")

    from hermes_cli.memory_setup import prune_memory_entry

    result = prune_memory_entry(target="memory", entry_index=2, dry_run=True)

    assert result["applied"] is False
    assert result["would_remove"]["content"] == "remove me"
    assert result["chars_saved"] == len("remove me")
    assert result["before_entry_count"] == 2
    assert memory_path.read_text(encoding="utf-8") == original


def test_prune_memory_entry_requires_yes_for_apply(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": ""})
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    memory_path = mem_dir / "MEMORY.md"
    original = "keep\n§\nremove me"
    memory_path.write_text(original, encoding="utf-8")

    from hermes_cli.memory_setup import prune_memory_entry

    result = prune_memory_entry(target="memory", entry_index=2, yes=False)

    assert result["applied"] is False
    assert result["error"] == "confirmation_required"
    assert memory_path.read_text(encoding="utf-8") == original


def test_prune_memory_entry_with_yes_removes_entry(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": ""})
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    memory_path = mem_dir / "MEMORY.md"
    memory_path.write_text("keep\n§\nremove me\n§\nkeep too", encoding="utf-8")

    from hermes_cli.memory_setup import prune_memory_entry

    result = prune_memory_entry(target="memory", entry_index=2, yes=True)

    assert result["applied"] is True
    assert result["removed"]["content"] == "remove me"
    assert result["chars_saved"] == len("remove me")
    assert result["before_entry_count"] == 3
    assert result["after_entry_count"] == 2
    assert result["backup_path"]
    backup_path = mem_dir / "backups" / "MEMORY.md.bak"
    assert result["backup_path"].startswith(str(backup_path))
    assert "remove me" in backup_path.parent.joinpath(
        result["backup_path"].split("/")[-1]
    ).read_text(encoding="utf-8")
    assert memory_path.read_text(encoding="utf-8") == "keep\n§\nkeep too"


def test_prune_memory_entry_rejects_invalid_index(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": ""})
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    (mem_dir / "USER.md").write_text("one entry", encoding="utf-8")

    from hermes_cli.memory_setup import prune_memory_entry

    result = prune_memory_entry(target="user", entry_index=2, dry_run=True)

    assert result["applied"] is False
    assert result["error"] == "invalid_entry_index"


def test_prune_memory_entry_rejects_dry_run_with_yes(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": ""})
    mem_dir = hermes_home / "memories"
    mem_dir.mkdir()
    memory_path = mem_dir / "MEMORY.md"
    original = "keep\n§\nremove me"
    memory_path.write_text(original, encoding="utf-8")

    from hermes_cli.memory_setup import prune_memory_entry

    result = prune_memory_entry(
        target="memory",
        entry_index=2,
        dry_run=True,
        yes=True,
    )

    assert result["applied"] is False
    assert result["error"] == "ambiguous_confirmation"
    assert memory_path.read_text(encoding="utf-8") == original


def test_restore_memory_backup_dry_run_does_not_modify_file(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": ""})
    mem_dir = hermes_home / "memories"
    backup_dir = mem_dir / "backups"
    backup_dir.mkdir(parents=True)
    memory_path = mem_dir / "MEMORY.md"
    backup_path = backup_dir / "MEMORY.md.bak.test"
    memory_path.write_text("current", encoding="utf-8")
    backup_path.write_text("restored", encoding="utf-8")

    from hermes_cli.memory_setup import restore_memory_backup

    result = restore_memory_backup(
        target="memory",
        backup_path=str(backup_path),
        dry_run=True,
    )

    assert result["applied"] is False
    assert result["would_restore"]["entry_count"] == 1
    assert memory_path.read_text(encoding="utf-8") == "current"


def test_restore_memory_backup_with_yes_restores_file(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": ""})
    mem_dir = hermes_home / "memories"
    backup_dir = mem_dir / "backups"
    backup_dir.mkdir(parents=True)
    user_path = mem_dir / "USER.md"
    backup_path = backup_dir / "USER.md.bak.test"
    user_path.write_text("current", encoding="utf-8")
    backup_path.write_text("restored\n§\nsecond", encoding="utf-8")

    from hermes_cli.memory_setup import restore_memory_backup

    result = restore_memory_backup(
        target="user",
        backup_path=str(backup_path),
        yes=True,
    )

    assert result["applied"] is True
    assert result["restored_entry_count"] == 2
    assert user_path.read_text(encoding="utf-8") == "restored\n§\nsecond"


def test_restore_memory_backup_rejects_path_outside_backups(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    _write_config(hermes_home, {"provider": ""})
    outside = tmp_path / "outside.bak"
    outside.write_text("bad", encoding="utf-8")

    from hermes_cli.memory_setup import restore_memory_backup

    result = restore_memory_backup(
        target="memory",
        backup_path=str(outside),
        dry_run=True,
    )

    assert result["applied"] is False
    assert result["error"] == "backup_outside_memory_backups"
