"""Tests for boundary.py — path normalization and boundary checking."""

from __future__ import annotations

import os

import pytest

from amplifier_module_hooks_workspace_boundary.boundary import (
    check_path,
    is_within,
    normalize_path,
)
from amplifier_module_hooks_workspace_boundary.config import BoundaryConfig


# ---------------------------------------------------------------------------
# normalize_path
# ---------------------------------------------------------------------------


class TestNormalizePath:
    def test_strips_single_quotes(self) -> None:
        result = normalize_path("'/tmp/foo'", resolve_symlinks=False)
        assert result == "/tmp/foo"

    def test_strips_double_quotes(self) -> None:
        result = normalize_path('"/tmp/foo"', resolve_symlinks=False)
        assert result == "/tmp/foo"

    def test_expands_home(self) -> None:
        result = normalize_path("~/foo", resolve_symlinks=False)
        assert result == os.path.join(os.path.expanduser("~"), "foo")

    def test_collapses_dotdot(self) -> None:
        result = normalize_path("/tmp/foo/../bar", resolve_symlinks=False)
        assert result == "/tmp/bar"

    def test_abspath_for_relative(self, monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
        monkeypatch.chdir(str(tmp_path))
        result = normalize_path("subdir/file.txt", resolve_symlinks=False)
        assert result == os.path.join(str(tmp_path), "subdir", "file.txt")

    def test_strips_whitespace(self) -> None:
        result = normalize_path("  /tmp/foo  ", resolve_symlinks=False)
        assert result == "/tmp/foo"

    def test_resolves_symlinks_when_enabled(self, tmp_path: object) -> None:
        tmp = str(tmp_path)
        real_dir = os.path.join(tmp, "real")
        os.makedirs(real_dir)
        link = os.path.join(tmp, "link")
        os.symlink(real_dir, link)

        result = normalize_path(link, resolve_symlinks=True)
        assert result == os.path.realpath(real_dir)

    def test_no_symlink_resolution_when_disabled(self, tmp_path: object) -> None:
        tmp = str(tmp_path)
        real_dir = os.path.join(tmp, "real")
        os.makedirs(real_dir)
        link = os.path.join(tmp, "link")
        os.symlink(real_dir, link)

        result = normalize_path(link, resolve_symlinks=False)
        assert result == os.path.abspath(link)


# ---------------------------------------------------------------------------
# is_within
# ---------------------------------------------------------------------------


class TestIsWithin:
    def test_exact_match(self) -> None:
        assert is_within("/workspace", "/workspace") is True

    def test_direct_child(self) -> None:
        assert is_within("/workspace/src", "/workspace") is True

    def test_deep_child(self) -> None:
        assert is_within("/workspace/src/foo/bar.py", "/workspace") is True

    def test_sibling_path_rejected(self) -> None:
        # /workspace2 must NOT match /workspace
        assert is_within("/workspace2", "/workspace") is False

    def test_sibling_prefix_rejected(self) -> None:
        # /workspacefoo/bar must NOT match /workspace
        assert is_within("/workspacefoo/bar", "/workspace") is False

    def test_parent_rejected(self) -> None:
        assert is_within("/", "/workspace") is False

    def test_unrelated_path_rejected(self) -> None:
        assert is_within("/home/user/other", "/workspace") is False

    def test_empty_strings(self) -> None:
        # Degenerate: both empty — equal, so True
        assert is_within("", "") is True

    def test_non_absolute_root_not_considered(self) -> None:
        # Degenerate safety check: empty path is not within a non-empty root
        assert is_within("", "/workspace") is False


# ---------------------------------------------------------------------------
# check_path fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: object) -> str:
    ws = os.path.join(str(tmp_path), "workspace")
    os.makedirs(ws)
    return ws


@pytest.fixture
def cfg(workspace: str) -> BoundaryConfig:
    """BoundaryConfig with EMPTY allowlists for testing pure boundary logic.

    Pytest's tmp_path is under /tmp/ which is on the default read allowlist.
    Empty allowlists ensure tests are checking workspace boundary rules,
    not accidentally passing because /tmp/ is allowlisted.
    """
    return BoundaryConfig(
        workspace_root=workspace,
        resolve_symlinks=True,
        read_allowlist=[],
        write_allowlist=[],
    )


@pytest.fixture
def cfg_full(workspace: str) -> BoundaryConfig:
    """BoundaryConfig with DEFAULT allowlists for testing allowlist logic."""
    return BoundaryConfig(workspace_root=workspace, resolve_symlinks=True)


# ---------------------------------------------------------------------------
# check_path — basic allow / deny
# ---------------------------------------------------------------------------


class TestCheckPathBasic:
    def test_path_within_boundary_allowed(self, workspace: str, cfg: BoundaryConfig) -> None:
        target = os.path.join(workspace, "src", "main.py")
        result = check_path(target, "read", cfg)
        assert result.allowed is True
        assert result.event == "workspace_boundary:path_allowed"
        assert result.resolved_path is not None

    def test_path_outside_boundary_denied(
        self, tmp_path: object, workspace: str, cfg: BoundaryConfig
    ) -> None:
        outside = os.path.join(str(tmp_path), "other", "secret.txt")
        result = check_path(outside, "read", cfg)
        assert result.allowed is False
        assert result.reason is not None
        assert "[WorkspaceBoundary]" in result.reason
        assert result.event == "workspace_boundary:path_denied"

    def test_denial_reason_contains_boundary(
        self, tmp_path: object, workspace: str, cfg: BoundaryConfig
    ) -> None:
        outside = os.path.join(str(tmp_path), "intruder", "file.txt")
        result = check_path(outside, "write", cfg)
        assert workspace in result.reason

    def test_denial_reason_contains_operation(
        self, tmp_path: object, workspace: str, cfg: BoundaryConfig
    ) -> None:
        outside = os.path.join(str(tmp_path), "evil.txt")
        result = check_path(outside, "write", cfg)
        assert "write" in result.reason

    def test_workspace_root_itself_allowed(self, workspace: str, cfg: BoundaryConfig) -> None:
        result = check_path(workspace, "read", cfg)
        assert result.allowed is True


# ---------------------------------------------------------------------------
# check_path — allowlists
# ---------------------------------------------------------------------------


class TestCheckPathAllowlists:
    """These tests use cfg_full (default allowlists) to verify allowlist behavior."""

    def test_tmp_on_read_allowlist(self, cfg_full: BoundaryConfig) -> None:
        result = check_path("/tmp/somefile.txt", "read", cfg_full)
        assert result.allowed is True
        assert result.allowlist_rule == "read_allowlist"
        assert result.event == "workspace_boundary:allowlisted"

    def test_tmp_on_write_allowlist(self, cfg_full: BoundaryConfig) -> None:
        result = check_path("/tmp/output.txt", "write", cfg_full)
        assert result.allowed is True
        assert result.allowlist_rule == "write_allowlist"

    def test_home_amplifier_readable(self, cfg_full: BoundaryConfig) -> None:
        skill_path = os.path.join(os.path.expanduser("~/.amplifier"), "skills", "foo.md")
        result = check_path(skill_path, "read", cfg_full)
        assert result.allowed is True

    def test_home_amplifier_not_writable_by_default(
        self, cfg_full: BoundaryConfig
    ) -> None:
        amplifier_cfg = os.path.join(os.path.expanduser("~/.amplifier"), "config.yaml")
        amplifier_root = os.path.expanduser("~/.amplifier")
        workspace = cfg_full.workspace_root
        # Only assert if workspace is NOT inside ~/.amplifier (avoids false failures
        # for developers who happen to work inside their .amplifier directory).
        if not workspace.startswith(amplifier_root):
            result = check_path(amplifier_cfg, "write", cfg_full)
            assert result.allowed is False

    def test_extra_read_root_allowed(self, tmp_path: object, workspace: str) -> None:
        extra = os.path.join(str(tmp_path), "extra_read")
        os.makedirs(extra)
        c = BoundaryConfig(workspace_root=workspace, read_allowlist=[], write_allowlist=[], extra_read_roots=[extra])
        result = check_path(os.path.join(extra, "file.txt"), "read", c)
        assert result.allowed is True

    def test_extra_write_root_allowed(self, tmp_path: object, workspace: str) -> None:
        extra = os.path.join(str(tmp_path), "extra_write")
        os.makedirs(extra)
        c = BoundaryConfig(workspace_root=workspace, read_allowlist=[], write_allowlist=[], extra_write_roots=[extra])
        result = check_path(os.path.join(extra, "out.txt"), "write", c)
        assert result.allowed is True

    def test_extra_workspace_root_allowed(self, tmp_path: object, workspace: str) -> None:
        second = os.path.join(str(tmp_path), "second_ws")
        os.makedirs(second)
        c = BoundaryConfig(workspace_root=workspace, read_allowlist=[], write_allowlist=[], extra_workspace_roots=[second])
        result = check_path(os.path.join(second, "src.py"), "read", c)
        assert result.allowed is True

    def test_extra_workspace_root_write_allowed(self, tmp_path: object, workspace: str) -> None:
        second = os.path.join(str(tmp_path), "second_ws")
        os.makedirs(second)
        c = BoundaryConfig(workspace_root=workspace, read_allowlist=[], write_allowlist=[], extra_workspace_roots=[second])
        result = check_path(os.path.join(second, "out.txt"), "write", c)
        assert result.allowed is True


# ---------------------------------------------------------------------------
# check_path — dotdot traversal
# ---------------------------------------------------------------------------


class TestDotdotTraversal:
    def test_dotdot_escaping_boundary_denied(
        self, tmp_path: object, workspace: str, cfg: BoundaryConfig
    ) -> None:
        # Craft a raw path that, after abspath, resolves outside the workspace.
        raw = workspace + "/subdir/../../escape"
        result = check_path(raw, "read", cfg)
        # After normalization: tmp_path/escape — outside workspace
        assert result.allowed is False

    def test_dotdot_staying_inside_allowed(self, workspace: str, cfg: BoundaryConfig) -> None:
        # subdir/../other stays inside workspace
        raw = workspace + "/subdir/../other/file.txt"
        result = check_path(raw, "read", cfg)
        assert result.allowed is True


# ---------------------------------------------------------------------------
# check_path — symlinks
# ---------------------------------------------------------------------------


class TestSymlinks:
    def test_symlink_pointing_outside_denied(
        self, tmp_path: object, workspace: str, cfg: BoundaryConfig
    ) -> None:
        """Symlink inside workspace pointing outside is denied (resolve_symlinks=True)."""
        outside = os.path.join(str(tmp_path), "sensitive")
        os.makedirs(outside)
        secret = os.path.join(outside, "secret.txt")
        with open(secret, "w") as f:
            f.write("secret")

        link = os.path.join(workspace, "evil_link")
        os.symlink(outside, link)

        result = check_path(os.path.join(link, "secret.txt"), "read", cfg)
        assert result.allowed is False

    def test_symlink_not_resolved_when_disabled(
        self, tmp_path: object, workspace: str
    ) -> None:
        """resolve_symlinks=False: symlink inside workspace appears in-boundary."""
        c = BoundaryConfig(workspace_root=workspace, resolve_symlinks=False)
        outside = os.path.join(str(tmp_path), "sensitive")
        os.makedirs(outside)

        link = os.path.join(workspace, "link")
        os.symlink(outside, link)

        # With symlink resolution off, the link path is within workspace.
        result = check_path(link, "read", c)
        assert result.allowed is True
