"""Configuration loading and defaults for hooks-workspace-boundary.

Resolves raw config dicts into typed BoundaryConfig instances with sensible
defaults and environment-aware workspace root discovery.

Supports user-contributed safe directories via YAML config files at two levels:
- Global:    ``~/.amplifier/workspace-boundary.yaml``
- Workspace: ``{workspace_root}/.amplifier/workspace-boundary.yaml``

Only path-extension keys are accepted from user configs (``extra_workspace_roots``,
``extra_read_roots``, ``extra_write_roots``).  Security-sensitive keys are
bundle-author-only and are rejected with a warning if found in user configs.

All user config loading happens at mount time — changes take effect only on
session boundaries, preserving the static-at-mount-time security guarantee.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any

try:
    import yaml as _yaml_mod  # type: ignore[import-untyped]
except ImportError:
    _yaml_mod = None

_HAS_YAML: bool = _yaml_mod is not None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default allowlists
# ---------------------------------------------------------------------------

# Resolved at import time so virtualenv prefix is captured once.
_DEFAULT_READ_ALLOWLIST: list[str] = [
    os.path.expanduser("~/.amplifier/"),
    os.path.expanduser("~/.gitconfig"),
    os.path.expanduser("~/.gitignore"),
    os.path.expanduser("~/.ssh/known_hosts"),
    "/tmp/",
    "/var/tmp/",
    "/dev/",
    "/etc/hosts",
    "/etc/resolv.conf",
    "/usr/",
    "/lib/",
    "/lib64/",
    "/opt/",
    sys.prefix,  # active virtualenv / site-packages root
]

# Writes to ~/.amplifier/ are NOT included — requires explicit opt-in.
_DEFAULT_WRITE_ALLOWLIST: list[str] = [
    "/tmp/",
    "/var/tmp/",
]

# ---------------------------------------------------------------------------
# User config file constants
# ---------------------------------------------------------------------------

# Only these keys are accepted from user config files.  Security-sensitive
# keys (enforcement_mode, resolve_symlinks, bash_strict_mode, etc.) are
# bundle-author-only — rejected with a warning if found in user configs.
_ALLOWED_USER_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "extra_workspace_roots",
        "extra_read_roots",
        "extra_write_roots",
    }
)

_USER_CONFIG_FILENAME = "workspace-boundary.yaml"
"""Name of the user config file at both global and workspace levels."""

_GLOBAL_USER_CONFIG_DIR = os.path.expanduser("~/.amplifier")
"""Directory containing the global user config file."""


# ---------------------------------------------------------------------------
# Default tool dispatch table
# ---------------------------------------------------------------------------

_DEFAULT_TOOL_DISPATCH: dict[str, dict[str, str]] = {
    "read_file": {"path_key": "file_path", "operation": "read"},
    "write_file": {"path_key": "file_path", "operation": "write"},
    "edit_file": {"path_key": "file_path", "operation": "write"},
    "apply_patch": {"path_key": "path", "operation": "write"},
    "glob": {"path_key": "path", "operation": "read"},
    "grep": {"path_key": "path", "operation": "read"},
}


# ---------------------------------------------------------------------------
# BoundaryConfig
# ---------------------------------------------------------------------------


@dataclass
class BoundaryConfig:
    """Fully-resolved configuration for the workspace-boundary hook.

    All path fields are absolute strings resolved at mount time.
    Never re-evaluated per-call to prevent CWD-drift from widening the boundary.
    """

    workspace_root: str
    """Primary workspace root (resolved absolute path)."""

    extra_workspace_roots: list[str] = field(default_factory=list)
    """Additional workspace roots to allow access to (resolved absolute paths)."""

    extra_read_roots: list[str] = field(default_factory=list)
    """Additional paths allowed for read access."""

    extra_write_roots: list[str] = field(default_factory=list)
    """Additional paths allowed for write access."""

    read_allowlist: list[str] = field(
        default_factory=lambda: list(_DEFAULT_READ_ALLOWLIST)
    )
    """Paths always permitted for read operations (default allowlist)."""

    write_allowlist: list[str] = field(
        default_factory=lambda: list(_DEFAULT_WRITE_ALLOWLIST)
    )
    """Paths always permitted for write operations (default allowlist)."""

    tool_dispatch: dict[str, dict[str, str]] = field(
        default_factory=lambda: dict(_DEFAULT_TOOL_DISPATCH)
    )
    """Dispatch table: tool_name -> {path_key, operation}."""

    enforcement_mode: str = "enforce"
    """enforce | warn | audit_only."""

    resolve_symlinks: bool = True
    """Whether to call os.path.realpath during path normalization."""

    bash_strict_mode: bool = False
    """Escalate bash ambiguity warnings to deny."""

    strict_unknown_tools: bool = False
    """Deny unknown tool names when enforcement_mode=enforce."""

    user_config_sources: list[dict[str, Any]] = field(default_factory=list)
    """Audit trail of user config files loaded at mount time.

    Each entry: ``{"path": str, "loaded": bool, "paths_added": int, "error": str|None}``
    """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_path(raw: str) -> str:
    """Expand environment variables, user home, and make absolute."""
    path = os.path.expandvars(raw)
    path = os.path.expanduser(path)
    path = os.path.abspath(path)
    return path


def _discover_from_marker(marker_files: list[str] | None = None) -> str | None:
    """Walk up from CWD to find the nearest directory containing a marker file.

    Args:
        marker_files: List of filenames/dirnames to look for.
            Defaults to [".git", ".amplifier"].

    Returns:
        The directory path containing the first marker found, or None.
    """
    if marker_files is None:
        marker_files = [".git", ".amplifier"]

    current = os.path.abspath(os.getcwd())
    while True:
        for marker in marker_files:
            if os.path.exists(os.path.join(current, marker)):
                logger.debug("Discovered workspace root via %r at %s", marker, current)
                return current
        parent = os.path.dirname(current)
        if parent == current:
            break  # Reached filesystem root
        current = parent

    return None


# ---------------------------------------------------------------------------
# User config file loading
# ---------------------------------------------------------------------------


def _load_user_config(path: str) -> dict[str, list[str]]:
    """Load a user config YAML file and return only allowed path-extension keys.

    Silently returns an empty dict when the file does not exist (expected case).
    Logs a warning and returns empty dict on parse errors or I/O failures.
    Logs a warning for any disallowed keys found (security-sensitive keys that
    only the bundle author may set).

    Args:
        path: Absolute path to the user config YAML file.

    Returns:
        Dict with zero or more of ``extra_workspace_roots``, ``extra_read_roots``,
        ``extra_write_roots`` — each a list of raw path strings.
    """
    if not _HAS_YAML:
        logger.debug("PyYAML not available — skipping user config at %s", path)
        return {}

    if not os.path.isfile(path):
        return {}

    assert _yaml_mod is not None  # guarded by _HAS_YAML check above
    try:
        with open(path) as f:
            raw = _yaml_mod.safe_load(f)
    except Exception as exc:
        logger.warning(
            "workspace-boundary: failed to parse user config %s: %s — skipping",
            path,
            exc,
        )
        return {}

    if not isinstance(raw, dict):
        logger.warning(
            "workspace-boundary: user config %s is not a YAML mapping — skipping",
            path,
        )
        return {}

    # Warn about disallowed keys.
    disallowed = set(raw.keys()) - _ALLOWED_USER_CONFIG_KEYS
    if disallowed:
        logger.warning(
            "workspace-boundary: user config %s contains disallowed keys "
            "(only path-extension keys are accepted): %s — ignoring them",
            path,
            ", ".join(sorted(disallowed)),
        )

    # Extract and validate allowed keys.
    result: dict[str, list[str]] = {}
    for key in _ALLOWED_USER_CONFIG_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            logger.warning(
                "workspace-boundary: user config %s key %r must be a list — skipping",
                path,
                key,
            )
            continue
        # Coerce all entries to strings, skip non-string items with a warning.
        paths: list[str] = []
        for item in value:
            if isinstance(item, str):
                paths.append(item)
            else:
                logger.warning(
                    "workspace-boundary: user config %s key %r has non-string entry %r — skipping",
                    path,
                    key,
                    item,
                )
        if paths:
            result[key] = paths

    logger.info(
        "workspace-boundary: loaded user config %s — %d path(s) across %d key(s)",
        path,
        sum(len(v) for v in result.values()),
        len(result),
    )
    return result


def _user_config_paths(workspace_root: str) -> list[str]:
    """Return ordered list of user config file paths to check.

    Order (lower priority first):
    1. Global:    ``~/.amplifier/workspace-boundary.yaml``
    2. Workspace: ``{workspace_root}/.amplifier/workspace-boundary.yaml``

    Later entries take additive precedence (all paths are merged, not replaced).
    """
    return [
        os.path.join(_GLOBAL_USER_CONFIG_DIR, _USER_CONFIG_FILENAME),
        os.path.join(workspace_root, ".amplifier", _USER_CONFIG_FILENAME),
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_boundary(config: dict[str, Any] | None) -> BoundaryConfig:
    """Resolve a raw config dict into a BoundaryConfig.

    Workspace root priority:
    1. Explicit ``config['workspace_root']``
    2. Marker file discovery (when ``discover_from_marker=True``)
    3. Process CWD at call time (default)

    User config file merge order (additive):
    1. Global user config   (``~/.amplifier/workspace-boundary.yaml``)
    2. Workspace user config (``{workspace_root}/.amplifier/workspace-boundary.yaml``)
    3. Bundle config         (the ``config`` dict from the mount plan)

    All three layers are additive — paths from any layer are merged together.
    Only ``extra_workspace_roots``, ``extra_read_roots``, and ``extra_write_roots``
    are accepted from user config files.

    Args:
        config: Raw configuration dict from the mount plan, or None.

    Returns:
        Fully-resolved BoundaryConfig.
    """
    cfg: dict[str, Any] = config or {}

    # --- Workspace root ---
    if cfg.get("workspace_root"):
        workspace_root = _resolve_path(str(cfg["workspace_root"]))
        logger.debug("workspace_root from config: %s", workspace_root)
    elif cfg.get("discover_from_marker", False):
        marker_files = cfg.get("marker_files", [".git", ".amplifier"])
        discovered = _discover_from_marker(marker_files)
        if discovered:
            workspace_root = discovered
            logger.debug("workspace_root from marker discovery: %s", workspace_root)
        else:
            workspace_root = os.path.abspath(os.getcwd())
            logger.warning(
                "Marker discovery enabled but no marker found; falling back to CWD: %s",
                workspace_root,
            )
    else:
        workspace_root = os.path.abspath(os.getcwd())
        logger.debug("workspace_root from CWD: %s", workspace_root)

    # --- Load user config files (additive merge) ---
    user_config_sources: list[dict[str, Any]] = []
    user_extra_workspace_roots: list[str] = []
    user_extra_read_roots: list[str] = []
    user_extra_write_roots: list[str] = []

    for uc_path in _user_config_paths(workspace_root):
        uc_data = _load_user_config(uc_path)
        paths_added = sum(len(v) for v in uc_data.values())
        user_config_sources.append(
            {
                "path": uc_path,
                "loaded": bool(uc_data),
                "paths_added": paths_added,
                "error": None,
            }
        )
        user_extra_workspace_roots.extend(uc_data.get("extra_workspace_roots", []))
        user_extra_read_roots.extend(uc_data.get("extra_read_roots", []))
        user_extra_write_roots.extend(uc_data.get("extra_write_roots", []))

    # --- Merge: user config paths + bundle config paths (all additive) ---
    all_raw_workspace_roots = user_extra_workspace_roots + list(
        cfg.get("extra_workspace_roots", [])
    )
    all_raw_read_roots = user_extra_read_roots + list(cfg.get("extra_read_roots", []))
    all_raw_write_roots = user_extra_write_roots + list(
        cfg.get("extra_write_roots", [])
    )

    extra_workspace_roots = [_resolve_path(p) for p in all_raw_workspace_roots]
    extra_read_roots = [_resolve_path(p) for p in all_raw_read_roots]
    extra_write_roots = [_resolve_path(p) for p in all_raw_write_roots]

    # Deduplicate while preserving order.
    extra_workspace_roots = list(dict.fromkeys(extra_workspace_roots))
    extra_read_roots = list(dict.fromkeys(extra_read_roots))
    extra_write_roots = list(dict.fromkeys(extra_write_roots))

    # --- Tool dispatch: merge defaults with user-provided tool_paths ---
    tool_dispatch: dict[str, dict[str, str]] = dict(_DEFAULT_TOOL_DISPATCH)
    for tool_name, path_key in cfg.get("tool_paths", {}).items():
        tool_dispatch[str(tool_name)] = {"path_key": str(path_key), "operation": "read"}

    return BoundaryConfig(
        workspace_root=workspace_root,
        extra_workspace_roots=extra_workspace_roots,
        extra_read_roots=extra_read_roots,
        extra_write_roots=extra_write_roots,
        tool_dispatch=tool_dispatch,
        enforcement_mode=cfg.get("enforcement_mode", "enforce"),
        resolve_symlinks=cfg.get("resolve_symlinks", True),
        bash_strict_mode=cfg.get("bash_strict_mode", False),
        strict_unknown_tools=cfg.get("strict_unknown_tools", False),
        user_config_sources=user_config_sources,
    )
