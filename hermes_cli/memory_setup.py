"""hermes memory setup|status — configure memory provider plugins.

Auto-detects installed memory providers via the plugin system.
Interactive curses-based UI for provider selection, then walks through
the provider's config schema. Writes config to config.yaml + .env.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from hermes_constants import get_hermes_home


# ---------------------------------------------------------------------------
# Curses-based interactive picker (same pattern as hermes tools)
# ---------------------------------------------------------------------------

def _curses_select(title: str, items: list[tuple[str, str]], default: int = 0) -> int:
    """Interactive single-select with arrow keys.

    items: list of (label, description) tuples.
    Returns selected index, or default on escape/quit.
    """
    from hermes_cli.curses_ui import curses_radiolist
    # Format (label, desc) tuples into display strings
    display_items = [
        f"{label}  {desc}" if desc else label
        for label, desc in items
    ]
    return curses_radiolist(title, display_items, selected=default, cancel_returns=default)


def _prompt(label: str, default: str | None = None, secret: bool = False) -> str:
    """Prompt for a value with optional default and secret masking."""
    suffix = f" [{default}]" if default else ""
    if secret:
        sys.stdout.write(f"  {label}{suffix}: ")
        sys.stdout.flush()
        if sys.stdin.isatty():
            val = getpass.getpass(prompt="")
        else:
            val = sys.stdin.readline().strip()
    else:
        sys.stdout.write(f"  {label}{suffix}: ")
        sys.stdout.flush()
        val = sys.stdin.readline().strip()
    return val or (default or "")


# ---------------------------------------------------------------------------
# Provider discovery
# ---------------------------------------------------------------------------

def _install_dependencies(provider_name: str) -> None:
    """Install pip dependencies declared in plugin.yaml."""
    import subprocess
    from plugins.memory import find_provider_dir

    plugin_dir = find_provider_dir(provider_name)
    if not plugin_dir:
        return
    yaml_path = plugin_dir / "plugin.yaml"
    if not yaml_path.exists():
        return

    try:
        import yaml
        with open(yaml_path) as f:
            meta = yaml.safe_load(f) or {}
    except Exception:
        return

    pip_deps = meta.get("pip_dependencies", [])
    if not pip_deps:
        return

    # pip name → import name mapping for packages where they differ
    _IMPORT_NAMES = {
        "honcho-ai": "honcho",
        "mem0ai": "mem0",
        "hindsight-client": "hindsight_client",
        "hindsight-all": "hindsight",
    }

    # Check which packages are missing
    missing = []
    for dep in pip_deps:
        import_name = _IMPORT_NAMES.get(dep, dep.replace("-", "_").split("[")[0])
        try:
            __import__(import_name)
        except ImportError:
            missing.append(dep)

    if not missing:
        return

    print(f"\n  Installing dependencies: {', '.join(missing)}")

    import shutil
    uv_path = shutil.which("uv")
    if not uv_path:
        print(f"  ⚠ uv not found — cannot install dependencies")
        print(f"  Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh")
        print(f"  Then re-run: hermes memory setup")
        return

    try:
        subprocess.run(
            [uv_path, "pip", "install", "--python", sys.executable, "--quiet"] + missing,
            check=True, timeout=120,
            capture_output=True,
        )
        print(f"  ✓ Installed {', '.join(missing)}")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠ Failed to install {', '.join(missing)}")
        stderr = (e.stderr or b"").decode()[:200]
        if stderr:
            print(f"    {stderr}")
        print(f"  Run manually: uv pip install --python {sys.executable} {' '.join(missing)}")
    except Exception as e:
        print(f"  ⚠ Install failed: {e}")
        print(f"  Run manually: uv pip install --python {sys.executable} {' '.join(missing)}")

    # Also show external dependencies (non-pip) if any
    ext_deps = meta.get("external_dependencies", [])
    for dep in ext_deps:
        dep_name = dep.get("name", "")
        check_cmd = dep.get("check", "")
        install_cmd = dep.get("install", "")
        if check_cmd:
            try:
                subprocess.run(
                    check_cmd, shell=True, capture_output=True, timeout=5
                )
            except Exception:
                if install_cmd:
                    print(f"\n  ⚠ '{dep_name}' not found. Install with:")
                    print(f"    {install_cmd}")


def _get_available_providers() -> list:
    """Discover memory providers from plugins/memory/.

    Returns list of (name, description, provider_instance) tuples.
    """
    try:
        from plugins.memory import discover_memory_providers, load_memory_provider
        raw = discover_memory_providers()
    except Exception:
        raw = []

    results = []
    for name, desc, available in raw:
        try:
            provider = load_memory_provider(name)
            if not provider:
                continue
        except Exception:
            continue

        schema = provider.get_config_schema() if hasattr(provider, "get_config_schema") else []
        has_secrets = any(f.get("secret") for f in schema)
        has_non_secrets = any(not f.get("secret") for f in schema)
        if has_secrets and has_non_secrets:
            setup_hint = "API key / local"
        elif has_secrets:
            setup_hint = "requires API key"
        elif not schema:
            setup_hint = "no setup needed"
        else:
            setup_hint = "local"

        results.append((name, setup_hint, provider))
    return results


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def cmd_setup_provider(provider_name: str) -> None:
    """Run memory setup for a specific provider, skipping the picker."""
    from hermes_cli.config import load_config, save_config

    providers = _get_available_providers()
    match = None
    for name, desc, provider in providers:
        if name == provider_name:
            match = (name, desc, provider)
            break

    if not match:
        print(f"\n  Memory provider '{provider_name}' not found.")
        print("  Run 'hermes memory setup' to see available providers.\n")
        return

    name, _, provider = match

    _install_dependencies(name)

    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}

    if hasattr(provider, "post_setup"):
        hermes_home = str(get_hermes_home())
        provider.post_setup(hermes_home, config)
        return

    # Fallback: generic schema-based setup (same as cmd_setup)
    config["memory"]["provider"] = name
    save_config(config)
    print(f"\n  Memory provider: {name}")
    print(f"  Activation saved to config.yaml\n")


def cmd_setup(args) -> None:
    """Interactive memory provider setup wizard."""
    from hermes_cli.config import load_config, save_config

    providers = _get_available_providers()

    if not providers:
        print("\n  No memory provider plugins detected.")
        print("  Install a plugin to ~/.hermes/plugins/ and try again.\n")
        return

    # Build picker items
    items = []
    for name, desc, _ in providers:
        items.append((name, f"— {desc}"))
    items.append(("Built-in only", "— MEMORY.md / USER.md (default)"))

    builtin_idx = len(items) - 1
    selected = _curses_select("Memory provider setup", items, default=builtin_idx)

    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}

    # Built-in only
    if selected >= len(providers) or selected < 0:
        config["memory"]["provider"] = ""
        save_config(config)
        print("\n  ✓ Memory provider: built-in only")
        print("  Saved to config.yaml\n")
        return

    name, _, provider = providers[selected]

    # Install pip dependencies if declared in plugin.yaml
    _install_dependencies(name)

    # If the provider has a post_setup hook, delegate entirely to it.
    # The hook handles its own config, connection test, and activation.
    if hasattr(provider, "post_setup"):
        hermes_home = str(get_hermes_home())
        provider.post_setup(hermes_home, config)
        return

    schema = provider.get_config_schema() if hasattr(provider, "get_config_schema") else []

    provider_config = config["memory"].get(name, {})
    if not isinstance(provider_config, dict):
        provider_config = {}

    env_path = get_hermes_home() / ".env"
    env_writes = {}

    if schema:
        print(f"\n  Configuring {name}:\n")

        for field in schema:
            key = field["key"]
            desc = field.get("description", key)
            default = field.get("default")
            # Dynamic default: look up default from another field's value
            default_from = field.get("default_from")
            if default_from and isinstance(default_from, dict):
                ref_field = default_from.get("field", "")
                ref_map = default_from.get("map", {})
                ref_value = provider_config.get(ref_field, "")
                if ref_value and ref_value in ref_map:
                    default = ref_map[ref_value]
            is_secret = field.get("secret", False)
            choices = field.get("choices")
            env_var = field.get("env_var")
            url = field.get("url")

            # Skip fields whose "when" condition doesn't match
            when = field.get("when")
            if when and isinstance(when, dict):
                if not all(provider_config.get(k) == v for k, v in when.items()):
                    continue

            if choices and not is_secret:
                # Use curses picker for choice fields
                choice_items = [(c, "") for c in choices]
                current = provider_config.get(key, default)
                current_idx = 0
                if current and current in choices:
                    current_idx = choices.index(current)
                sel = _curses_select(f"  {desc}", choice_items, default=current_idx)
                provider_config[key] = choices[sel]
            elif is_secret:
                # Prompt for secret
                existing = os.environ.get(env_var, "") if env_var else ""
                if existing:
                    masked = f"...{existing[-4:]}" if len(existing) > 4 else "set"
                    val = _prompt(f"{desc} (current: {masked}, blank to keep)", secret=True)
                else:
                    hint = f"  Get yours at {url}" if url else ""
                    if hint:
                        print(hint)
                    val = _prompt(desc, secret=True)
                if val and env_var:
                    env_writes[env_var] = val
            else:
                # Regular text prompt
                current = provider_config.get(key)
                effective_default = current or default
                val = _prompt(desc, default=str(effective_default) if effective_default else None)
                if val:
                    provider_config[key] = val
                    # Also write to .env if this field has an env_var
                    if env_var and env_var not in env_writes:
                        env_writes[env_var] = val

    # Write activation key to config.yaml
    config["memory"]["provider"] = name
    save_config(config)

    # Write non-secret config to provider's native location
    hermes_home = str(get_hermes_home())
    if provider_config and hasattr(provider, "save_config"):
        try:
            provider.save_config(provider_config, hermes_home)
        except Exception as e:
            print(f"  Failed to write provider config: {e}")

    # Write secrets to .env
    if env_writes:
        _write_env_vars(env_path, env_writes)

    print(f"\n  Memory provider: {name}")
    print(f"  Activation saved to config.yaml")
    if provider_config:
        print(f"  Provider config saved")
    if env_writes:
        print(f"  API keys saved to .env")
    print(f"\n  Start a new session to activate.\n")


def _write_env_vars(env_path: Path, env_writes: dict) -> None:
    """Append or update env vars in .env file."""
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text().splitlines()

    updated_keys = set()
    new_lines = []
    for line in existing_lines:
        key_match = line.split("=", 1)[0].strip() if "=" in line else ""
        if key_match in env_writes:
            new_lines.append(f"{key_match}={env_writes[key_match]}")
            updated_keys.add(key_match)
        else:
            new_lines.append(line)

    for key, val in env_writes.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def _read_memory_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, IOError):
        return []
    if not raw.strip():
        return []
    return [entry.strip() for entry in raw.split("\n§\n") if entry.strip()]


def _store_diagnostics(path: Path, char_limit: int) -> dict:
    entries = _read_memory_entries(path)
    char_count = len("\n§\n".join(entries)) if entries else 0
    usage_percent = int((char_count / char_limit) * 100) if char_limit else 0
    if not path.exists():
        health = "missing"
    elif usage_percent >= 90:
        health = "near_full"
    else:
        health = "ok"
    return {
        "path": str(path),
        "exists": path.exists(),
        "entry_count": len(entries),
        "char_count": char_count,
        "char_limit": char_limit,
        "usage_percent": usage_percent,
        "health": health,
    }


def _provider_diagnostics(provider_name: str) -> dict:
    if not provider_name:
        return {
            "configured": False,
            "name": "",
            "installed": False,
            "available": False,
            "description": "",
        }

    provider_info = {
        "configured": True,
        "name": provider_name,
        "installed": False,
        "available": False,
        "description": "",
    }
    try:
        for name, desc, provider in _get_available_providers():
            if name != provider_name:
                continue
            provider_info["installed"] = True
            provider_info["description"] = desc
            try:
                provider_info["available"] = bool(provider.is_available())
            except Exception:
                provider_info["available"] = False
            break
    except Exception:
        pass
    return provider_info


def collect_memory_diagnostics(memory_manager=None) -> dict:
    """Collect read-only diagnostics for Hermes memory recall surfaces."""
    from hermes_cli.config import load_config

    config = load_config()
    mem_config = config.get("memory", {}) if isinstance(config, dict) else {}
    if not isinstance(mem_config, dict):
        mem_config = {}

    memory_enabled = bool(mem_config.get("memory_enabled", True))
    user_profile_enabled = bool(mem_config.get("user_profile_enabled", True))
    memory_char_limit = int(mem_config.get("memory_char_limit", 2200) or 0)
    user_char_limit = int(mem_config.get("user_char_limit", 1375) or 0)
    provider_name = str(mem_config.get("provider", "") or "").strip()
    prefetch_bridge_enabled = bool(
        mem_config.get("prefetch_session_search_bridge", False)
    )
    prefetch_bridge_limit = int(
        mem_config.get("prefetch_session_search_limit", 3) or 0
    )

    hermes_home = get_hermes_home()
    mem_dir = hermes_home / "memories"

    try:
        from toolsets import _HERMES_CORE_TOOLS
        session_search_core = "session_search" in _HERMES_CORE_TOOLS
    except Exception:
        session_search_core = False

    runtime_operation_status = {}
    if memory_manager is not None and hasattr(memory_manager, "get_operation_status"):
        try:
            runtime_operation_status = memory_manager.get_operation_status()
        except Exception:
            runtime_operation_status = {}

    stores = {
        "memory": _store_diagnostics(mem_dir / "MEMORY.md", memory_char_limit),
        "user": _store_diagnostics(mem_dir / "USER.md", user_char_limit),
    }
    warnings = []
    for filename, key in (("MEMORY.md", "memory"), ("USER.md", "user")):
        store = stores[key]
        if store["health"] == "near_full":
            warnings.append(
                f"{filename} is {store['usage_percent']}% full; replace or remove stale entries before adding more."
            )

    return {
        "hermes_home": str(hermes_home),
        "built_in": {
            "memory_enabled": memory_enabled,
            "user_profile_enabled": user_profile_enabled,
            "snapshot_refresh": "next_session_or_compression",
            "stores": stores,
        },
        "external_provider": _provider_diagnostics(provider_name),
        "session_search": {
            "core_tool": session_search_core,
            "recall_surface": "explicit_tool",
            "prefetch_bridge_enabled": prefetch_bridge_enabled,
            "prefetch_bridge_limit": prefetch_bridge_limit,
        },
        "runtime_operation_status": runtime_operation_status,
        "warnings": warnings,
    }


def cmd_diagnostics(args) -> None:
    """Show read-only memory diagnostics."""
    diag = collect_memory_diagnostics()
    if getattr(args, "json", False):
        print(json.dumps(diag, indent=2, ensure_ascii=False))
        return

    built_in = diag["built_in"]
    provider = diag["external_provider"]
    session_search = diag["session_search"]
    operation_status = diag.get("runtime_operation_status") or {}

    print("\nMemory diagnostics\n" + "─" * 40)
    print(f"  HERMES_HOME: {diag['hermes_home']}")
    print(
        "  Built-in:   "
        f"memory={'on' if built_in['memory_enabled'] else 'off'}, "
        f"user_profile={'on' if built_in['user_profile_enabled'] else 'off'}"
    )
    print(f"  Snapshot:   refreshes on {built_in['snapshot_refresh']}")
    for label, store in built_in["stores"].items():
        mark = "✓" if store["exists"] else "✗"
        health = f", {store['health']}" if store.get("health") != "ok" else ""
        print(
            f"  {label.upper():<7}   {mark} {store['entry_count']} entries, "
            f"{store['char_count']:,}/{store['char_limit']:,} chars{health}"
        )
    if provider["configured"]:
        installed = "installed" if provider["installed"] else "not installed"
        available = "available" if provider["available"] else "not available"
        print(f"  Provider:   {provider['name']} ({installed}, {available})")
    else:
        print("  Provider:   none (built-in only)")
    print(
        "  Session search: "
        f"{'core tool' if session_search['core_tool'] else 'not in core tools'}; "
        f"{session_search['recall_surface']}"
    )
    print(
        "  Prefetch bridge: "
        f"{'on' if session_search.get('prefetch_bridge_enabled') else 'off'}"
        f" (limit {session_search.get('prefetch_bridge_limit', 3)})"
    )
    if operation_status:
        print("  Runtime operations:")
        for provider_name, operations in operation_status.items():
            for operation, status in operations.items():
                line = f"    {provider_name}.{operation}: {status.get('status', 'unknown')}"
                if status.get("detail"):
                    line += f" ({status['detail']})"
                if status.get("error_type"):
                    line += f" — {status['error_type']}: {status.get('error', '')}"
                print(line)
    warnings = diag.get("warnings") or []
    if warnings:
        print("  Warnings:")
        for warning in warnings:
            print(f"    - {warning}")
    print()


def _entry_preview(content: str, width: int = 96) -> str:
    compact = " ".join(content.split())
    if len(compact) <= width:
        return compact
    return compact[: width - 1].rstrip() + "…"


def _memory_path_for_target(target: str) -> Path:
    mem_dir = get_hermes_home() / "memories"
    if target == "user":
        return mem_dir / "USER.md"
    return mem_dir / "MEMORY.md"


def _write_memory_entries(path: Path, entries: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n§\n".join(entries), encoding="utf-8")


def _backup_memory_file(path: Path) -> str:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{path.name}.bak.{stamp}"
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return str(backup_path)


def prune_memory_entry(
    *,
    target: str,
    entry_index: int,
    dry_run: bool = False,
    yes: bool = False,
) -> dict:
    """Remove one built-in memory entry by 1-based index, unless dry-run."""
    if dry_run and yes:
        return {
            "applied": False,
            "error": "ambiguous_confirmation",
            "message": "Use either --dry-run or --yes, not both.",
        }

    if target not in ("memory", "user"):
        return {"applied": False, "error": "invalid_target", "target": target}

    path = _memory_path_for_target(target)
    entries = _read_memory_entries(path)
    if entry_index < 1 or entry_index > len(entries):
        return {
            "applied": False,
            "error": "invalid_entry_index",
            "target": target,
            "entry_index": entry_index,
            "entry_count": len(entries),
        }

    content = entries[entry_index - 1]
    entry = {
        "target": target,
        "entry_index": entry_index,
        "chars": len(content),
        "preview": _entry_preview(content),
        "content": content,
    }

    if dry_run:
        return {
            "applied": False,
            "dry_run": True,
            "would_remove": entry,
            "chars_saved": len(content),
            "before_entry_count": len(entries),
            "remaining_entry_count": len(entries) - 1,
            "after_entry_count": len(entries) - 1,
        }

    if not yes:
        return {
            "applied": False,
            "error": "confirmation_required",
            "target": target,
            "entry_index": entry_index,
            "message": "Pass --yes to remove this entry, or --dry-run to preview.",
            "would_remove": entry,
        }

    new_entries = entries[: entry_index - 1] + entries[entry_index:]
    backup_path = _backup_memory_file(path)
    _write_memory_entries(path, new_entries)
    return {
        "applied": True,
        "dry_run": False,
        "removed": entry,
        "backup_path": backup_path,
        "chars_saved": len(content),
        "before_entry_count": len(entries),
        "remaining_entry_count": len(new_entries),
        "after_entry_count": len(new_entries),
    }


def restore_memory_backup(
    *,
    target: str,
    backup_path: str,
    dry_run: bool = False,
    yes: bool = False,
) -> dict:
    """Restore a built-in memory file from a backup created by prune."""
    if dry_run and yes:
        return {
            "applied": False,
            "error": "ambiguous_confirmation",
            "message": "Use either --dry-run or --yes, not both.",
        }
    if target not in ("memory", "user"):
        return {"applied": False, "error": "invalid_target", "target": target}

    backup = Path(backup_path).expanduser().resolve()
    backups_dir = (get_hermes_home() / "memories" / "backups").resolve()
    try:
        backup.relative_to(backups_dir)
    except ValueError:
        return {
            "applied": False,
            "error": "backup_outside_memory_backups",
            "backup_path": str(backup),
            "allowed_dir": str(backups_dir),
        }
    if not backup.exists() or not backup.is_file():
        return {
            "applied": False,
            "error": "backup_not_found",
            "backup_path": str(backup),
        }

    content = backup.read_text(encoding="utf-8")
    restored_entries = [entry.strip() for entry in content.split("\n§\n") if entry.strip()]
    target_path = _memory_path_for_target(target)
    current_entries = _read_memory_entries(target_path)

    preview = {
        "target": target,
        "backup_path": str(backup),
        "target_path": str(target_path),
        "entry_count": len(restored_entries),
        "current_entry_count": len(current_entries),
    }
    if dry_run:
        return {
            "applied": False,
            "dry_run": True,
            "would_restore": preview,
        }
    if not yes:
        return {
            "applied": False,
            "error": "confirmation_required",
            "message": "Pass --yes to restore this backup, or --dry-run to preview.",
            "would_restore": preview,
        }

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")
    return {
        "applied": True,
        "dry_run": False,
        "restored": preview,
        "restored_entry_count": len(restored_entries),
    }


def _review_store(path: Path, limit: int) -> dict:
    entries = _read_memory_entries(path)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry] = counts.get(entry, 0) + 1

    details = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        duplicate = counts.get(entry, 0) > 1
        repeated = entry in seen
        seen.add(entry)
        details.append(
            {
                "index": index,
                "chars": len(entry),
                "duplicate": duplicate,
                "repeated_duplicate": repeated,
                "preview": _entry_preview(entry),
                "content": entry,
            }
        )

    details.sort(key=lambda item: (-item["chars"], not item["duplicate"], item["index"]))
    return {
        "path": str(path),
        "exists": path.exists(),
        "entry_count": len(entries),
        "duplicate_count": sum(max(0, count - 1) for count in counts.values()),
        "entries": details[: max(0, limit)],
    }


def collect_memory_review(target: str = "all", limit: int = 10) -> dict:
    """Collect read-only review data for built-in memory stores."""
    hermes_home = get_hermes_home()
    mem_dir = hermes_home / "memories"
    target = target if target in ("all", "memory", "user") else "all"
    limit = max(1, int(limit or 10))

    stores = {}
    if target in ("all", "memory"):
        stores["memory"] = _review_store(mem_dir / "MEMORY.md", limit)
    if target in ("all", "user"):
        stores["user"] = _review_store(mem_dir / "USER.md", limit)

    recommendations = []
    for label, store in stores.items():
        if store["duplicate_count"]:
            recommendations.append(
                f"Review duplicate entries in {label}; {store['duplicate_count']} repeated entr"
                f"{'y' if store['duplicate_count'] == 1 else 'ies'} found."
            )
    if not recommendations:
        recommendations.append("No exact duplicates found; review largest entries first if compaction is needed.")

    return {
        "hermes_home": str(hermes_home),
        "target": target,
        "limit": limit,
        "stores": stores,
        "recommendations": recommendations,
    }


def _compaction_actions_for_store(label: str, store: dict, max_actions: int) -> list[dict]:
    actions: list[dict] = []
    entries = store.get("entries", [])

    for entry in sorted(entries, key=lambda item: item["index"]):
        if not entry.get("repeated_duplicate"):
            continue
        actions.append(
            {
                "action": "remove_duplicate",
                "target": label,
                "entry_index": entry["index"],
                "chars": entry["chars"],
                "preview": entry["preview"],
                "reason": "Exact duplicate repeat; the first matching entry would remain.",
                "dry_run_command": (
                    f"hermes memory prune --target {label} "
                    f"--entry-index {entry['index']} --dry-run"
                ),
                "apply_command": (
                    f"hermes memory prune --target {label} "
                    f"--entry-index {entry['index']} --yes"
                ),
            }
        )
        if len(actions) >= max_actions:
            return actions

    if store.get("duplicate_count", 0) == 0:
        for entry in entries:
            actions.append(
                {
                    "action": "review_large_entry",
                    "target": label,
                    "entry_index": entry["index"],
                    "chars": entry["chars"],
                    "preview": entry["preview"],
                    "reason": "Largest entry; review for stale or compressible content.",
                    "dry_run_command": (
                        f"hermes memory prune --target {label} "
                        f"--entry-index {entry['index']} --dry-run"
                    ),
                    "apply_command": (
                        f"hermes memory prune --target {label} "
                        f"--entry-index {entry['index']} --yes"
                    ),
                }
            )
            if len(actions) >= max_actions:
                break

    return actions


def collect_memory_compaction_plan(
    target: str = "all",
    max_actions: int = 10,
) -> dict:
    """Build a read-only compaction plan for built-in memory stores."""
    target = target if target in ("all", "memory", "user") else "all"
    max_actions = max(1, int(max_actions or 10))
    review = collect_memory_review(target=target, limit=max_actions * 4)

    stores = {}
    for label, store in review["stores"].items():
        actions = _compaction_actions_for_store(label, store, max_actions)
        stores[label] = {
            "path": store["path"],
            "entry_count": store["entry_count"],
            "duplicate_count": store["duplicate_count"],
            "actions": actions,
            "projected_chars_saved": sum(
                action["chars"]
                for action in actions
                if action["action"] == "remove_duplicate"
            ),
        }

    return {
        "hermes_home": review["hermes_home"],
        "target": target,
        "dry_run": True,
        "max_actions": max_actions,
        "stores": stores,
        "notes": [
            "This is a dry-run plan only; no memory files were changed.",
            "Use exact entry indexes/previews to decide whether a future destructive prune is safe.",
        ],
    }


def cmd_compact(args) -> None:
    """Build a read-only memory compaction plan."""
    if not getattr(args, "plan", False):
        print("\n  Nothing changed. Use: hermes memory compact --plan\n")
        return

    plan = collect_memory_compaction_plan(
        target=getattr(args, "target", "all"),
        max_actions=getattr(args, "max_actions", 10),
    )
    if getattr(args, "json", False):
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return

    print("\nMemory compact plan\n" + "─" * 40)
    print(f"  HERMES_HOME: {plan['hermes_home']}")
    print(f"  Target:      {plan['target']}")
    print("  Mode:        dry-run")
    for label, store in plan["stores"].items():
        print(
            f"\n  {label.upper()} — {store['entry_count']} entries, "
            f"{store['duplicate_count']} duplicate repeats"
        )
        if not store["actions"]:
            print("    No obvious exact-duplicate or large-entry actions.")
            continue
        for action in store["actions"]:
            print(
                f"    {action['action']} #{action['entry_index']} "
                f"({action['chars']} chars): {action['preview']}"
            )
            print(f"      reason: {action['reason']}")
            print(f"      preview: {action['dry_run_command']}")
            print(f"      apply:   {action['apply_command']}")
        if store["projected_chars_saved"]:
            print(f"    projected exact-duplicate savings: {store['projected_chars_saved']} chars")
    print("\n  Notes:")
    for note in plan["notes"]:
        print(f"    - {note}")
    print()


def cmd_prune(args) -> None:
    """Remove one built-in memory entry by index."""
    result = prune_memory_entry(
        target=getattr(args, "target", "memory"),
        entry_index=getattr(args, "entry_index", 0),
        dry_run=getattr(args, "dry_run", False),
        yes=getattr(args, "yes", False),
    )
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    print("\nMemory prune\n" + "─" * 40)
    if result.get("applied"):
        removed = result["removed"]
        print(
            f"  Removed {removed['target']} entry #{removed['entry_index']} "
            f"({removed['chars']} chars): {removed['preview']}"
        )
        print(f"  Saved chars: {result.get('chars_saved', removed['chars'])}")
        if result.get("backup_path"):
            print(f"  Backup: {result['backup_path']}")
        print(f"  Remaining entries: {result['remaining_entry_count']}\n")
        return

    if result.get("dry_run"):
        entry = result["would_remove"]
        print(
            f"  Dry run: would remove {entry['target']} entry #{entry['entry_index']} "
            f"({entry['chars']} chars): {entry['preview']}"
        )
        print(f"  Would save chars: {result.get('chars_saved', entry['chars'])}")
        print(f"  Remaining entries after apply: {result['remaining_entry_count']}")
        print("  No files changed. Re-run with --yes to apply.\n")
        return

    error = result.get("error", "unknown_error")
    print(f"  Not applied: {error}")
    if result.get("message"):
        print(f"  {result['message']}")
    if result.get("would_remove"):
        entry = result["would_remove"]
        print(
            f"  Candidate: {entry['target']} entry #{entry['entry_index']} "
            f"({entry['chars']} chars): {entry['preview']}"
        )
    print()


def cmd_restore(args) -> None:
    """Restore one built-in memory store from a prune backup."""
    result = restore_memory_backup(
        target=getattr(args, "target", "memory"),
        backup_path=getattr(args, "backup_path", ""),
        dry_run=getattr(args, "dry_run", False),
        yes=getattr(args, "yes", False),
    )
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    print("\nMemory restore\n" + "─" * 40)
    if result.get("applied"):
        restored = result["restored"]
        print(f"  Restored {restored['target']} from: {restored['backup_path']}")
        print(f"  Target: {restored['target_path']}")
        print(f"  Entries: {result['restored_entry_count']}\n")
        return
    if result.get("dry_run"):
        restore = result["would_restore"]
        print(f"  Dry run: would restore {restore['target']} from: {restore['backup_path']}")
        print(f"  Target: {restore['target_path']}")
        print(
            f"  Entries: {restore['current_entry_count']} current -> "
            f"{restore['entry_count']} restored"
        )
        print("  No files changed. Re-run with --yes to apply.\n")
        return
    print(f"  Not applied: {result.get('error', 'unknown_error')}")
    if result.get("message"):
        print(f"  {result['message']}")
    print()


def cmd_review(args) -> None:
    """Show a read-only review of built-in memory entries."""
    review = collect_memory_review(
        target=getattr(args, "target", "all"),
        limit=getattr(args, "limit", 10),
    )
    if getattr(args, "json", False):
        print(json.dumps(review, indent=2, ensure_ascii=False))
        return

    print("\nMemory review\n" + "─" * 40)
    print(f"  HERMES_HOME: {review['hermes_home']}")
    print(f"  Target:      {review['target']} (top {review['limit']})")
    for label, store in review["stores"].items():
        print(
            f"\n  {label.upper()} — {store['entry_count']} entries, "
            f"{store['duplicate_count']} duplicate repeats"
        )
        if not store["entries"]:
            print("    (no entries)")
            continue
        for entry in store["entries"]:
            dup = " duplicate" if entry["duplicate"] else ""
            print(
                f"    #{entry['index']} {entry['chars']} chars{dup}: "
                f"{entry['preview']}"
            )
    print("\n  Recommendations:")
    for recommendation in review["recommendations"]:
        print(f"    - {recommendation}")
    print()

def cmd_status(args) -> None:
    """Show current memory provider config."""
    from hermes_cli.config import load_config

    config = load_config()
    mem_config = config.get("memory", {})
    provider_name = mem_config.get("provider", "")

    print(f"\nMemory status\n" + "─" * 40)
    print(f"  Built-in:  always active")
    print(f"  Provider:  {provider_name or '(none — built-in only)'}")

    if provider_name:
        provider_config = mem_config.get(provider_name, {})
        if provider_config:
            print(f"\n  {provider_name} config:")
            for key, val in provider_config.items():
                print(f"    {key}: {val}")

        providers = _get_available_providers()
        found = any(name == provider_name for name, _, _ in providers)
        if found:
            print(f"\n  Plugin:    installed ✓")
            for pname, _, p in providers:
                if pname == provider_name:
                    if p.is_available():
                        print(f"  Status:    available ✓")
                    else:
                        print(f"  Status:    not available ✗")
                        schema = p.get_config_schema() if hasattr(p, "get_config_schema") else []
                        # Check all fields that have env_var (both secret and non-secret)
                        required_fields = [f for f in schema if f.get("env_var")]
                        if required_fields:
                            print(f"  Missing:")
                            for f in required_fields:
                                env_var = f.get("env_var", "")
                                url = f.get("url", "")
                                is_set = bool(os.environ.get(env_var))
                                mark = "✓" if is_set else "✗"
                                line = f"    {mark} {env_var}"
                                if url and not is_set:
                                    line += f"  → {url}"
                                print(line)
                    break
        else:
            print(f"\n  Plugin:    NOT installed ✗")
            print(f"  Install the '{provider_name}' memory plugin to ~/.hermes/plugins/")

    providers = _get_available_providers()
    if providers:
        print(f"\n  Installed plugins:")
        for pname, desc, _ in providers:
            active = " ← active" if pname == provider_name else ""
            print(f"    • {pname}  ({desc}){active}")

    print()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def memory_command(args) -> None:
    """Route memory subcommands."""
    sub = getattr(args, "memory_command", None)
    if sub == "setup":
        cmd_setup(args)
    elif sub == "diagnostics":
        cmd_diagnostics(args)
    elif sub == "review":
        cmd_review(args)
    elif sub == "compact":
        cmd_compact(args)
    elif sub == "prune":
        cmd_prune(args)
    elif sub == "restore":
        cmd_restore(args)
    elif sub == "status":
        cmd_status(args)
    else:
        cmd_status(args)
