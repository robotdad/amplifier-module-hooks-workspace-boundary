# What This Guards Against

Real-world scenarios where agents operate outside the intended workspace. Each scenario describes the delegation pattern that causes boundary drift, shows what happens without the hook, and shows what happens with it.

All scenarios assume:
- **Workspace root:** `~/Work/my-project`
- **Hook configured:** `enforcement_mode: enforce`

---

## Scenario 1: Vague "find the working copy" delegation

### The delegation

The orchestrator knows the working copy is at `~/Work/my-project/my-repo` (from memory, status files, or prior context). But when delegating to a builder agent, it paraphrases the path into a search directive:

```python
delegate(
    agent="foundation:modular-builder",
    context_depth="none",
    instruction="""
    Locate the working copy from your shell environment — it should be a
    sibling of the current cwd or under a known path. Use `git remote -v`
    to confirm you are on the `main` branch. If you find the working copy
    at a path like `/home/user/Work/my-repo` (or similar), cd there before
    applying changes.
    """,
)
```

### Why it drifts

Three things combine:

1. `context_depth="none"` strips all workspace context from the sub-agent.
2. The instruction says "find" and "or similar" — giving the agent license to search.
3. The example path drops a directory segment (`~/Work/my-repo` instead of `~/Work/my-project/my-repo`), so the agent searches at the wrong level.

### What the sub-agent does without the hook

```bash
# Example path from instruction doesn't exist
cd /home/user/Work/my-repo && git remote -v
# → "No such file or directory"

# Agent widens search
find /home/user/Work -maxdepth 3 -name ".git" -type d | head -20
# → Finds ~/Work/other-checkout/my-repo/.git among 20 results

# Matches by remote URL — "same repo, must be it"
cd /home/user/Work/other-checkout/my-repo && git remote -v
# → Remote matches. Agent proceeds here.
```

The sub-agent reports back the wrong path. The orchestrator doesn't validate it against its own context. Subsequent agents receive the bad path as fact.

### What happens with the hook

```
[WorkspaceBoundary] Path access denied.
  Requested path: /home/user/Work
  Allowed boundary: /home/user/Work/my-project
  Operation: exec

→ bash: find /home/user/Work ... DENIED (search root is above workspace)
```

The `find` command targeting `/home/user/Work` (one level above the workspace) is denied before execution. The agent cannot discover sibling repos because it cannot search above the boundary.

---

## Scenario 2: `context_depth="none"` severs all workspace knowledge

### The delegation

```python
delegate(
    agent="foundation:modular-builder",
    context_depth="none",
    instruction="Implement the dependency gate per the spec. The repo uses the refactor branch.",
)
```

The instruction says *what* to build but not *where*. The agent has no status files, no preferences, no prior context — `context_depth="none"` stripped everything.

### What the sub-agent does without the hook

The agent has no path at all. It tries to orient itself:

```bash
pwd
# → /home/user/Work/my-project (correct CWD, but agent doesn't know this is the workspace)

# Agent decides to "find" the repo mentioned in the instruction
find /home/user -maxdepth 4 -name "*.git" -type d 2>/dev/null | grep -i "repo-name"
# → Finds multiple matches across the filesystem
```

The agent picks whichever match seems most plausible — often a stale checkout, a fork, or an archived copy.

### What happens with the hook

The `find` command rooted at `/home/user` is denied. The agent is forced to work within `~/Work/my-project`, which is the correct workspace. If the repo is a subdirectory of the workspace, `find` within the boundary succeeds. If not, the agent gets a clear error telling it exactly what the boundary is.

---

## Scenario 3: Agent reads sensitive files outside the workspace

### The delegation

```python
delegate(
    agent="foundation:explorer",
    context_depth="recent",
    instruction="Survey the authentication module and check how API keys are configured.",
)
```

### What the sub-agent does without the hook

A well-intentioned agent exploring "how API keys are configured" might check environment files and system configs:

