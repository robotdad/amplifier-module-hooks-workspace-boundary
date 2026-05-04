"""Tests for bash_parser.py — static bash command analysis."""

from __future__ import annotations

import os

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
# Colon-separated path false-positive prevention (Docker -v, PATH, etc.)
# ---------------------------------------------------------------------------


class TestColonSeparatedNotMerged:
    """Colon-separated constructs must not merge into a single path token.

    Docker bind mounts (``-v /host:/container:ro``), PATH variables
    (``PATH=/usr/bin:/usr/local/bin``), and similar colon-separated strings
    must be split at the colon so each component is checked independently.
    """

    def test_docker_bind_mount_splits(self) -> None:
        """Docker -v host:container:options must extract host and container separately."""
        paths = extract_absolute_paths(
            "docker run -v /home/user/bin/gh:/usr/local/bin/gh:ro myimage"
        )
        assert "/home/user/bin/gh" in paths
        assert "/usr/local/bin/gh" in paths
        # The merged form must NOT appear
        assert not any(":" in p for p in paths)

    def test_docker_bind_mount_config_dir(self) -> None:
        paths = extract_absolute_paths(
            "docker run -v /home/user/.config/gh:/home/user/.config/gh:ro img"
        )
        assert "/home/user/.config/gh" in paths
        assert not any(":" in p for p in paths)

    def test_path_variable_splits(self) -> None:
        """PATH=/usr/bin:/usr/local/bin must extract both components."""
        paths = extract_absolute_paths(
            "export PATH=/usr/bin:/usr/local/bin:/home/user/bin"
        )
        assert "/usr/bin" in paths
        assert "/usr/local/bin" in paths
        assert "/home/user/bin" in paths
        assert not any(":" in p for p in paths)

    def test_python_string_with_bind_mount(self) -> None:
        """Python code generating bind mount specs — the original incident."""
        paths = extract_absolute_paths(
            """python3 -c "mount = '/host/path:/container/path:ro'" """
        )
        # Each path component is extracted separately (after quote stripping)
        assert not any(":" in p for p in paths)

    def test_classpath_splits(self) -> None:
        paths = extract_absolute_paths(
            "java -cp /opt/lib/a.jar:/opt/lib/b.jar Main"
        )
        assert "/opt/lib/a.jar" in paths
        assert "/opt/lib/b.jar" in paths
        assert not any(":" in p for p in paths)

    def test_normal_paths_unaffected(self) -> None:
        """Paths without colons must still be extracted normally."""
        paths = extract_absolute_paths("cat /etc/hosts /tmp/data.txt")
        assert "/etc/hosts" in paths
        assert "/tmp/data.txt" in paths


# ---------------------------------------------------------------------------
# Root path false-positive prevention (jq //, empty strings)
# ---------------------------------------------------------------------------


class TestRootPathNotExtracted:
    """Bare root path '/' or '//' must not be extracted as a candidate.

    The jq null-coalescing operator ``//`` and escaped empty string
    literals can produce '/' or '//' tokens that normalize to the root
    directory, causing false boundary denials on every workspace.
    """

    def test_jq_double_slash_not_extracted(self) -> None:
        """jq's // null-coalescing operator must not yield a path."""
        paths = extract_absolute_paths(
            """jq '.data // null' /tmp/input.json"""
        )
        assert "/tmp/input.json" in paths
        assert "/" not in paths
        assert "//" not in paths

    def test_bare_slash_not_extracted(self) -> None:
        paths = extract_absolute_paths("echo /")
        assert paths == []

    def test_double_slash_not_extracted(self) -> None:
        paths = extract_absolute_paths("echo //")
        assert paths == []

    def test_trailing_slash_path_still_extracted(self) -> None:
        """Paths with trailing slashes are fine — only bare / is filtered."""
        paths = extract_absolute_paths("ls /tmp/")
        assert "/tmp/" in paths


# ---------------------------------------------------------------------------
# Message-flag false-positive prevention (git commit -m, etc.)
# ---------------------------------------------------------------------------


class TestMessageFlagContentNotExtracted:
    """Paths inside message-flag arguments must not be extracted.

    Commands like ``git commit -m "fix /outside/path"`` pass a text
    string, not a filesystem path.  The message-flag pre-filter strips
    the content of ``-m``, ``--message``, and compound flags like
    ``-am`` so that path-like substrings inside messages don't trigger
    false boundary violations.
    """

    def test_git_commit_m_single_quotes(self) -> None:
        paths = extract_absolute_paths("git commit -m 'fix: handle /outside/path'")
        assert paths == []

    def test_git_commit_m_double_quotes(self) -> None:
        paths = extract_absolute_paths('git commit -m "fix: handle /outside/path"')
        assert paths == []

    def test_git_commit_am_with_message(self) -> None:
        paths = extract_absolute_paths('git commit -am "refactor /src/old/module"')
        assert paths == []

    def test_git_commit_message_long_flag(self) -> None:
        paths = extract_absolute_paths(
            'git commit --message="chore: bump /etc/config version"'
        )
        assert paths == []

    def test_git_commit_message_long_flag_space(self) -> None:
        paths = extract_absolute_paths(
            "git commit --message 'chore: bump /etc/config version'"
        )
        assert paths == []

    def test_git_tag_m(self) -> None:
        paths = extract_absolute_paths('git tag -m "release /v1.0" v1.0')
        assert paths == []

    def test_git_tag_am(self) -> None:
        paths = extract_absolute_paths('git tag -am "annotated /release/notes" v2.0')
        assert paths == []

    def test_message_flag_with_real_path_before(self) -> None:
        """Real paths outside the message must still be extracted."""
        paths = extract_absolute_paths(
            'cat /workspace/CHANGELOG.md && git commit -m "update /changelog"'
        )
        assert "/workspace/CHANGELOG.md" in paths
        assert not any("changelog" in p for p in paths)

    def test_message_flag_with_real_path_after(self) -> None:
        """Real paths after the message flag must still be extracted."""
        paths = extract_absolute_paths(
            'git commit -m "fix /outside/path" -- /workspace/file.py'
        )
        assert "/workspace/file.py" in paths
        assert not any("outside" in p for p in paths)

    def test_non_git_m_flag_with_message(self) -> None:
        """The -m filter is not git-specific — any -m 'quoted' is stripped."""
        paths = extract_absolute_paths("sometool -m 'note about /var/log' /tmp/out")
        assert "/tmp/out" in paths
        assert not any("var" in p for p in paths)

    def test_unquoted_m_flag_not_stripped(self) -> None:
        """Unquoted -m arguments are NOT stripped — only quoted content."""
        paths = extract_absolute_paths("git commit -m /tmp/issue")
        # The unquoted arg is not matched by the regex, so /tmp/issue
        # is extracted normally (conservative: may be a real path).
        assert "/tmp/issue" in paths


