"""Test configuration and shared fixtures for hooks-workspace-boundary.

Sets up a mock ``amplifier_core`` module in ``sys.modules`` when the real
library is not installed, so the full test suite can run with only pytest
and pytest-asyncio as dependencies.

Also provides a ``MockCoordinator`` fixture that records hook registrations
and contributor declarations for assertion in tests.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# amplifier_core mock
# ---------------------------------------------------------------------------


def _setup_amplifier_core_mock() -> None:
    """Inject a mock ``amplifier_core`` into ``sys.modules`` when absent.

    Must run at conftest import time (before any test module imports the
    package under test) so that deferred imports inside ``mount()`` resolve
    to our mock.
    """
    if "amplifier_core" in sys.modules:
        return  # Already present — real or previously mocked.

    try:
        import amplifier_core  # noqa: F401

        return  # Real library is installed.
    except ImportError:
        pass

    # Build a minimal HookResult that matches the real pydantic model's fields.
    @dataclass
    class HookResult:
        """Mock HookResult matching the amplifier_core contract."""

        action: str = "continue"
        reason: str | None = None
        context_injection: str | None = None
        context_injection_role: str = "system"
        ephemeral: bool = False
        user_message: str | None = None
        user_message_level: str = "info"

    TOOL_PRE = "tool:pre"
    TOOL_POST = "tool:post"

    mock_events = MagicMock()
    mock_events.TOOL_PRE = TOOL_PRE
    mock_events.TOOL_POST = TOOL_POST

    mock_core = MagicMock()
    mock_core.HookResult = HookResult
    mock_core.events = mock_events

    sys.modules["amplifier_core"] = mock_core
    sys.modules["amplifier_core.events"] = mock_events


# Run immediately at conftest import time.
_setup_amplifier_core_mock()


# ---------------------------------------------------------------------------
# MockCoordinator
# ---------------------------------------------------------------------------


@dataclass
class _HooksRegistry:
    """Records hook registrations and supports un-registration."""

    registrations: list[tuple] = field(default_factory=list)

    def register(
        self, event: str, handler: Any, priority: int = 10, name: str = ""
    ) -> Any:
        """Register a handler and return a callable that removes it."""
        entry = (event, handler, priority, name)
        self.registrations.append(entry)

        def _unregister() -> None:
            try:
                self.registrations.remove(entry)
            except ValueError:
                pass  # Already removed — idempotent.

        return _unregister


class MockCoordinator:
    """Minimal Amplifier coordinator mock for hook module testing.

    Tracks:
    - ``hooks.registrations`` — list of (event, handler, priority, name) tuples
    - ``contributors``        — list of (namespace, name, fn) tuples
    - ``emitted_events``      — list of (event_name, payload) tuples
    """

    def __init__(self) -> None:
        self.hooks = _HooksRegistry()
        self.contributors: list[tuple[str, str, Any]] = []
        self.emitted_events: list[tuple[str, dict]] = []

    def register_contributor(self, namespace: str, name: str, fn: Any) -> None:
        """Record a contributor registration."""
        self.contributors.append((namespace, name, fn))

    def emit(self, event_name: str, payload: dict) -> None:
        """Record an emitted observability event."""
        self.emitted_events.append((event_name, payload))

    # ------------------------------------------------------------------
    # Helpers for test assertions
    # ------------------------------------------------------------------

    def get_handler(
        self,
        event: str | None = None,
        priority: int | None = None,
        name: str | None = None,
    ) -> Any:
        """Retrieve the first registered handler matching the given filters."""
        for ev, handler, pri, nm in self.hooks.registrations:
            if event is not None and ev != event:
                continue
            if priority is not None and pri != priority:
                continue
            if name is not None and nm != name:
                continue
            return handler
        return None

    def emitted(self, event_name: str) -> list[dict]:
        """Return payloads for all emissions of a given event name."""
        return [payload for ev, payload in self.emitted_events if ev == event_name]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def coordinator() -> MockCoordinator:
    """Fresh MockCoordinator for each test."""
    return MockCoordinator()