```bash
cat ~/.aws/credentials
cat ~/.ssh/id_rsa
cat /etc/environment
cat ~/Work/other-project/.env
```

None of these are in the workspace. The agent is not malicious — it's following the instruction to understand key configuration by checking common locations.

### What happens with the hook

```
[WorkspaceBoundary] Path access denied.
  Requested path: /home/user/.aws/credentials
  Allowed boundary: /home/user/Work/my-project
  Operation: read
```

Every read outside the workspace boundary (and not on the read allowlist) is denied. `~/.aws/credentials` and `~/.ssh/id_rsa` are not on the default read allowlist. The agent is constrained to reading key configuration within the project itself.

---

## Scenario 4: Git operations in the wrong directory

### The delegation

An orchestrator completes implementation work and delegates the commit:

```python
delegate(
    agent="foundation:git-ops",
    context_depth="recent",
    context_turns=2,
    instruction="""
    Push the implementation to origin/main.
    Working copy: /home/user/Work/old-checkout/my-repo
    """,
)
```

The path in the instruction is wrong — it was inherited from a previous agent that discovered the wrong clone (Scenario 1). The git-ops agent has no way to know the path is wrong. It sees an explicit instruction with an absolute path.

### What the sub-agent does without the hook

```bash
cd /home/user/Work/old-checkout/my-repo
git add -A && git commit -m "feat: implement dependency gate"
git push origin main
```

The commit lands in the wrong repository. The correct working copy at `~/Work/my-project/my-repo` now has uncommitted changes that are "ahead" of what was pushed, and the stale checkout is now the source of truth.

### What happens with the hook

```
[WorkspaceBoundary] Path access denied.
  Requested path: /home/user/Work/old-checkout/my-repo
  Allowed boundary: /home/user/Work/my-project
  Operation: exec

→ bash: cd /home/user/Work/old-checkout/my-repo ... DENIED
```

The `cd` to the wrong directory is denied. Git-ops cannot operate outside the workspace.

---

## Scenario 5: Bash command with path variable indirection

### The delegation

```python
delegate(
    agent="foundation:modular-builder",
    context_depth="none",
    instruction="Run the test suite for the auth module.",
)
```

### What the sub-agent does without the hook

The agent constructs a bash command using variable indirection:

```bash
REPO_ROOT=$(find /home/user/Work -maxdepth 2 -name "auth-module" -type d | head -1)
cd $REPO_ROOT && pytest tests/
```

### What the hook does

The hook detects the `$(...)` command substitution pattern and emits a warning:

```
Boundary: bash command contains ambiguous patterns: '$(find' (command substitution)
```

In default mode, this is a warning — the command proceeds but the human operator is alerted. With `bash_strict_mode: true`, the command is denied outright:

```
[WorkspaceBoundary] Bash strict mode: command contains patterns that defeat
static analysis: '$(find' (command substitution)
```

**This is a known limitation.** The hook cannot semantically analyze bash. It catches the `find /home/user/Work` path (out of bounds → deny) and warns on the `$(...)` substitution. Full bash confinement requires OS-level sandboxing.

---

## Scenario 6: Orchestrator propagates a bad path through multiple agents

### The chain

This is the compound failure — the most dangerous pattern because each agent reinforces the previous agent's mistake.

```
Agent 1 (modular-builder):
  → Instruction: "Find the repo" (vague)
  → Discovers: ~/Work/other-checkout/my-repo (wrong)
  → Reports back: "Found at /home/user/Work/other-checkout/my-repo"

Agent 2 (orchestrator):
  → Receives the wrong path
  → Does NOT validate against its own context
  → Passes the wrong path to the next agent

Agent 3 (git-ops):
  → Instruction: "Push from /home/user/Work/other-checkout/my-repo" (explicit but wrong)
  → Commits and pushes from wrong workspace

Agent 4 (orchestrator):
  → Updates status: "Push successful"
  → Correct workspace is now behind, wrong workspace is ahead
```

### What the hook does

