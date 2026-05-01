"""Tests for config.py — configuration loading and workspace root discovery."""

from __future__ import annotations

import os

import pytest
import yaml

from amplifier_module_hooks_workspace_boundary.config import (
    _USER_CONFIG_FILENAME,
    _discover_from_marker,
    _load_user_config,
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

        cfg = resolve_boundary(
            {
                "discover_from_marker": True,
                "marker_files": [".this_marker_will_never_exist_xyzzy"],
            }
        )
        assert cfg.workspace_root == bare


# ---------------------------------------------------------------------------
# resolve_boundary — extra roots
# ---------------------------------------------------------------------------


class TestResolveBoundaryExtraRoots:
    def test_extra_workspace_roots_resolved(self, tmp_path: object) -> None:
        second = os.path.join(str(tmp_path), "second")
        cfg = resolve_boundary(
            {
                "workspace_root": str(tmp_path),
                "extra_workspace_roots": [second],
            }
        )
        assert second in cfg.extra_workspace_roots

    def test_extra_read_roots(self, tmp_path: object) -> None:
        cfg = resolve_boundary(
            {
                "workspace_root": str(tmp_path),
                "extra_read_roots": ["/some/read/path"],
            }
        )
        assert "/some/read/path" in cfg.extra_read_roots

    def test_extra_write_roots(self, tmp_path: object) -> None:
        cfg = resolve_boundary(
            {
                "workspace_root": str(tmp_path),
                "extra_write_roots": ["/some/write/path"],
            }
        )
        assert "/some/write/path" in cfg.extra_write_roots

    def test_extra_roots_home_expansion(self) -> None:
        cfg = resolve_boundary(
            {
                "workspace_root": "/tmp",
                "extra_read_roots": ["~/docs"],
            }
        )
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
        cfg = resolve_boundary(
            {"workspace_root": str(tmp_path), "enforcement_mode": "warn"}
        )
        assert cfg.enforcement_mode == "warn"

    def test_enforcement_mode_audit_only(self, tmp_path: object) -> None:
        cfg = resolve_boundary(
            {"workspace_root": str(tmp_path), "enforcement_mode": "audit_only"}
        )
        assert cfg.enforcement_mode == "audit_only"

    def test_resolve_symlinks_default_true(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert cfg.resolve_symlinks is True

    def test_resolve_symlinks_configurable_false(self, tmp_path: object) -> None:
        cfg = resolve_boundary(
            {"workspace_root": str(tmp_path), "resolve_symlinks": False}
        )
        assert cfg.resolve_symlinks is False

    def test_bash_strict_mode_default_false(self, tmp_path: object) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        assert cfg.bash_strict_mode is False

    def test_bash_strict_mode_configurable(self, tmp_path: object) -> None:
        cfg = resolve_boundary(
            {"workspace_root": str(tmp_path), "bash_strict_mode": True}
        )
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

    def test_default_tool_dispatch_has_all_required_tools(
        self, tmp_path: object
    ) -> None:
        cfg = resolve_boundary({"workspace_root": str(tmp_path)})
        required = [
            "read_file",
            "write_file",
            "edit_file",
            "apply_patch",
            "glob",
            "grep",
        ]
        for tool in required:
            assert tool in cfg.tool_dispatch, f"Missing tool in dispatch: {tool}"

    def test_tool_paths_merges_with_defaults(self, tmp_path: object) -> None:
        cfg = resolve_boundary(
            {
                "workspace_root": str(tmp_path),
                "tool_paths": {
                    "my_custom_reader": "file_path",
                    "my_custom_writer": "output_path",
                },
            }
        )
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


# ---------------------------------------------------------------------------
# _load_user_config (unit tests)
# ---------------------------------------------------------------------------


class TestLoadUserConfig:
    """Tests for _load_user_config — YAML file loading with key filtering."""

    def test_missing_file_returns_empty(self, tmp_path: object) -> None:
        result = _load_user_config(os.path.join(str(tmp_path), "nonexistent.yaml"))
        assert result == {}

    def test_loads_extra_workspace_roots(self, tmp_path: object) -> None:
        cfg_file = os.path.join(str(tmp_path), "wb.yaml")
        with open(cfg_file, "w") as f:
            yaml.dump({"extra_workspace_roots": ["/data/shared", "~/libs"]}, f)
        result = _load_user_config(cfg_file)
        assert result == {"extra_workspace_roots": ["/data/shared", "~/libs"]}

    def test_loads_extra_read_roots(self, tmp_path: object) -> None:
        cfg_file = os.path.join(str(tmp_path), "wb.yaml")
        with open(cfg_file, "w") as f:
            yaml.dump({"extra_read_roots": ["/mnt/datasets"]}, f)
        result = _load_user_config(cfg_file)
        assert result == {"extra_read_roots": ["/mnt/datasets"]}

    def test_loads_extra_write_roots(self, tmp_path: object) -> None:
        cfg_file = os.path.join(str(tmp_path), "wb.yaml")
        with open(cfg_file, "w") as f:
            yaml.dump({"extra_write_roots": ["/output"]}, f)
        result = _load_user_config(cfg_file)
        assert result == {"extra_write_roots": ["/output"]}

    def test_loads_all_three_keys(self, tmp_path: object) -> None:
        cfg_file = os.path.join(str(tmp_path), "wb.yaml")
        data = {
            "extra_workspace_roots": ["/ws2"],
            "extra_read_roots": ["/docs"],
            "extra_write_roots": ["/out"],
        }
        with open(cfg_file, "w") as f:
            yaml.dump(data, f)
        result = _load_user_config(cfg_file)
        assert result == data

    def test_disallowed_keys_ignored(self, tmp_path: object) -> None:
        """Security-sensitive keys like enforcement_mode are silently ignored."""
        cfg_file = os.path.join(str(tmp_path), "wb.yaml")
        data = {
            "enforcement_mode": "audit_only",
            "resolve_symlinks": False,
            "bash_strict_mode": True,
            "extra_read_roots": ["/safe"],
        }
        with open(cfg_file, "w") as f:
            yaml.dump(data, f)
        result = _load_user_config(cfg_file)
        assert result == {"extra_read_roots": ["/safe"]}
        # Disallowed keys not present
        assert "enforcement_mode" not in result
        assert "resolve_symlinks" not in result
        assert "bash_strict_mode" not in result

    def test_non_list_value_skipped(self, tmp_path: object) -> None:
        cfg_file = os.path.join(str(tmp_path), "wb.yaml")
        with open(cfg_file, "w") as f:
            yaml.dump({"extra_read_roots": "/single/string"}, f)
        result = _load_user_config(cfg_file)
        assert result == {}

    def test_non_string_entries_skipped(self, tmp_path: object) -> None:
        cfg_file = os.path.join(str(tmp_path), "wb.yaml")
        with open(cfg_file, "w") as f:
            yaml.dump({"extra_read_roots": ["/valid", 42, "/also-valid"]}, f)
        result = _load_user_config(cfg_file)
        assert result == {"extra_read_roots": ["/valid", "/also-valid"]}

    def test_malformed_yaml_returns_empty(self, tmp_path: object) -> None:
        cfg_file = os.path.join(str(tmp_path), "wb.yaml")
        with open(cfg_file, "w") as f:
            f.write(": : : not valid yaml [[[")
        result = _load_user_config(cfg_file)
        assert result == {}

    def test_non_mapping_yaml_returns_empty(self, tmp_path: object) -> None:
        """A YAML file that parses as a list instead of a mapping."""
        cfg_file = os.path.join(str(tmp_path), "wb.yaml")
        with open(cfg_file, "w") as f:
            yaml.dump(["/path1", "/path2"], f)
        result = _load_user_config(cfg_file)
        assert result == {}

    def test_empty_file_returns_empty(self, tmp_path: object) -> None:
        cfg_file = os.path.join(str(tmp_path), "wb.yaml")
        with open(cfg_file, "w") as f:
            f.write("")
        result = _load_user_config(cfg_file)
        assert result == {}

    def test_empty_list_not_included(self, tmp_path: object) -> None:
        cfg_file = os.path.join(str(tmp_path), "wb.yaml")
        with open(cfg_file, "w") as f:
            yaml.dump({"extra_read_roots": []}, f)
        result = _load_user_config(cfg_file)
        assert result == {}


# ---------------------------------------------------------------------------
# resolve_boundary — user config file integration
# ---------------------------------------------------------------------------


def _write_user_config(path: str, data: dict) -> None:
    """Helper: write a YAML user config file, creating parent dirs."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f)


class TestUserConfigIntegration:
    """Tests for user config file loading and merging in resolve_boundary()."""

    def test_global_config_adds_paths(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """Global ~/.amplifier/workspace-boundary.yaml adds extra roots."""
        ws = os.path.join(str(tmp_path), "workspace")
        os.makedirs(ws)
        global_dir = os.path.join(str(tmp_path), "fake_home", ".amplifier")
        global_cfg = os.path.join(global_dir, _USER_CONFIG_FILENAME)
        _write_user_config(global_cfg, {"extra_read_roots": ["/global/docs"]})

        monkeypatch.setattr(
            "amplifier_module_hooks_workspace_boundary.config._GLOBAL_USER_CONFIG_DIR",
            global_dir,
        )
        cfg = resolve_boundary({"workspace_root": ws})
        assert "/global/docs" in cfg.extra_read_roots

    def test_workspace_config_adds_paths(self, tmp_path: object) -> None:
        """Workspace .amplifier/workspace-boundary.yaml adds extra roots."""
        ws = str(tmp_path)
        ws_cfg = os.path.join(ws, ".amplifier", _USER_CONFIG_FILENAME)
        _write_user_config(ws_cfg, {"extra_write_roots": ["/workspace/output"]})

        cfg = resolve_boundary({"workspace_root": ws})
        assert "/workspace/output" in cfg.extra_write_roots

    def test_both_configs_merge_additively(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """Global and workspace configs merge together."""
        ws = os.path.join(str(tmp_path), "workspace")
        os.makedirs(ws)

        global_dir = os.path.join(str(tmp_path), "fake_home", ".amplifier")
        global_cfg = os.path.join(global_dir, _USER_CONFIG_FILENAME)
        _write_user_config(global_cfg, {"extra_read_roots": ["/global/path"]})

        ws_cfg = os.path.join(ws, ".amplifier", _USER_CONFIG_FILENAME)
        _write_user_config(ws_cfg, {"extra_read_roots": ["/workspace/path"]})

        monkeypatch.setattr(
            "amplifier_module_hooks_workspace_boundary.config._GLOBAL_USER_CONFIG_DIR",
            global_dir,
        )
        cfg = resolve_boundary({"workspace_root": ws})
        assert "/global/path" in cfg.extra_read_roots
        assert "/workspace/path" in cfg.extra_read_roots

    def test_user_config_merges_with_bundle_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """User config paths merge additively with bundle config paths."""
        ws = os.path.join(str(tmp_path), "workspace")
        os.makedirs(ws)

        global_dir = os.path.join(str(tmp_path), "fake_home", ".amplifier")
        global_cfg = os.path.join(global_dir, _USER_CONFIG_FILENAME)
        _write_user_config(global_cfg, {"extra_workspace_roots": ["/user/shared"]})

        monkeypatch.setattr(
            "amplifier_module_hooks_workspace_boundary.config._GLOBAL_USER_CONFIG_DIR",
            global_dir,
        )
        cfg = resolve_boundary(
            {
                "workspace_root": ws,
                "extra_workspace_roots": ["/bundle/extra"],
            }
        )
        assert "/user/shared" in cfg.extra_workspace_roots
        assert "/bundle/extra" in cfg.extra_workspace_roots

    def test_user_config_paths_resolved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """Paths from user configs are resolved (~ expanded, made absolute)."""
        ws = str(tmp_path)
        ws_cfg = os.path.join(ws, ".amplifier", _USER_CONFIG_FILENAME)
        _write_user_config(ws_cfg, {"extra_read_roots": ["~/my-docs"]})

        cfg = resolve_boundary({"workspace_root": ws})
        # Path should be expanded and absolute.
        assert "~" not in cfg.extra_read_roots[0]
        assert os.path.isabs(cfg.extra_read_roots[0])

    def test_duplicate_paths_deduplicated(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """Same path from multiple sources appears only once."""
        ws = os.path.join(str(tmp_path), "workspace")
        os.makedirs(ws)

        global_dir = os.path.join(str(tmp_path), "fake_home", ".amplifier")
        global_cfg = os.path.join(global_dir, _USER_CONFIG_FILENAME)
        _write_user_config(global_cfg, {"extra_read_roots": ["/shared"]})

        ws_cfg = os.path.join(ws, ".amplifier", _USER_CONFIG_FILENAME)
        _write_user_config(ws_cfg, {"extra_read_roots": ["/shared"]})

        monkeypatch.setattr(
            "amplifier_module_hooks_workspace_boundary.config._GLOBAL_USER_CONFIG_DIR",
            global_dir,
        )
        cfg = resolve_boundary(
            {
                "workspace_root": ws,
                "extra_read_roots": ["/shared"],
            }
        )
        assert cfg.extra_read_roots.count("/shared") == 1

    def test_user_config_sources_audit_trail(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """user_config_sources records which files were checked and loaded."""
        ws = os.path.join(str(tmp_path), "workspace")
        os.makedirs(ws)

        global_dir = os.path.join(str(tmp_path), "fake_home", ".amplifier")
        global_cfg = os.path.join(global_dir, _USER_CONFIG_FILENAME)
        _write_user_config(global_cfg, {"extra_read_roots": ["/docs"]})
        # No workspace config file — should be recorded as not loaded.

        monkeypatch.setattr(
            "amplifier_module_hooks_workspace_boundary.config._GLOBAL_USER_CONFIG_DIR",
            global_dir,
        )
        cfg = resolve_boundary({"workspace_root": ws})

        assert len(cfg.user_config_sources) == 2
        # Global was loaded.
        global_source = cfg.user_config_sources[0]
        assert global_source["path"] == global_cfg
        assert global_source["loaded"] is True
        assert global_source["paths_added"] == 1
        # Workspace was not found.
        ws_source = cfg.user_config_sources[1]
        assert ws_source["loaded"] is False
        assert ws_source["paths_added"] == 0

    def test_no_user_configs_empty_audit_trail(self, tmp_path: object) -> None:
        """When no user config files exist, audit trail has entries but loaded=False."""
        ws = os.path.join(str(tmp_path), "workspace")
        os.makedirs(ws)
        cfg = resolve_boundary({"workspace_root": ws})
        assert len(cfg.user_config_sources) == 2
        assert all(s["loaded"] is False for s in cfg.user_config_sources)

    def test_disallowed_keys_in_user_config_do_not_affect_enforcement(
        self, tmp_path: object
    ) -> None:
        """User cannot override enforcement_mode via config file."""
        ws = str(tmp_path)
        ws_cfg = os.path.join(ws, ".amplifier", _USER_CONFIG_FILENAME)
        _write_user_config(
            ws_cfg,
            {
                "enforcement_mode": "audit_only",
                "extra_read_roots": ["/safe"],
            },
        )
        cfg = resolve_boundary({"workspace_root": ws, "enforcement_mode": "enforce"})
        # enforcement_mode must come from bundle config, not user config.
        assert cfg.enforcement_mode == "enforce"
        # But the allowed key should still work.
        assert "/safe" in cfg.extra_read_roots
