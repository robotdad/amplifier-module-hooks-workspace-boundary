"""Configuration loading and defaults for hooks-workspace-boundary.

Resolves raw config dicts into typed BoundaryConfig instances with sensible
defaults and environment-aware workspace root discovery.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any

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
# Public API
# ---------------------------------------------------------------------------


def resolve_boundary(config: dict[str, Any] | None) -> BoundaryConfig:
    """Resolve a raw config dict into a BoundaryConfig.

    Workspace root priority:
    1. Explicit ``config['workspace_root']``
    2. Marker file discovery (when ``discover_from_marker=True``)
    3. Process CWD at call time (default)

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

    # --- Extra roots ---
    extra_workspace_roots = [
        _resolve_path(p) for p in cfg.get("extra_workspace_roots", [])
    ]
    extra_read_roots = [_resolve_path(p) for p in cfg.get("extra_read_roots", [])]
    extra_write_roots = [_resolve_path(p) for p in cfg.get("extra_write_roots", [])]

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
    )