The chain breaks at Agent 1. The `find` command above the workspace boundary is denied. Agent 1 cannot discover the sibling repo, so Agent 2 never receives the wrong path, and Agents 3 and 4 never execute on it.

Even if Agent 1 somehow reports a wrong path (e.g., it was in the instruction text, not discovered via bash), Agent 3's `cd` to that path is independently denied. The hook provides **multiple independent rejection points** — the contamination cannot survive any one of them.

---

## Scenario 7: Agent writes to global configuration

### The delegation

```python
delegate(
    agent="foundation:modular-builder",
    context_depth="recent",
    instruction="Update the project configuration to use the new API endpoint.",
)
```

### What the sub-agent does without the hook

The agent interprets "project configuration" broadly:

```bash
# Updates the project config (correct)
echo 'API_URL=https://new.api.example.com' >> ~/Work/my-project/.env

# Also updates the global config (incorrect)
echo 'API_URL=https://new.api.example.com' >> ~/.config/my-tool/config.yaml
```

The write to `~/.config/` is outside the workspace and modifies global state that affects all projects.

### What happens with the hook

The write to `~/Work/my-project/.env` succeeds (inside workspace). The write to `~/.config/my-tool/config.yaml` is denied — `~/.config/` is not on the default write allowlist and is outside the workspace boundary.

---

## Scenario 8: Traversal attack via `..` paths

### The delegation

An agent constructs a relative path that escapes the boundary:

```python
# tool_input for read_file
{"file_path": "../../other-project/secrets.env"}
```

### What the hook does

The path normalization pipeline resolves `../../other-project/secrets.env` against the CWD:

```
~/Work/my-project/../../other-project/secrets.env
→ ~/other-project/secrets.env  (after abspath)
→ DENIED (outside ~/Work/my-project)
```

The `..` is collapsed by `os.path.abspath` before the boundary check. Traversal cannot escape.

---

## Scenario 9: Symlink resolving outside the workspace

### Setup

```
~/Work/my-project/data → /mnt/shared/datasets  (symlink)
```

### What happens

With `resolve_symlinks: true` (default):

```python
# tool_input for read_file
{"file_path": "~/Work/my-project/data/users.csv"}
```

The path resolves to `/mnt/shared/datasets/users.csv` after `os.path.realpath`. This is outside the workspace boundary → **denied**.

To allow access to the symlink target, add it explicitly:

```yaml
config:
  extra_read_roots:
    - /mnt/shared/datasets
```

With `resolve_symlinks: false`, the path stays as `~/Work/my-project/data/users.csv` which is inside the boundary → **allowed** (but the security guarantee is weaker because the symlink target is unverified).

---

## Summary: How the hook breaks each failure mode

| Failure mode | Without hook | With hook |
|---|---|---|
| Vague "find" instruction | Agent searches filesystem, finds wrong repo | Search above boundary denied |
| `context_depth="none"` strips path | Agent has no path, improvises | Forced to work within boundary |
| Reads outside workspace | Agent reads ~/.aws, ~/.ssh, other projects | Denied unless on read allowlist |
| Git in wrong directory | Commits land in stale checkout | `cd` to wrong dir denied |
| Variable indirection in bash | Agent uses `$()` to escape boundary | Warning (or deny in strict mode) |
| URLs in bash commands | `curl http://host/path` blocked as filesystem path | URL stripped before path check — not blocked |
| Container exec paths | `docker exec c -- cat /app/cfg` blocked as host path | Paths after `--` stripped — not blocked |
| Multi-agent path propagation | Bad path reinforced through chain | Chain breaks at first agent |
| Writes to global config | Agent modifies ~/.config, /etc | Denied unless on write allowlist |
| `..` traversal | Path escapes to parent directories | Collapsed by normalization, denied |
| Symlink escape | Path resolves outside workspace | Resolved by realpath, denied |

The hook provides defense independent of orchestrator behavior. It doesn't matter if the orchestrator gets the delegation wrong, strips context, or paraphrases paths. The boundary holds at the tool layer.
