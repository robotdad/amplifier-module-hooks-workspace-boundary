"""Path normalization and boundary checking for hooks-workspace-boundary.

Implements the path normalization pipeline and check-order logic from DESIGN.md §5 and §8.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .config import BoundaryConfig

logger = logging.getLogger(__name__)


@dataclass
class PathCheckResult:
    """Result of a boundary check on a single path."""

    allowed: bool
    """Whether the path is permitted."""

    reason: str | None = None
    """Human-readable denial reason, shown to the agent on deny. None if allowed."""

    allowlist_rule: str | None = None
    """Which allowlist permitted this path (e.g. 'read_allowlist'). None if not allowlisted."""

    event: str | None = None
    """Observability event name to emit for this result."""

    resolved_path: str | None = None
    """The resolved absolute path that was evaluated."""


def normalize_path(raw_path: str, resolve_symlinks: bool = True) -> str:
    """Normalize a raw path through the required pipeline.

    Pipeline (per DESIGN.md §5):
        raw → strip quotes → expandvars → expanduser → abspath → realpath

    Args:
        raw_path: The raw path string from tool input.
        resolve_symlinks: Call os.path.realpath (default True).

    Returns:
        Normalized absolute path string.

    Raises:
        OSError: If realpath resolution fails on a broken symlink or similar.
    """
    path = raw_path.strip().strip("'\"")
    path = os.path.expandvars(path)
    path = os.path.expanduser(path)
    path = os.path.abspath(path)
    if resolve_symlinks:
        path = os.path.realpath(path)
    return path


def is_within(path: str, root: str) -> bool:
    """Return True if path equals root or is a strict descendant of root.

    Uses the trailing-separator predicate to prevent /workspace2 matching /workspace.

    Args:
        path: Normalized absolute path to check.
        root: Normalized absolute root to check against.

    Returns:
        True if path is within root.
    """
    return path == root or path.startswith(root + os.sep)


def _format_denial(resolved_path: str, boundary: str, operation: str) -> str:
    """Build the standard denial reason string (seen by the agent)."""
    return (
        f"[WorkspaceBoundary] Path access denied.\n"
        f"  Requested path: {resolved_path}\n"
        f"  Allowed boundary: {boundary}\n"
        f"  Operation: {operation}\n\n"
        f"This path is outside the configured workspace boundary. To allow access,\n"
        f"add the path to extra_workspace_roots (or extra_read_roots / extra_write_roots)\n"
        f"in the hook configuration."
    )


def check_path(
    raw_path: str, operation: str, config: BoundaryConfig
) -> PathCheckResult:
    """Run the full boundary check for a single path.

    Check order (DESIGN.md §8):
    1. write op  → check write allowlist  → permit if match
    2. read/exec → check read allowlist   → permit if match
    3. check workspace roots (primary + extra) → permit if match
    4. deny

    Args:
        raw_path: Raw path string from tool input.
        operation: One of ``"read"``, ``"write"``, or ``"exec"`` (bash paths).
        config: BoundaryConfig instance.

    Returns:
        PathCheckResult indicating allowed/denied with reason and event name.
    """
    try:
        path = normalize_path(raw_path, config.resolve_symlinks)
    except Exception as exc:
        logger.warning("Failed to normalize path %r: %s", raw_path, exc)
        return PathCheckResult(
            allowed=False,
            reason=(
                f"[WorkspaceBoundary] could not resolve path — "
                f"boundary policy requires resolution: {exc}"
            ),
            event="workspace_boundary:path_denied",
            resolved_path=raw_path,
        )

    def _matches_any(roots: list[str]) -> bool:
        for root in roots:
            try:
                norm_root = normalize_path(root, config.resolve_symlinks)
                if is_within(path, norm_root) or path == norm_root:
                    return True
            except Exception as exc:
                logger.debug("Could not normalize allowlist root %r: %s", root, exc)
        return False

    # Step 1 — write allowlist (write operations only)
    if operation == "write":
        combined_write = list(config.write_allowlist) + list(config.extra_write_roots)
        if _matches_any(combined_write):
            return PathCheckResult(
                allowed=True,
                allowlist_rule="write_allowlist",
                event="workspace_boundary:allowlisted",
                resolved_path=path,
            )

    # Step 2 — read allowlist (read and exec operations)
    if operation in ("read", "exec"):
        combined_read = list(config.read_allowlist) + list(config.extra_read_roots)
        if _matches_any(combined_read):
            return PathCheckResult(
                allowed=True,
                allowlist_rule="read_allowlist",
                event="workspace_boundary:allowlisted",
                resolved_path=path,
            )

    # Step 3 — workspace roots
    all_roots = [config.workspace_root] + list(config.extra_workspace_roots)
    if _matches_any(all_roots):
        return PathCheckResult(
            allowed=True,
            event="workspace_boundary:path_allowed",
            resolved_path=path,
        )

    # Step 4 — deny
    return PathCheckResult(
        allowed=False,
        reason=_format_denial(path, config.workspace_root, operation),
        event="workspace_boundary:path_denied",
        resolved_path=path,
    )
