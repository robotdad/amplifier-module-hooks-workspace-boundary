"""Static analysis of bash command strings for workspace-boundary enforcement.

Strategy: static regex extraction of absolute path tokens (DESIGN.md §6, strategy (a)).
This catches the obvious cases. Dynamic constructs are flagged with warnings.

Documented bypass vectors (cannot be blocked without OS-level sandboxing):
  - CMD=/outside/path; cat $CMD
  - $(find / -name secret)
  - eval "cat /outside/path"
  - source /outside/script.sh

Posture: "Block the obvious, warn on ambiguous."
"""

from __future__ import annotations

import re

# Matches absolute path tokens starting with '/' that are NOT preceded
# by a dot or word character — this prevents extracting /path from relative
# tokens like ./path or word/path.
# Uses a negative lookbehind (?<![.\w]) so that /setup.sh in ./setup.sh
# is not extracted as an absolute path.
# Stops at whitespace and shell metacharacters to avoid eating operators.
_ABSOLUTE_PATH_RE = re.compile(r"(?<![.\w])(/[^\s;|&><`'\"()\[\]{}\\]+)")

# Patterns that defeat static path analysis, paired with human-readable descriptions.
# Order matters: more specific patterns before general ones.
_AMBIGUOUS_PATTERNS: list[tuple[str, str]] = [
    (r"\$\(", "subshell substitution $(...)"),
    (r"`[^`\n]", "backtick substitution"),
    (r"\$\{", "variable expansion ${...}"),
    (r"\beval\b", "eval command"),
    (r"\bsource\b", "source command"),
    (r"\bexec\b", "exec command"),
]


def extract_absolute_paths(command: str) -> list[str]:
    """Extract absolute path tokens from a bash command string via regex.

    Will miss dynamically constructed paths (variable expansion, subshell
    substitution, etc.). Use :func:`detect_ambiguous_patterns` to surface
    those cases as warnings.

    Args:
        command: Bash command string to analyze.

    Returns:
        List of absolute path strings found (may include flags like ``/dev/null``
        or tool paths like ``/usr/bin/cat`` — callers should run them through
        the boundary checker which will allowlist system paths).
    """
    return _ABSOLUTE_PATH_RE.findall(command)


def detect_ambiguous_patterns(command: str) -> list[tuple[str, str]]:
    """Detect shell patterns in a command that defeat static path analysis.

    These constructs can be used to access out-of-boundary paths in ways the
    regex extractor in :func:`extract_absolute_paths` cannot see.

    Args:
        command: Bash command string to analyze.

    Returns:
        List of ``(pattern, description)`` tuples for each matched ambiguity.
        Empty list if the command is statically clean.
    """
    found: list[tuple[str, str]] = []
    for pattern, description in _AMBIGUOUS_PATTERNS:
        if re.search(pattern, command):
            found.append((pattern, description))
    return found
