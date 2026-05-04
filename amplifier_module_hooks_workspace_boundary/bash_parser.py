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

import os
import re

# Matches absolute path tokens starting with '/' that are NOT preceded
# by a dot, tilde, or word character — this prevents extracting /path from
# relative tokens like ./path, ~/path, or word/path.
# Uses a negative lookbehind (?<![.~\w]) so that /setup.sh in ./setup.sh
# and /Work/project in ~/Work/project are not extracted as absolute paths.
# Stops at whitespace and shell metacharacters to avoid eating operators.
_ABSOLUTE_PATH_RE = re.compile(r"(?<![.~\w])(/[^\s;|&><`'\"()\[\]{}\\]+)")

# Matches tilde-prefixed path tokens (~/...).  The shell expands these to
# absolute paths at runtime.  Extracted separately and expanded via
# os.path.expanduser() so they are checked against the workspace boundary.
# Without this, tilde paths bypass the boundary entirely because the
# negative lookbehind in _ABSOLUTE_PATH_RE excludes them from extraction.
# The lookbehind also excludes ':' to skip remote host paths like
# user@host:~/path used by scp/rsync.
_TILDE_PATH_RE = re.compile(r"(?<![\w:])(~(?:/[^\s;|&><`'\"()\[\]{}\\]+))")

# Matches URL tokens (scheme://...) so they can be stripped before path
# extraction.  Covers http, https, ftp, ssh, git, file, and any other
# <scheme>:// prefix.  Consumes until whitespace or shell metacharacter.
_URL_RE = re.compile(r"\w+://[^\s;|&><`'\"()\[\]{}\\]*")

# Detects container runtime exec commands.  When one of these precedes a
# ``--`` separator, everything after ``--`` is a command running *inside*
# the container and its paths are not host filesystem paths.
_CONTAINER_EXEC_RE = re.compile(
    r"\b(?:docker|podman|nerdctl|incus|lxc|kubectl|amplifier-digital-twin)\s+exec\b"
)

# Strips the content of message-flag arguments (e.g. ``git commit -m "..."``).
# These flags take a text string, not a filesystem path.  Without this
# pre-filter, paths mentioned *inside* commit messages, tag annotations,
# etc. are extracted as candidates and trigger false boundary violations.
#
# Matches: -m 'msg', -am "msg", --message="msg", --message 'msg'
# The flag letter class [-a-z]*m covers compound short flags like -am, -sm.
_MSG_FLAG_RE = re.compile(r"""(?:-[a-z]*m|--message)\s*=?\s*(?:'[^']*'|"[^"]*")""")

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


def _strip_container_internal(command: str) -> str:
    """Remove container-internal arguments from exec commands.

    When a container runtime ``exec`` is detected, everything after the
    ``--`` separator is a command running *inside* the container.  Those
    paths are container-internal and must not be treated as host
    filesystem paths.

    Only the portion of the command string after ``--`` (following the
    matched exec keyword) is removed.  Any host-side commands chained
    *before* the exec via ``&&``, ``||``, ``;``, or ``|`` are preserved
    so their paths are still checked.
    """
    match = _CONTAINER_EXEC_RE.search(command)
    if not match:
        return command

    # Look for ' -- ' after the exec keyword.
    separator_idx = command.find(" -- ", match.end())
    if separator_idx < 0:
        return command

    # Keep everything up to (not including) the separator.
    return command[:separator_idx]


def extract_absolute_paths(command: str) -> list[str]:
    """Extract absolute path tokens from a bash command string via regex.

    Three pre-filters run before path extraction:

    1. **Container exec** — when a container runtime ``exec`` command is
       detected (docker, podman, incus, kubectl, amplifier-digital-twin,
       etc.), everything after the ``--`` separator is stripped.  Those
       paths live inside the container, not on the host filesystem.
    2. **URLs** — ``scheme://...`` tokens are replaced with whitespace so
       URL path components are not mistaken for filesystem paths.
    3. **Message flags** — ``-m "..."`` / ``--message="..."`` arguments
       are replaced with whitespace so paths inside commit messages, tag
       annotations, etc. are not mistaken for filesystem targets.

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
    # 1. Strip container-internal arguments (paths after '--' in exec).
    sanitized = _strip_container_internal(command)
    # 2. Replace URLs with whitespace so their path components are not extracted.
    sanitized = _URL_RE.sub(" ", sanitized)
    # 3. Replace message-flag arguments with whitespace so paths inside
    #    commit messages, tag annotations, etc. are not extracted.
    sanitized = _MSG_FLAG_RE.sub(" ", sanitized)
    # 4. Extract candidate absolute paths, then discard glob patterns.
    #    Real absolute paths never contain '*' or '?'.  These characters
    #    appear in flag arguments like ``find -path '*/foo/*'`` or
    #    ``grep --include='*.py'`` and are not filesystem targets.
    candidates = _ABSOLUTE_PATH_RE.findall(sanitized)
    absolute = [p for p in candidates if "*" not in p and "?" not in p]
    # 5. Extract tilde-prefixed paths and expand to absolute paths.
    #    Without this step, ~/Work/topologies bypasses the boundary entirely
    #    because the negative lookbehind in _ABSOLUTE_PATH_RE excludes them.
    tilde_candidates = _TILDE_PATH_RE.findall(sanitized)
    for tp in tilde_candidates:
        expanded = os.path.expanduser(tp)
        if expanded != tp and "*" not in expanded and "?" not in expanded:
            absolute.append(expanded)
    return absolute


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
