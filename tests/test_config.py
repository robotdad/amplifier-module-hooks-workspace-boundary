"""Tests for config.py — configuration loading and workspace root discovery."""

from __future__ import annotations

import os

import pytest

from amplifier_module_hooks_workspace_boundary.config import (
    BoundaryConfig,
    _DEFAULT_READ_ALLOWLIST,
    _DEFAULT_TOOL_DISPATCH,
    _DEFAULT_WRITE_ALLOWLIST,
    _discover_from_marker,
    resolve_boundary,
)


# ---------------------------------------------------------------------------
# resolve_boundary — workspace root sources
# ---------------------------------------------------------------------------


class TestResolveBoundaryRoot:
    def test_explicit_workspace_root(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert cfg.workspace_root == str(tmp_path)

    def test_workspace_root_expands_home(self) -> None:
        cfg = resolve_boundary({"workspace_root": "~/Work/project"})
        expected = os.path.abspath(os.path.expanduser("~/Work/project"))
        assert cfg.workspace_root == expected
        assert "~" not in cfg.workspace_root

    def test_workspace_root_expands_env_var(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        monkeypatch.setenv("MY_PROJECT", str(tmp_path))
        cfg = resolve_boundary({"workspace_root": "$MY_PROJECT"})
        assert cfg.workspace_root == str(tmp_path)

    def test_default_none_uses_cwd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        monkeypatch.chdir(str(tmp_path))
        cfg = resolve_boundary(None)
        assert cfg.workspace_root == str(tmp_path)

    def test_default_empty_dict_uses_cwd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        monkeypatch.chdir(str(tmp_path))
        cfg = resolve_boundary({})
        assert cfg.workspace_root == str(tmp_path)

    def test_discover_from_marker_finds_git(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        root = str(tmp_path)
        os.makedirs(os.path.join(root, ".git"))
        subdir = os.path.join(root, "src", "module")
        os.makedirs(subdir)
        monkeypatch.chdir(subdir)

        cfg = resolve_boundary({"discover_from_marker": True})
        assert cfg.workspace_root == root

    def test_discover_from_marker_finds_amplifier(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        root = str(tmp_path)
        os.makedirs(os.path.join(root, ".amplifier"))
        subdir = os.path.join(root, "deep", "nested")
        os.makedirs(subdir)
        monkeypatch.chdir(subdir)

        cfg = resolve_boundary({"discover_from_marker": True})
        assert cfg.workspace_root == root

    def test_discover_from_marker_falls_back_to_cwd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """Marker not found: fall back to CWD with a warning."""
        bare = os.path.join(str(tmp_path), "bare")
        os.makedirs(bare)
        monkeypatch.chdir(bare)

        cfg = resolve_boundary({
            "discover_from_marker": True,
            "marker_files": [".this_marker_will_never_exist_xyzzy"],
        })
        assert cfg.workspace_root == bare


# ---------------------------------------------------------------------------
# resolve_boundary — extra roots
# ---------------------------------------------------------------------------


class TestResolveBoundaryExtraRoots:
    def test_extra_workspace_roots_resolved(self, tmp_path: object) -> None:
        second = os.path.join(str(tmp_path), "second")
        cfg = resolve_boundary({
            "workspace_root": str(tmp_path),
            "extra_workspace_roots": [second],
        })
        assert second in cfg.extra_workspace_roots

    def test_extra_read_roots(self, tmp_path: object) -> None:
        cfg = resolve_boundary({
            "workspace_root": str(tmp_path),
            "extra_read_roots": ["/some/read/path"],
        })
        assert "/some/read/path" in cfg.extra_read_roots

    def test_extra_write_roots(self, tmp_path: object) -> None:
        cfg = resolve_boundary({
            "workspace_root": str(tmp_path),
            "extra_write_roots": ["/some/write/path"],
        })
        assert "/some/write/path" in cfg.extra_write_roots

    def test_extra_roots_home_expansion(self) -> None:
        cfg = resolve_boundary({
            "workspace_root": "/tmp",
            "extra_read_roots": ["~/docs"],
        })
        assert "~" not in cfg.extra_read_roots[0]
        assert cfg.extra_read_roots[0] == os.path.abspath(os.path.expanduser("~/docs"))


# ---------------------------------------------------------------------------
# resolve_boundary — enforcement and flags
# ---------------------------------------------------------------------------


class TestResolveBoundaryFlags:
    def test_enforcement_mode_default(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert cfg.enforcement_mode == "enforce"

    def test_enforcement_mode_warn(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path), "enforcement_mode": "warn"})
        assert cfg.enforcement_mode == "warn"

    def test_enforcement_mode_audit_only(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path), "enforcement_mode": "audit_only"})
        assert cfg.enforcement_mode == "audit_only"

    def test_resolve_symlinks_default_true(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert cfg.resolve_symlinks is True

    def test_resolve_symlinks_configurable_false(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path), "resolve_symlinks": False})
        assert cfg.resolve_symlinks is False

    def test_bash_strict_mode_default_false(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert cfg.bash_strict_mode is False

    def test_bash_strict_mode_configurable(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path), "bash_strict_mode": True})
        assert cfg.bash_strict_mode is True

    def test_strict_unknown_tools_default_false(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert cfg.strict_unknown_tools is False


# ---------------------------------------------------------------------------
# resolve_boundary — allowlists and tool dispatch
# ---------------------------------------------------------------------------


class TestResolveBoundaryAllowlists:
    def test_read_allowlist_populated(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert len(cfg.read_allowlist) > 0
        assert "/tmp/" in cfg.read_allowlist

    def test_write_allowlist_populated(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert "/tmp/" in cfg.write_allowlist

    def test_write_allowlist_does_not_include_home_amplifier(
        self, tmp_path: object
    ) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        home_amp = os.path.expanduser("~/.amplifier/")
        assert home_amp not in cfg.write_allowlist

    def test_default_tool_dispatch_has_all_required_tools(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        required = ["read_file", "write_file", "edit_file", "apply_patch", "glob", "grep"]
        for tool in required:
            assert tool in cfg.tool_dispatch, f"Missing tool in dispatch: {tool}"

    def test_tool_paths_merges_with_defaults(self, tmp_path: object) -> None:
        cfg = resolve_boundary({
            "workspace_root": str(tmp_path),
            "tool_paths": {
                "my_custom_reader": "file_path",
                "my_custom_writer": "output_path",
            },
        })
        # Defaults still present
        assert "read_file" in cfg.tool_dispatch
        assert "write_file" in cfg.tool_dispatch
        # Custom tools added
        assert "my_custom_reader" in cfg.tool_dispatch
        assert cfg.tool_dispatch["my_custom_reader"]["path_key"] == "file_path"
        assert "my_custom_writer" in cfg.tool_dispatch
        assert cfg.tool_dispatch["my_custom_writer"]["path_key"] == "output_path"

    def test_tool_dispatch_read_file_operation(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert cfg.tool_dispatch["read_file"]["path_key"] == "file_path"
        assert cfg.tool_dispatch["read_file"]["operation"] == "read"

    def test_tool_dispatch_write_file_operation(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert cfg.tool_dispatch["write_file"]["path_key"] == "file_path"
        assert cfg.tool_dispatch["write_file"]["operation"] == "write"

    def test_tool_dispatch_apply_patch(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert cfg.tool_dispatch["apply_patch"]["path_key"] == "path"
        assert cfg.tool_dispatch["apply_patch"]["operation"] == "write"


# ---------------------------------------------------------------------------
# _discover_from_marker (unit tests)
# ---------------------------------------------------------------------------


class TestDiscoverFromMarker:
    def test_finds_git_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        root = str(tmp_path)
        os.makedirs(os.path.join(root, ".git"))
        subdir = os.path.join(root, "src", "module")
        os.makedirs(subdir)
        monkeypatch.chdir(subdir)

        result = _discover_from_marker([".git"])
        assert result == root

    def test_finds_amplifier_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        root = str(tmp_path)
        os.makedirs(os.path.join(root, ".amplifier"))
        subdir = os.path.join(root, "deep", "path")
        os.makedirs(subdir)
        monkeypatch.chdir(subdir)

        result = _discover_from_marker([".amplifier"])
        assert result == root

    def test_returns_none_when_no_marker(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        bare = os.path.join(str(tmp_path), "bare")
        os.makedirs(bare)
        monkeypatch.chdir(bare)

        result = _discover_from_marker([".this_will_never_exist_xyzzy_abc123"])
        assert result is None

    def test_finds_nearest_ancestor(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """When multiple markers exist, the nearest ancestor wins."""
        root = str(tmp_path)
        os.makedirs(os.path.join(root, ".git"))  # root marker
        subproject = os.path.join(root, "subproject")
        os.makedirs(os.path.join(subproject, ".git"))  # nested marker
        subdir = os.path.join(subproject, "src")
        os.makedirs(subdir)
        monkeypatch.chdir(subdir)

        result = _discover_from_marker([".git"])
        assert result == subproject  # nearest ancestor, not root
