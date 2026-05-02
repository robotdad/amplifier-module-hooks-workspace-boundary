"""Tests for bash_parser.py — static bash command analysis."""

from __future__ import annotations


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
        paths = extract_absolute_paths(
            "mkdir /workspace/build; cp /src/main.py /workspace/build/"
        )
        # Should find all three path references
        assert any("/workspace/build" in p for p in paths)
        assert any("/src/main.py" in p for p in paths)


# ---------------------------------------------------------------------------
# URL false-positive prevention (filesystem hook, not network hook)
# ---------------------------------------------------------------------------


class TestURLsNotExtracted:
    """URLs must not be mistaken for filesystem paths.

    The workspace boundary hook enforces filesystem access.  Network
    locations (``http://``, ``https://``, ``ftp://``, etc.) are not
    filesystem paths and must never be fed to the boundary checker.
    """

    def test_http_url_not_extracted(self) -> None:
        paths = extract_absolute_paths("curl http://localhost:3000/api/health")
        assert paths == []

    def test_https_url_not_extracted(self) -> None:
        paths = extract_absolute_paths("curl https://example.com/admin/pages")
        assert paths == []

    def test_url_with_ip_and_port(self) -> None:
        paths = extract_absolute_paths(
            "agent-browser open http://10.191.237.217:3000/admin/pages"
        )
        assert paths == []

    def test_ftp_url_not_extracted(self) -> None:
        paths = extract_absolute_paths(
            "wget ftp://mirror.example.com/pub/release.tar.gz"
        )
        assert paths == []

    def test_file_url_not_extracted(self) -> None:
        paths = extract_absolute_paths("xdg-open file:///home/user/doc.pdf")
        assert paths == []

    def test_git_ssh_url_not_extracted(self) -> None:
        paths = extract_absolute_paths(
            "git clone git+https://github.com/microsoft/amplifier@main"
        )
        assert paths == []

    def test_url_mixed_with_real_path(self) -> None:
        """A command with both a URL and a real filesystem path."""
        paths = extract_absolute_paths(
            "curl http://localhost:3000/api/data -o /tmp/output.json"
        )
        assert "/tmp/output.json" in paths
        assert len(paths) == 1

    def test_multiple_urls_stripped(self) -> None:
        paths = extract_absolute_paths("curl http://host1/path1 http://host2/path2")
        assert paths == []

    def test_url_in_assignment(self) -> None:
        paths = extract_absolute_paths('URL="http://10.191.237.217:3000/"')
        assert paths == []

    def test_url_in_redirect(self) -> None:
        """curl with URL and redirect to /dev/null — only /dev/null extracted."""
        paths = extract_absolute_paths("curl http://localhost:3000/health 2>/dev/null")
        assert "/dev/null" in paths
        # The URL path component should NOT appear
        assert not any("health" in p for p in paths)
        assert not any("localhost" in p for p in paths)


# ---------------------------------------------------------------------------
# Container exec — paths after '--' are container-internal
# ---------------------------------------------------------------------------


