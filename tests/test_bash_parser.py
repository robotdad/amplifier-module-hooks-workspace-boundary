"""Tests for bash_parser.py — static bash command analysis."""

from __future__ import annotations

import pytest

from amplifier_module_hooks_workspace_boundary.bash_parser import (
    detect_ambiguous_patterns,
    extract_absolute_paths,
)


# ---------------------------------------------------------------------------
# extract_absolute_paths
# ---------------------------------------------------------------------------


class TestExtractAbsolutePaths:
    def test_single_absolute_path(self) -> None:
        paths = extract_absolute_paths("cat /etc/hosts")
        assert "/etc/hosts" in paths

    def test_multiple_absolute_paths(self) -> None:
        paths = extract_absolute_paths("cp /src/file.txt /dst/output.txt")
        assert "/src/file.txt" in paths
        assert "/dst/output.txt" in paths

    def test_nested_path(self) -> None:
        paths = extract_absolute_paths("ls /home/user/Work/project/src")
        assert "/home/user/Work/project/src" in paths

    def test_no_absolute_paths(self) -> None:
        paths = extract_absolute_paths("ls -la .")
        assert paths == []

    def test_path_stops_at_semicolon(self) -> None:
        paths = extract_absolute_paths("cat /tmp/foo; rm /tmp/bar")
        assert "/tmp/foo" in paths
        assert "/tmp/bar" in paths
        for p in paths:
            assert ";" not in p

    def test_path_stops_at_pipe(self) -> None:
        paths = extract_absolute_paths("cat /tmp/foo | grep pattern")
        assert "/tmp/foo" in paths
        for p in paths:
            assert "|" not in p

    def test_path_stops_at_ampersand(self) -> None:
        paths = extract_absolute_paths("cat /tmp/foo && echo done")
        assert "/tmp/foo" in paths
        for p in paths:
            assert "&" not in p

    def test_path_stops_at_redirect(self) -> None:
        paths = extract_absolute_paths("cat /tmp/input > /tmp/output")
        assert "/tmp/input" in paths
        assert "/tmp/output" in paths
        for p in paths:
            assert ">" not in p

    def test_relative_paths_not_extracted(self) -> None:
        paths = extract_absolute_paths("cat ./relative/path.txt ../sibling.txt")
        assert all(p.startswith("/") for p in paths)

    def test_empty_command(self) -> None:
        assert extract_absolute_paths("") == []

    def test_no_flags_extracted(self) -> None:
        """Flags like -la should not appear as absolute paths."""
        paths = extract_absolute_paths("ls -la /tmp")
        assert all(not p.startswith("-") for p in paths)

    def test_command_with_only_options(self) -> None:
        paths = extract_absolute_paths("git status --short")
        assert paths == []

    def test_path_with_extension(self) -> None:
        paths = extract_absolute_paths("python /workspace/script.py")
        assert "/workspace/script.py" in paths

    def test_path_in_double_quotes_partial(self) -> None:
        # Quotes themselves are stripped by the regex boundary; path inside is extracted.
        paths = extract_absolute_paths('cat "/tmp/my-file.txt"')
        assert any("/tmp/my-file.txt" in p for p in paths)

    def test_dev_null(self) -> None:
        paths = extract_absolute_paths("cat /dev/null")
        assert "/dev/null" in paths

    def test_multiple_commands_on_one_line(self) -> None:
        paths = extract_absolute_paths("mkdir /workspace/build; cp /src/main.py /workspace/build/")
        # Should find all three path references
        assert any("/workspace/build" in p for p in paths)
        assert any("/src/main.py" in p for p in paths)


# ---------------------------------------------------------------------------
# detect_ambiguous_patterns
# ---------------------------------------------------------------------------


class TestDetectAmbiguousPatterns:
    def test_detects_subshell_dollar_paren(self) -> None:
        result = detect_ambiguous_patterns("cat $(find / -name secret)")
        assert len(result) > 0
        descriptions = [desc for _, desc in result]
        assert any("subshell" in d for d in descriptions)

    def test_detects_backticks(self) -> None:
        result = detect_ambiguous_patterns("cat `echo /outside/path`")
        assert len(result) > 0
        descriptions = [desc for _, desc in result]
        assert any("backtick" in d for d in descriptions)

    def test_detects_variable_expansion(self) -> None:
        result = detect_ambiguous_patterns("cat ${MY_PATH}/file")
        assert len(result) > 0
        descriptions = [desc for _, desc in result]
        assert any("variable" in d for d in descriptions)

    def test_detects_eval(self) -> None:
        result = detect_ambiguous_patterns("eval 'cat /outside/path'")
        assert len(result) > 0
        descriptions = [desc for _, desc in result]
        assert any("eval" in d for d in descriptions)

    def test_detects_source(self) -> None:
        result = detect_ambiguous_patterns("source /outside/script.sh")
        assert len(result) > 0
        descriptions = [desc for _, desc in result]
        assert any("source" in d for d in descriptions)

    def test_detects_exec(self) -> None:
        result = detect_ambiguous_patterns("exec /outside/binary")
        assert len(result) > 0
        descriptions = [desc for _, desc in result]
        assert any("exec" in d for d in descriptions)

    def test_no_false_positives_ls(self) -> None:
        result = detect_ambiguous_patterns("ls -la /tmp")
        assert result == []

    def test_no_false_positives_git(self) -> None:
        result = detect_ambiguous_patterns("git status && git add .")
        assert result == []

    def test_no_false_positives_echo(self) -> None:
        result = detect_ambiguous_patterns("echo hello world")
        assert result == []

    def test_no_false_positives_python(self) -> None:
        result = detect_ambiguous_patterns("python /workspace/script.py --verbose")
        assert result == []

    def test_multiple_patterns_detected(self) -> None:
        """Both eval and $() in the same command produce multiple entries."""
        result = detect_ambiguous_patterns("eval $(cat /etc/passwd)")
        assert len(result) >= 2

    def test_returns_list_of_tuples(self) -> None:
        result = detect_ambiguous_patterns("cat $(find /)")
        assert isinstance(result, list)
        for item in result:
            assert len(item) == 2
            pattern, desc = item
            assert isinstance(pattern, str)
            assert isinstance(desc, str)

    def test_empty_command_no_ambiguity(self) -> None:
        assert detect_ambiguous_patterns("") == []
