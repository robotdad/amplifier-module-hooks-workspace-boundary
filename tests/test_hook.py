"""Integration tests for the hooks-workspace-boundary mount() entry point.

Tests the full handler pipeline via a MockCoordinator, verifying that mount()
registers handlers correctly and that the handlers enforce boundary policy.

amplifier_core is mocked in conftest.py — no real Amplifier installation required.
"""

from __future__ import annotations

import os

import pytest

# conftest.py sets up the amplifier_core mock before any of these imports.
from amplifier_module_hooks_workspace_boundary import mount

# Constants mirroring the mock values set in conftest.py.
TOOL_PRE = "tool:pre"
TOOL_POST = "tool:post"


def _pre(tool_name: str, **tool_input) -> dict:
    """Build a minimal tool:pre event data dict."""
    return {"tool_name": tool_name, "tool_input": tool_input}


# ---------------------------------------------------------------------------
# mount() registration
# ---------------------------------------------------------------------------


class TestMountRegistration:
    async def test_registers_two_handlers(self, coordinator, tmp_path) -> None:
        await mount(coordinator, {"workspace_root": str(tmp_path)})
        assert len(coordinator.hooks.registrations) == 2

    async def test_registers_pre_handler_at_priority_5(self, coordinator, tmp_path) -> None:
        await mount(coordinator, {"workspace_root": str(tmp_path)})
        pre_regs = [r for r in coordinator.hooks.registrations if r[0] == TOOL_PRE]
        assert len(pre_regs) == 1
        assert pre_regs[0][2] == 5  # priority

    async def test_registers_post_handler_at_priority_50(self, coordinator, tmp_path) -> None:
        await mount(coordinator, {"workspace_root": str(tmp_path)})
        post_regs = [r for r in coordinator.hooks.registrations if r[0] == TOOL_POST]
        assert len(post_regs) == 1
        assert post_regs[0][2] == 50  # priority

    async def test_pre_handler_named_workspace_boundary(self, coordinator, tmp_path) -> None:
        await mount(coordinator, {"workspace_root": str(tmp_path)})
        pre_regs = [r for r in coordinator.hooks.registrations if r[0] == TOOL_PRE]
        assert pre_regs[0][3] == "workspace-boundary"

    async def test_returns_cleanup_callable(self, coordinator, tmp_path) -> None:
        cleanup = await mount(coordinator, {"workspace_root": str(tmp_path)})
        assert callable(cleanup)

    async def test_cleanup_unregisters_both_handlers(self, coordinator, tmp_path) -> None:
        cleanup = await mount(coordinator, {"workspace_root": str(tmp_path)})
        assert len(coordinator.hooks.registrations) == 2
        cleanup()
        assert len(coordinator.hooks.registrations) == 0

    async def test_registers_contributor(self, coordinator, tmp_path) -> None:
        await mount(coordinator, {"workspace_root": str(tmp_path)})
        assert len(coordinator.contributors) == 1
        ns, name, fn = coordinator.contributors[0]
        assert ns == "observability.events"
        assert name == "hooks-workspace-boundary"

    async def test_contributor_returns_all_event_names(self, coordinator, tmp_path) -> None:
        await mount(coordinator, {"workspace_root": str(tmp_path)})
        _, _, fn = coordinator.contributors[0]
        events = fn()
        expected = {
            "workspace_boundary:path_denied",
            "workspace_boundary:path_allowed",
            "workspace_boundary:allowlisted",
            "workspace_boundary:bash_warning",
            "workspace_boundary:unknown_tool",
        }
        assert expected.issubset(set(events))

    async def test_mount_no_config_uses_cwd(
        self, coordinator, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.chdir(str(tmp_path))
        cleanup = await mount(coordinator, None)
        assert cleanup is not None
        assert len(coordinator.hooks.registrations) == 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _mount_and_get_pre(coordinator, workspace, **extra_cfg):
    """Mount with given config and return the tool:pre handler."""
    await mount(coordinator, {"workspace_root": str(workspace), **extra_cfg})
    return coordinator.get_handler(event=TOOL_PRE, priority=5)


async def _mount_and_get_post(coordinator, workspace, **extra_cfg):
    """Mount with given config and return the tool:post handler."""
    await mount(coordinator, {"workspace_root": str(workspace), **extra_cfg})
    return coordinator.get_handler(event=TOOL_POST, priority=50)


def _ws(tmp_path) -> str:
    ws = os.path.join(str(tmp_path), "ws")
    os.makedirs(ws, exist_ok=True)
    return ws


# ---------------------------------------------------------------------------
# Pre-handler: known tools — allow / deny
# ---------------------------------------------------------------------------


class TestPreHandlerKnownTools:
    async def test_read_file_in_boundary_allowed(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("read_file", file_path=os.path.join(ws, "foo.py")))
        assert result.action == "continue"

    async def test_read_file_outside_boundary_denied(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        # Use a path that is NOT on any default allowlist (/tmp/, /usr/, etc.)
        result = await handler(TOOL_PRE, _pre("read_file", file_path="/outside/the/boundary/secret.txt"))
        assert result.action == "deny"
        assert result.reason is not None
        assert "[WorkspaceBoundary]" in result.reason
        assert result.user_message is not None
        assert result.user_message_level == "error"

    async def test_write_file_in_boundary_allowed(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("write_file", file_path=os.path.join(ws, "out.txt")))
        assert result.action == "continue"

    async def test_write_file_outside_boundary_denied(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("write_file", file_path="/etc/passwd"))
        assert result.action == "deny"

    async def test_edit_file_outside_boundary_denied(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("edit_file", file_path="/etc/hosts"))
        assert result.action == "deny"

    async def test_apply_patch_outside_boundary_denied(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("apply_patch", path="/etc/crontab"))
        assert result.action == "deny"

    async def test_glob_in_boundary_allowed(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("glob", path=ws))
        assert result.action == "continue"

    async def test_grep_outside_boundary_denied(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("grep", path="/home/other/project"))
        assert result.action == "deny"

    async def test_read_tmp_allowlisted(self, coordinator, tmp_path) -> None:
        """Reads from /tmp/ are on the read allowlist."""
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("read_file", file_path="/tmp/test.txt"))
        assert result.action == "continue"

    async def test_write_tmp_allowlisted(self, coordinator, tmp_path) -> None:
        """/tmp/ writes are on the write allowlist."""
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("write_file", file_path="/tmp/output.txt"))
        assert result.action == "continue"

    async def test_missing_path_key_continues(self, coordinator, tmp_path) -> None:
        """Tool in dispatch table but path key absent → continue (missing path = benign)."""
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        # read_file without file_path
        result = await handler(TOOL_PRE, {"tool_name": "read_file", "tool_input": {}})
        assert result.action == "continue"

    async def test_denial_emits_path_denied_event(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        await handler(TOOL_PRE, _pre("read_file", file_path="/outside/secret.txt"))
        events = [e for e, _ in coordinator.emitted_events]
        assert "workspace_boundary:path_denied" in events

    async def test_allowlisted_path_emits_allowlisted_event(
        self, coordinator, tmp_path
    ) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        await handler(TOOL_PRE, _pre("read_file", file_path="/tmp/test.txt"))
        events = [e for e, _ in coordinator.emitted_events]
        assert "workspace_boundary:allowlisted" in events


# ---------------------------------------------------------------------------
# Enforcement modes
# ---------------------------------------------------------------------------


class TestEnforcementModes:
    async def test_enforce_returns_deny(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws, enforcement_mode="enforce")
        result = await handler(TOOL_PRE, _pre("read_file", file_path="/outside/file.txt"))
        assert result.action == "deny"

    async def test_warn_returns_inject_context(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws, enforcement_mode="warn")
        result = await handler(TOOL_PRE, _pre("read_file", file_path="/outside/file.txt"))
        assert result.action == "inject_context"
        assert result.context_injection is not None
        assert "[WorkspaceBoundary]" in result.context_injection
        assert result.context_injection_role == "system"
        assert result.user_message_level == "warning"

    async def test_audit_only_returns_continue(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws, enforcement_mode="audit_only")
        result = await handler(TOOL_PRE, _pre("read_file", file_path="/outside/file.txt"))
        assert result.action == "continue"

    async def test_warn_user_message_is_set(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws, enforcement_mode="warn")
        result = await handler(TOOL_PRE, _pre("read_file", file_path="/outside/file.txt"))
        assert result.user_message is not None


# ---------------------------------------------------------------------------
# Bash handler
# ---------------------------------------------------------------------------


class TestBashHandler:
    async def test_bash_in_boundary_allowed(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("bash", command=f"ls {ws}"))
        assert result.action == "continue"

    async def test_bash_outside_boundary_denied(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        # Use a path outside /tmp/ so it's not on the read allowlist
        result = await handler(TOOL_PRE, _pre("bash", command="cat /outside/the/boundary/secret.txt"))
        assert result.action == "deny"
        assert result.user_message_level == "error"

    async def test_bash_tmp_allowed(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("bash", command="cat /tmp/output.txt"))
        assert result.action == "continue"

    async def test_bash_dollar_paren_warns(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("bash", command="cat $(find . -name '*.txt')"))
        # Not denied — ambiguous patterns warn, not block (by default)
        assert result.action == "continue"
        assert result.user_message is not None
        assert result.user_message_level == "warning"

    async def test_bash_eval_warns(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("bash", command="eval 'ls .'"))
        assert result.action == "continue"
        assert result.user_message is not None

    async def test_bash_source_warns(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("bash", command="source ./setup.sh"))
        assert result.action == "continue"
        assert result.user_message is not None

    async def test_bash_backticks_warns(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("bash", command="cat `echo /tmp/file`"))
        assert result.action == "continue"
        assert result.user_message is not None

    async def test_bash_strict_mode_denies_ambiguous(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws, bash_strict_mode=True)
        result = await handler(TOOL_PRE, _pre("bash", command="cat $(find . -name secret)"))
        assert result.action == "deny"
        assert result.user_message_level == "error"

    async def test_bash_strict_mode_warn_mode_no_deny(self, coordinator, tmp_path) -> None:
        """strict_mode only escalates to deny when enforcement_mode=enforce."""
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(
            coordinator, ws, bash_strict_mode=True, enforcement_mode="warn"
        )
        result = await handler(TOOL_PRE, _pre("bash", command="cat $(find . -name secret)"))
        # warn mode + strict: still warn, not deny
        assert result.action != "deny"

    async def test_bash_clean_command_continues(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("bash", command="echo hello world"))
        assert result.action == "continue"
        assert result.user_message is None

    async def test_bash_ambiguity_emits_bash_warning_event(
        self, coordinator, tmp_path
    ) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        await handler(TOOL_PRE, _pre("bash", command="cat $(find . -name secret)"))
        events = [e for e, _ in coordinator.emitted_events]
        assert "workspace_boundary:bash_warning" in events

    async def test_bash_outside_path_emits_denied_event(
        self, coordinator, tmp_path
    ) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        # Use a path outside /tmp/ so it's not on the default read allowlist
        await handler(TOOL_PRE, _pre("bash", command="cat /outside/the/boundary/file.txt"))
        events = [e for e, _ in coordinator.emitted_events]
        assert "workspace_boundary:path_denied" in events


# ---------------------------------------------------------------------------
# Unknown tools
# ---------------------------------------------------------------------------


class TestUnknownTools:
    async def test_unknown_tool_continues_by_default(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(TOOL_PRE, _pre("some_custom_tool", param="value"))
        assert result.action == "continue"

    async def test_unknown_tool_emits_event(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        await handler(TOOL_PRE, _pre("mystery_tool"))
        events = [e for e, _ in coordinator.emitted_events]
        assert "workspace_boundary:unknown_tool" in events

    async def test_strict_unknown_tools_denies(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(
            coordinator, ws,
            enforcement_mode="enforce",
            strict_unknown_tools=True,
        )
        result = await handler(TOOL_PRE, _pre("mystery_tool"))
        assert result.action == "deny"
        assert result.user_message_level == "error"

    async def test_strict_unknown_tools_warn_mode_still_continues(
        self, coordinator, tmp_path
    ) -> None:
        """strict_unknown_tools only denies when enforcement_mode=enforce."""
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(
            coordinator, ws,
            enforcement_mode="warn",
            strict_unknown_tools=True,
        )
        result = await handler(TOOL_PRE, _pre("mystery_tool"))
        # warn mode: strict_unknown_tools doesn't apply (only enforce)
        assert result.action == "continue"


# ---------------------------------------------------------------------------
# Fail-closed behavior
# ---------------------------------------------------------------------------


class TestFailClosed:
    async def test_none_tool_input_fails_closed(self, coordinator, tmp_path) -> None:
        """Passing None as tool_input causes AttributeError → fail closed → deny."""
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        # tool_input=None: data.get("tool_input", {}) returns None (key present),
        # then tool_input.get(path_key) raises AttributeError.
        result = await handler(
            TOOL_PRE, {"tool_name": "read_file", "tool_input": None}
        )
        assert result.action == "deny"
        assert result.reason is not None
        assert "failing closed" in result.reason

    async def test_fail_closed_user_message_set(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_pre(coordinator, ws)
        result = await handler(
            TOOL_PRE, {"tool_name": "write_file", "tool_input": None}
        )
        assert result.action == "deny"
        assert result.user_message is not None
        assert result.user_message_level == "error"


# ---------------------------------------------------------------------------
# tool:post audit handler
# ---------------------------------------------------------------------------


class TestPostHandler:
    async def test_post_handler_always_returns_continue(self, coordinator, tmp_path) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_post(coordinator, ws)
        result = await handler(
            TOOL_POST,
            {"tool_name": "read_file", "tool_input": {"file_path": os.path.join(ws, "f.py")}},
        )
        assert result.action == "continue"

    async def test_post_handler_emits_path_allowed_event(
        self, coordinator, tmp_path
    ) -> None:
        ws = _ws(tmp_path)
        handler = await _mount_and_get_post(coordinator, ws)
        await handler(
            TOOL_POST,
            {"tool_name": "read_file", "tool_input": {"file_path": os.path.join(ws, "f.py")}},
        )
        events = [e for e, _ in coordinator.emitted_events]
        assert "workspace_boundary:path_allowed" in events

    async def test_post_handler_survives_missing_tool_input(
        self, coordinator, tmp_path
    ) -> None:
        """Post handler must not raise even with minimal event data."""
        ws = _ws(tmp_path)
        handler = await _mount_and_get_post(coordinator, ws)
        result = await handler(TOOL_POST, {"tool_name": "unknown_tool"})
        assert result.action == "continue"