# ---------------------------------------------------------------------------
# Tilde path false-positive prevention
# ---------------------------------------------------------------------------


class TestTildePathsExtracted:
    """Tilde-prefixed paths (~/...) must be expanded and extracted.

    The shell expands ``~/path`` to ``/home/user/path`` at runtime.
    The parser must extract these and expand them via ``os.path.expanduser()``
    so they are checked against the workspace boundary.  Without this,
    ``~/Work/topologies`` bypasses the boundary entirely.

    The suffix (``/path``) must NOT be extracted as a bare absolute path —
    only the fully expanded ``/home/user/path`` form.
    """

    def test_tilde_home_path_extracted(self) -> None:
        """Standalone ~/path should be expanded and extracted."""
        paths = extract_absolute_paths("ls ~/Documents")
        home = os.path.expanduser("~")
        assert f"{home}/Documents" in paths

    def test_cp_from_tilde_to_workspace(self) -> None:
        """Both the expanded tilde source and absolute destination extracted."""
        paths = extract_absolute_paths("cp ~/data/file.csv /workspace/results/")
        home = os.path.expanduser("~")
        assert "/workspace/results/" in paths
        assert f"{home}/data/file.csv" in paths

    def test_rsync_tilde_source(self) -> None:
        paths = extract_absolute_paths("rsync ~/project/ /workspace/backup/")
        home = os.path.expanduser("~")
        assert "/workspace/backup/" in paths
        assert f"{home}/project/" in paths

    def test_scp_remote_tilde(self) -> None:
        """scp with remote user@host:~/path — the colon-prefixed tilde is not
        a local tilde path and must not be extracted."""
        paths = extract_absolute_paths("scp user@host:~/config.yaml /workspace/")
        assert "/workspace/" in paths
        # The remote tilde path follows a word char (colon-less host:~ pattern
        # has ~ preceded by ':' which the lookbehind allows, but the 'host'
        # prefix means the regex won't match).  Only /workspace/ extracted.
        home = os.path.expanduser("~")
        assert f"{home}/config.yaml" not in paths

    def test_ssh_cat_remote_tilde(self) -> None:
        """ssh host 'cat ~/file' — the tilde inside single quotes is still
        visible to the regex.  It will be expanded locally, which is
        conservative (may false-positive on remote paths, but never
        false-negative on local ones)."""
        paths = extract_absolute_paths("ssh host 'cat ~/Work/project/data.csv'")
        home = os.path.expanduser("~")
        assert f"{home}/Work/project/data.csv" in paths

    def test_cd_tilde(self) -> None:
        paths = extract_absolute_paths("cd ~/Work/project && make build")
        home = os.path.expanduser("~")
        assert f"{home}/Work/project" in paths

    def test_tilde_nested_deep_path(self) -> None:
        paths = extract_absolute_paths("cat ~/a/b/c/d/e/file.txt")
        home = os.path.expanduser("~")
        assert f"{home}/a/b/c/d/e/file.txt" in paths

    def test_tilde_mixed_with_absolute(self) -> None:
        """Command with both ~/path and /absolute/path."""
        paths = extract_absolute_paths("diff ~/local/config.yaml /etc/config.yaml")
        home = os.path.expanduser("~")
        assert "/etc/config.yaml" in paths
        assert f"{home}/local/config.yaml" in paths

    def test_bare_tilde_not_extracted(self) -> None:
        """Bare ~ without a path suffix must not be extracted."""
        paths = extract_absolute_paths("cd ~")
        assert paths == []

    def test_tilde_suffix_not_extracted_as_bare_absolute(self) -> None:
        """The /path suffix of ~/path must not appear as a separate entry."""
        paths = extract_absolute_paths("ls ~/Work/project")
        # /Work/project must NOT be in paths (old bug: lookbehind prevented
        # this, but now the full expanded path should appear instead).
        assert "/Work/project" not in paths

    def test_tilde_in_message_flag_not_extracted(self) -> None:
        """Tilde paths inside -m '...' message flags must not be extracted."""
        paths = extract_absolute_paths("git commit -m 'fix ~/Work/project'")
        assert paths == []

    def test_tilde_after_container_exec_not_extracted(self) -> None:
        """Tilde paths after container exec -- must not be extracted."""
        paths = extract_absolute_paths("docker exec mycontainer -- ls ~/Work/project")
        assert paths == []


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
