"""Amplifier hook module: hooks-workspace-boundary.

Enforces filesystem workspace boundaries by intercepting ``tool:pre`` events
and denying access to paths outside the configured workspace root.

Mount entry point::

    async def mount(coordinator, config: dict | None = None) -> Callable | None

Subscriptions:
- ``tool:pre``  priority=5  — enforcement (fires before hooks-approval at 10)
- ``tool:post`` priority=50 — audit logging

See DESIGN.md for the full specification.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from . import boundary as _boundary_mod
from .bash_parser import detect_ambiguous_patterns, extract_absolute_paths
from .config import BoundaryConfig, resolve_boundary

logger = logging.getLogger(__name__)


def _try_emit(coordinator: Any, event_name: str, payload: dict) -> None:
    """Emit an observability event, silently ignoring coordinator API mismatches."""
    try:
        coordinator.emit(event_name, payload)
    except AttributeError:
        pass  # coordinator.emit may not exist in all deployments
    except Exception as exc:
        logger.debug("Failed to emit event %s: %s", event_name, exc)


async def mount(
    coordinator: Any, config: dict[str, Any] | None = None
) -> Callable | None:
    """Mount the workspace-boundary hook into the coordinator.

    Resolves configuration, registers ``tool:pre`` (enforcement) and
    ``tool:post`` (audit) handlers, and registers observable event names.

    Args:
        coordinator: The Amplifier coordinator instance.
        config: Optional configuration dict. Supported keys:
            ``workspace_root``, ``extra_workspace_roots``, ``extra_read_roots``,
            ``extra_write_roots``, ``enforcement_mode``, ``resolve_symlinks``,
            ``bash_strict_mode``, ``strict_unknown_tools``, ``tool_paths``.

    Returns:
        Cleanup callable that unregisters both handlers.
    """
    # Deferred imports — amplifier_core is a peer dep, not in project dependencies.
    from amplifier_core import HookResult  # type: ignore[import]
    from amplifier_core.events import TOOL_POST, TOOL_PRE  # type: ignore[import]

    try:
        boundary_config = resolve_boundary(config)
    except Exception as exc:
        logger.error("Failed to resolve boundary config — falling back to CWD: %s", exc)
        boundary_config = BoundaryConfig(workspace_root=os.path.abspath(os.getcwd()))

    logger.info(
        "workspace-boundary mounted: root=%s enforcement=%s resolve_symlinks=%s",
        boundary_config.workspace_root,
        boundary_config.enforcement_mode,
        boundary_config.resolve_symlinks,
    )

    # ------------------------------------------------------------------
    # Response builders
    # ------------------------------------------------------------------

    def _deny(resolved_path: str, reason: str) -> HookResult:
        return HookResult(
            action="deny",
            reason=reason,
            user_message=f"Boundary violation blocked: {resolved_path}",
            user_message_level="error",
        )

    def _warn(resolved_path: str, message: str) -> HookResult:
        return HookResult(
            action="inject_context",
            context_injection=f"[WorkspaceBoundary] {message}",
            context_injection_role="system",
            user_message=message,
            user_message_level="warning",
        )

    # ------------------------------------------------------------------
    # Known-tool handler
    # ------------------------------------------------------------------

    async def _handle_known_tool(
        tool_name: str,
        tool_input: dict,
        path_key: str,
        operation: str,
    ) -> HookResult:
        """Handle a tool present in the dispatch table."""
        raw_path = tool_input.get(path_key)
        if raw_path is None:
            logger.debug(
                "workspace-boundary: %s has no %r key — continuing", tool_name, path_key
            )
            return HookResult(action="continue")

        # check_path accessed via module ref so tests can monkeypatch it.
        result = _boundary_mod.check_path(str(raw_path), operation, boundary_config)
        resolved = result.resolved_path or str(raw_path)

        # Emit observability event BEFORE returning (deny short-circuits remaining hooks).
        if result.event:
            _try_emit(
                coordinator,
                result.event,
                {
                    "tool_name": tool_name,
                    "raw_path": str(raw_path),
                    "resolved_path": resolved,
                    "boundary": boundary_config.workspace_root,
                    "operation": operation,
                    "allowlist_rule": result.allowlist_rule,
                },
            )

        if result.allowed:
            logger.debug(
                "workspace-boundary: ALLOW %s %s=%s", tool_name, path_key, resolved
            )
            return HookResult(action="continue")

        logger.info(
            "workspace-boundary: DENY %s %s=%s (mode=%s)",
            tool_name,
            path_key,
            resolved,
            boundary_config.enforcement_mode,
        )

        mode = boundary_config.enforcement_mode
        if mode == "enforce":
            return _deny(
                resolved, result.reason or "[WorkspaceBoundary] path outside boundary"
            )
        if mode == "warn":
            msg = (
                f"path outside workspace boundary: {resolved} "
                f"(boundary: {boundary_config.workspace_root})"
            )
            return _warn(resolved, msg)
        # audit_only
        return HookResult(action="continue")

    # ------------------------------------------------------------------
    # Bash handler
    # ------------------------------------------------------------------

    async def _handle_bash(tool_input: dict) -> HookResult:
        """Handle the bash tool via static path extraction and ambiguity detection."""
        command = tool_input.get("command", "")

        paths = extract_absolute_paths(command)
        ambiguous = detect_ambiguous_patterns(command)

        # Check each extracted absolute path token.
        for raw_path in paths:
            result = _boundary_mod.check_path(raw_path, "exec", boundary_config)
            resolved = result.resolved_path or raw_path

            if result.event:
                _try_emit(
                    coordinator,
                    result.event,
                    {
                        "tool_name": "bash",
                        "raw_path": raw_path,
                        "resolved_path": resolved,
                        "boundary": boundary_config.workspace_root,
                        "operation": "exec",
                    },
                )

            if not result.allowed:
                logger.info("workspace-boundary: DENY bash path=%s", resolved)
                mode = boundary_config.enforcement_mode
                if mode == "enforce":
                    return _deny(
                        resolved,
                        result.reason or "[WorkspaceBoundary] path outside boundary",
                    )
                if mode == "warn":
                    return _warn(
                        resolved, f"bash accessing path outside boundary: {resolved}"
                    )
                # audit_only: fall through to continue

        # Surface ambiguous patterns as warnings regardless of enforcement mode.
        if ambiguous:
            descriptions = [desc for _, desc in ambiguous]
            msg = (
                "Bash command contains patterns that defeat static analysis: "
                + ", ".join(descriptions)
            )
            _try_emit(
                coordinator,
                "workspace_boundary:bash_warning",
                {"command_fragment": command[:200], "reason": msg},
            )

            if (
                boundary_config.bash_strict_mode
                and boundary_config.enforcement_mode == "enforce"
            ):
                logger.info(
                    "workspace-boundary: DENY bash (strict_mode, ambiguous patterns)"
                )
                return HookResult(
                    action="deny",
                    reason=f"[WorkspaceBoundary] {msg}",
                    user_message=msg,
                    user_message_level="error",
                )
            # Default: continue but surface user_message to the human operator.
            logger.debug("workspace-boundary: bash ambiguity warning: %s", msg)
            return HookResult(
                action="continue",
                user_message=msg,
                user_message_level="warning",
            )

        return HookResult(action="continue")

    # ------------------------------------------------------------------
    # tool:pre handler — enforcement point
    # ------------------------------------------------------------------

    async def pre_handler(event_name: str, data: dict) -> HookResult:
        """Intercept tool:pre events and enforce workspace boundary."""
        try:
            tool_name: str = data.get("tool_name", "")
            tool_input: dict = data.get("tool_input", {})

            # Bash: static analysis path.
            if tool_name == "bash":
                return await _handle_bash(tool_input)

            # Known tools: dispatch table lookup.
            dispatch = boundary_config.tool_dispatch
            if tool_name in dispatch:
                info = dispatch[tool_name]
                return await _handle_known_tool(
                    tool_name,
                    tool_input,
                    info["path_key"],
                    info["operation"],
                )

            # Unknown tool.
            logger.debug("workspace-boundary: unknown tool %r", tool_name)
            _try_emit(
                coordinator,
                "workspace_boundary:unknown_tool",
                {"tool_name": tool_name},
            )

            if (
                boundary_config.enforcement_mode == "enforce"
                and boundary_config.strict_unknown_tools
            ):
                return HookResult(
                    action="deny",
                    reason=(
                        f"[WorkspaceBoundary] Unknown tool '{tool_name}' denied "
                        f"(strict_unknown_tools=True)"
                    ),
                    user_message=f"Unknown tool blocked by workspace boundary: {tool_name}",
                    user_message_level="error",
                )

            return HookResult(action="continue")

        except Exception as exc:
            # FAIL CLOSED — security hooks must not fail open.
            logger.exception(
                "workspace-boundary: exception in pre_handler — failing closed"
            )
            return HookResult(
                action="deny",
                reason=f"boundary check error — failing closed: {exc}",
                user_message="Workspace boundary check failed (internal error) — access denied",
                user_message_level="error",
            )

    # ------------------------------------------------------------------
    # tool:post handler — audit logging
    # ------------------------------------------------------------------

    async def post_handler(event_name: str, data: dict) -> HookResult:
        """Record audit trail of tools that completed execution."""
        try:
            tool_name: str = data.get("tool_name", "")
            tool_input: dict = data.get("tool_input", {})
            # Best-effort path extraction for audit payload.
            resolved_path = ""
            if tool_name in boundary_config.tool_dispatch:
                path_key = boundary_config.tool_dispatch[tool_name]["path_key"]
                resolved_path = str(tool_input.get(path_key, ""))
            logger.debug("workspace-boundary audit: tool=%s completed", tool_name)
            _try_emit(
                coordinator,
                "workspace_boundary:path_allowed",
                {"tool_name": tool_name, "resolved_path": resolved_path},
            )
        except Exception:
            logger.debug(
                "workspace-boundary: audit handler error (non-critical, ignoring)"
            )
        return HookResult(action="continue")

    # ------------------------------------------------------------------
    # Register handlers
    # ------------------------------------------------------------------

    unregister_pre = coordinator.hooks.register(
        TOOL_PRE, pre_handler, priority=5, name="workspace-boundary"
    )
    unregister_post = coordinator.hooks.register(
        TOOL_POST, post_handler, priority=50, name="workspace-boundary-audit"
    )

    # Declare observable events for hooks-logging and other consumers.
    coordinator.register_contributor(
        "observability.events",
        "hooks-workspace-boundary",
        lambda: [
            "workspace_boundary:path_denied",
            "workspace_boundary:path_allowed",
            "workspace_boundary:allowlisted",
            "workspace_boundary:bash_warning",
            "workspace_boundary:unknown_tool",
        ],
    )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup() -> None:
        """Unregister both hook handlers."""
        unregister_pre()
        unregister_post()
        logger.info("workspace-boundary cleanup: handlers unregistered")

    return cleanup