class TestContainerExecNotExtracted:
    """Paths after '--' in container exec commands are container-internal.

    The workspace boundary hook enforces *host* filesystem access.  When
    a container runtime ``exec`` command is detected, everything after
    the ``--`` separator runs inside the container — those paths must not
    be treated as host paths.
    """

    def test_docker_exec(self) -> None:
        paths = extract_absolute_paths("docker exec mycontainer -- cat /etc/hosts")
        assert paths == []

    def test_podman_exec(self) -> None:
        paths = extract_absolute_paths("podman exec mycontainer -- ls /var/log")
        assert paths == []

    def test_incus_exec(self) -> None:
        paths = extract_absolute_paths(
            "incus exec mycontainer -- bash -lc 'cd /app && node server.js'"
        )
        assert paths == []

    def test_kubectl_exec(self) -> None:
        paths = extract_absolute_paths(
            "kubectl exec mypod -- cat /etc/config/settings.yaml"
        )
        assert paths == []

    def test_amplifier_digital_twin_exec(self) -> None:
        paths = extract_absolute_paths(
            "amplifier-digital-twin exec dtu-35c496a0 -- bash -lc "
            "'cd /app && node scripts/populate-persona-api.js sarah-chen'"
        )
        assert paths == []

    def test_host_path_before_exec_still_checked(self) -> None:
        """Host-side paths chained before the exec must still be extracted."""
        paths = extract_absolute_paths(
            "cat /workspace/config.json && docker exec c -- cat /etc/hosts"
        )
        assert "/workspace/config.json" in paths
        # Container-internal path must not appear
        assert "/etc/hosts" not in paths

    def test_exec_without_separator_not_stripped(self) -> None:
        """Without '--', we can't tell where container args begin — keep all."""
        paths = extract_absolute_paths("docker exec mycontainer cat /etc/hosts")
        # Without '--' we conservatively keep the path
        assert "/etc/hosts" in paths

    def test_nerdctl_exec(self) -> None:
        paths = extract_absolute_paths("nerdctl exec builder -- make -C /build install")
        assert paths == []

    def test_lxc_exec(self) -> None:
        paths = extract_absolute_paths("lxc exec mycontainer -- ls /root")
        assert paths == []

    def test_exec_with_flags(self) -> None:
        """Docker exec with -it flags before the container name."""
        paths = extract_absolute_paths(
            "docker exec -it mycontainer -- bash -c 'ls /var/log'"
        )
        assert paths == []

    def test_exec_combined_with_url(self) -> None:
        """Container exec + URL — both filters must apply."""
        paths = extract_absolute_paths(
            "amplifier-digital-twin exec dtu-abc -- "
            "curl http://localhost:3000/api/health"
        )
        assert paths == []


# ---------------------------------------------------------------------------
# Glob pattern false-positive prevention
# ---------------------------------------------------------------------------


class TestGlobPatternsNotExtracted:
    """Glob patterns in flag arguments must not be treated as filesystem paths.

    Commands like ``find -path``, ``grep --include``, and ``rsync --exclude``
    accept glob patterns containing ``*`` and ``?``.  These are never real
    absolute filesystem paths and must be filtered out.
    """

    def test_find_path_glob_not_extracted(self) -> None:
        """The original false-positive: find -path '*/validation/*d5d*'."""
        paths = extract_absolute_paths(
            "find /home/user/Work/feedback -path '*/validation/*d5d*' "
            "-o -path '*/validation/*D5d*'"
        )
        assert "/home/user/Work/feedback" in paths
        assert len(paths) == 1

    def test_find_name_glob_not_extracted(self) -> None:
        paths = extract_absolute_paths("find /workspace -name '*.py'")
        assert "/workspace" in paths
        assert not any("*.py" in p for p in paths)

    def test_grep_include_glob_not_extracted(self) -> None:
        paths = extract_absolute_paths("grep --include='*.ts' pattern /src/app")
        assert "/src/app" in paths
        assert not any("*" in p for p in paths)

    def test_rsync_exclude_glob_not_extracted(self) -> None:
        paths = extract_absolute_paths("rsync -av /src/ /dst/ --exclude='*.log'")
        assert "/src/" in paths
        assert "/dst/" in paths
        assert not any("*" in p for p in paths)

    def test_question_mark_glob_not_extracted(self) -> None:
        paths = extract_absolute_paths("find /tmp -name 'file?.txt'")
        assert "/tmp" in paths
        assert not any("?" in p for p in paths)

    def test_real_paths_still_extracted(self) -> None:
        """Non-glob absolute paths must still be extracted normally."""
        paths = extract_absolute_paths(
            "find /workspace/src -path '*/test/*' -exec cat {} +"
        )
        assert "/workspace/src" in paths
        assert not any("*" in p for p in paths)


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
