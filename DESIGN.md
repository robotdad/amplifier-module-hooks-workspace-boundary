# Design: `hooks-workspace-boundary`

**Module type:** Hook
**Spec status:** Design — validated against kernel contracts
**Date:** 2026-04-30

## Table of Contents

- [§1 — Motivation](#1--motivation)
- [§2 — Why a Hook, Not a Kernel Feature](#2--why-a-hook-not-a-kernel-feature)
- [§3 — Module Contract](#3--module-contract)
- [§4 — Tool Coverage](#4--tool-coverage)
- [§5 — Path Normalization](#5--path-normalization)
- [§6 — Bash Command Parsing](#6--bash-command-parsing)
- [§7 — Boundary Configuration](#7--boundary-configuration)
- [§8 — Allowlists](#8--allowlists)
- [§9 — Sub-Session Inheritance](#9--sub-session-inheritance)
- [§10 — Failure Modes and UX](#10--failure-modes-and-ux)
- [§11 — Enforcement Modes](#11--enforcement-modes)
- [§12 — Override / Escape Hatch](#12--override--escape-hatch)
- [§13 — Observable Events](#13--observable-events)
- [§14 — Testing Requirements](#14--testing-requirements)
- [§15 — Module Placement](#15--module-placement)
- [§16 — Capability Matrix](#16--capability-matrix)
- [§17 — Design Review Notes](#17--design-review-notes)

---

## §1 — Motivation

When an orchestrator delegates work to a sub-agent, the sub-agent may receive little or no context about where it should operate. If the delegation instruction is vague — "find the working copy" rather than "use this absolute path" — the sub-agent will search the filesystem. If multiple clones of the same repo exist on disk (common in development environments), the sub-agent may discover a stale sibling, operate on the wrong workspace, and propagate the wrong path to subsequent agents.

This class of failure has three properties:

1. **It is structural.** As long as agents have unrestricted filesystem access and delegation can strip context, the conditions will eventually align again.
2. **It is silent.** The sub-agent succeeds from its own perspective — it found a repo with the right remote URL, on the right branch. No error is raised.
3. **Behavioral mitigations are insufficient.** Improving delegation instructions, propagating context, adding preference rules — all depend on the orchestrator getting it right every time. A single vague delegation undoes all of them.

A filesystem boundary hook is the only defense that works independently of orchestrator behavior. It operates at the tool layer, before execution, and rejects out-of-boundary access regardless of how the agent arrived at the path.

## §2 — Why a Hook, Not a Kernel Feature

The kernel is pure mechanism. Filesystem boundary policy passes the "could two teams want different behavior?" test — yes, some want strict confinement, some want roaming sessions. Therefore it belongs at the module layer.

A hook is the right vehicle because:
- Hooks can reject tool calls before execution via `deny`
- Opt-in via bundle composition (not forced on all users)
- Zero kernel changes required

## §3 — Module Contract

**Type:** Hook (one of the 5 module types).

**Events subscribed:**
- `tool:pre` — fires before tool execution. Primary enforcement point.
- `tool:post` — fires after tool execution. Audit logging of what actually executed within the boundary.

Use the canonical event constants from `amplifier_core.events` (`TOOL_PRE`, `TOOL_POST`) rather than string literals.

**Event payload schema** (from `HOOK_CONTRACT.md §Common Events`):
```python
# tool:pre
{
    "tool_name": str,    # registered name from tool's .name property
    "tool_input": dict,  # full input parameters
    "tool_obj": object,  # optional — the tool instance itself
}

# tool:post
{
    "tool_name": str,
    "tool_result": ...,  # result from tool execution
}
```

**Rejection mechanism:** `HookResult(action="deny", reason="...")` — `deny` short-circuits immediately; no further handlers run. Has highest precedence among all hook actions. `reason` propagates to the agent as a structured error (not an OS "permission denied").

**Important:** Because `deny` short-circuits, emit the `workspace_boundary:path_denied` observability event explicitly *before* returning the HookResult. Otherwise, logging hooks that run at lower priority will never see the rejection.

**Mount entry point:**
```python
async def mount(coordinator, config: dict | None = None) -> Callable | None:
    boundary = _resolve_boundary(config)
    handler = _make_handler(boundary, config)

    unregister_pre = coordinator.hooks.register(
        TOOL_PRE, handler, priority=5, name="workspace-boundary"
    )
    unregister_post = coordinator.hooks.register(
        TOOL_POST, _make_audit_handler(boundary), priority=50, name="workspace-boundary-audit"
    )

    def cleanup():
        unregister_pre()
        unregister_post()

    return cleanup
```

Use `priority=5` for the `tool:pre` handler so this hook fires before other hooks (e.g., `hooks-approval` at `priority=10`). This is the correct ordering — you don't want to ask a user to approve something the boundary forbids.

Use `priority=50` for the `tool:post` audit handler — low urgency, runs after other post-hooks.

**`on_session_ready()` is NOT needed.** The hook needs only its own config and the CWD, both available at `mount()`. No cross-module discovery required.

**`parent_id` filtering must NOT be used.** This hook applies equally to root and child sessions. The sub-agent is typically the offender — filtering by `parent_id` would defeat the purpose. The policy-behavior pattern documented in `POLICY_BEHAVIORS.md` is for non-security hooks.

## §4 — Tool Coverage

### MUST intercept (tool:pre)

| Tool | Path key in `tool_input` | Op type |
|------|--------------------------|---------|
| `read_file` | `file_path` | read |
| `write_file` | `file_path` | write |
| `edit_file` | `file_path` | write |
| `apply_patch` | `path` | write |
| `glob` | `path` (search root) | read |
| `grep` | `path` (search root) | read |
| `bash` | `command` (parsed — see §6) | exec |

**Pre-implementation requirement:** Empirically audit the `.name` property of every registered tool. The tool-name dispatch table is keyed on these strings, and they may not match module IDs.

**Config-overridable name table:** Provide a `tool_paths` config option so users can extend the dispatch table for custom tools:
```yaml
config:
  tool_paths:
    my_custom_reader: file_path
    my_custom_writer: output_path
```

**Unknown tools:** Default to `continue` with an observability event (`workspace_boundary:unknown_tool`). When `enforcement_mode: enforce` and `strict_unknown_tools: true`, default to `deny` instead.

### SHOULD consider

- `LSP` write-path operations (rename) — intercept; reads (hover, goToDefinition) — allow via read allowlist.

### Out of scope

- `delegate()` instruction text — cannot parse natural language reliably.
- `recipes` — recipes spawn their own sessions; boundary applies independently inside each session if the bundle includes this hook.
- `load_skill` — resolves into `~/.amplifier/`, in the read allowlist.

## §5 — Path Normalization

Required pipeline for every path argument:

```
raw_path
  → strip surrounding quotes (defensive)
  → os.path.expandvars   (env vars)
  → os.path.expanduser   (~ expansion)
  → os.path.abspath      (resolve relative paths against process CWD; collapse ..)
  → os.path.realpath     (if resolve_symlinks=true; default true)
  → compare against normalized boundary
```

**In-bounds predicate:**
```python
resolved(P) == resolved(boundary) or resolved(P).startswith(resolved(boundary) + os.sep)
```
The trailing separator is required — prevents `/workspace2` from matching inside `/workspace`.

**`resolve_symlinks` config:** Default `true`. Setting to `false` is for cases where the workspace root is itself a symlink (e.g., `/workspace → ~/Work/project`). The boundary itself must be resolved once at mount time (not per-call).

## §6 — Bash Command Parsing

This is the hard part. The hook can reject `bash` calls before execution but cannot fully sandbox bash semantically. Honest design:

| Strategy | Verdict |
|----------|---------|
| (a) Static parse for absolute path tokens | **Recommended primary.** Simple, transparent, catches the obvious cases. |
| (b) System sandbox (firejail/bwrap) | Out of scope. Belongs in tool layer, not hook. |
| (c) Whitelist bash patterns | Too restrictive, breaks normal use. |
| (d) No bash enforcement | Partially acceptable as documented limitation, but the hook should still try (a). |

### Required behavior for bash interception

1. **Pre-filter: strip non-filesystem tokens** before path extraction.
   - **URLs** — replace `scheme://...` tokens with whitespace so URL path components (e.g. `/admin/pages` in `http://host/admin/pages`) are not extracted.
   - **Container exec** — when a container runtime `exec` command is detected (`docker`, `podman`, `incus`, `kubectl`, `nerdctl`, `lxc`, `amplifier-digital-twin`), strip everything after the `--` separator. Those paths are container-internal, not host filesystem paths.
2. Extract absolute path tokens from the sanitized command via regex (`/[^\s;|&]*`).
3. **Post-filter: discard glob patterns** — remove any extracted token containing `*` or `?`. Real filesystem paths never contain these characters; their presence indicates a shell glob pattern passed as a flag argument (e.g. `find -path '*/foo/*'`, `grep --include='*.py'`, `rsync --exclude='*.log'`), not a path to check.
4. Resolve and check each remaining candidate against boundary.
5. `deny` if any candidate is out of bounds.
6. Emit `user_message` warning when the command contains patterns that defeat static analysis: `$(...)`, backticks, `${...}`, `eval`, `source`, `exec`, or assignments to path variables.
7. By default, do **not** deny ambiguous commands (would block too many legitimate cases). A `bash_strict_mode: true` config option escalates this to `deny`.

### Default read allowlist

`/dev/` is included in the default read allowlist alongside `/tmp/`, `/usr/`, `/lib/`, etc. This permits redirects to `/dev/null` and reads from device nodes without configuration. Note: `/dev/stderr` and `/dev/stdout` are symlinks to `/proc/self/fd/N` and resolve outside `/dev/` when `resolve_symlinks: true`.

### Documented bypass vectors (must be in module README)

The hook cannot reliably block:
- `CMD=/outside/path; cat $CMD`
- `$(find / -name secret)`
- `eval "cat /outside/path"`
- `source /outside/script.sh`

**Posture:** "Block the obvious cases, warn on ambiguous ones." Full bash confinement requires OS-level sandboxing outside this hook's scope.

## §7 — Boundary Configuration

### Sources (priority order)

| Priority | Source | Config key |
|----------|--------|------------|
| 1 | Hook config in mount plan | `workspace_root: <abs path or ~-prefixed>` |
| 2 | Marker file discovery | `discover_from_marker: true`, `marker_files: [".git", ".amplifier"]` (walk up from CWD) |
| 3 (default) | Process CWD at mount time | `use_cwd: true` (implicit default) |

### When captured

At `mount()` time. **Never re-evaluated per call** — that would let CWD drift widen the boundary.

### Example mount-plan config

```yaml
hooks:
  - module: hooks-workspace-boundary
    config:
      workspace_root: ~/Work/my-project
      resolve_symlinks: true
      extra_workspace_roots: []
      extra_read_roots: []
      extra_write_roots: []
      bash_strict_mode: false
      enforcement_mode: enforce   # enforce | warn | audit_only
```

**Zero-config default:** If no `workspace_root` is set, capture process CWD. For sessions launched from a project directory, this Just Works.

**Strong recommendation:** Always set `workspace_root` explicitly in production. The CWD-default is convenient for development but should not be the security guarantee — sub-processes or recipes with `cwd_override:` may not inherit the parent's CWD.

## §8 — Allowlists

### Default read allowlist (always permitted for reads)

```
~/.amplifier/        # skills, config
~/.gitconfig
~/.gitignore
~/.ssh/known_hosts
/tmp/
/var/tmp/
/etc/hosts
/etc/resolv.conf
/usr/                # system binaries and libs
/lib/, /lib64/
/opt/
sys.prefix/          # active virtualenv (resolved at mount time)
```

### Default write allowlist (stricter)

```
/tmp/
/var/tmp/
```

**Important:** Writes to `~/.amplifier/` must be **denied** by default. Any writes to user-global configuration require explicit `extra_write_roots` opt-in.

### Check order

```
For path P, op (read|write):
  1. write op: check write allowlist → permit if match
  2. read op:  check read allowlist  → permit if match
  3. check workspace roots (boundary + extra_workspace_roots) → permit if match
  4. deny
```

## §9 — Sub-Session Inheritance

**Critical kernel fact** (from `SESSION_FORK_SPECIFICATION.md`): The kernel does NOT propagate hook config to child sessions. Each child runs its own mount wave.

**How inheritance actually works:** Via bundle composition. If the hook is declared in the bundle (or behavior YAML), child sessions spawned from agents in that bundle will mount the hook independently with the same config.

**Boundary value in children:**
- Explicit `workspace_root` config → identical in all sessions (correct).
- `use_cwd: true` default → captured at child mount time. In standard deployments the process CWD is the same → identical boundary. **Edge case:** if a child runs in a subprocess with a different CWD, this would be wrong.

**Widening protection:** A child cannot widen its boundary at runtime. The agent has no API to mutate hook config. Only changing the mount-plan YAML (human action) changes the boundary.

### Known limitation: agent overlay hooks list

From `MOUNT_PLAN_SPECIFICATION.md §agents Section`: agent configurations are partial mount plans that get merged with a parent session's config. If an agent overlay specifies its own `hooks: [...]` list and the foundation's merge semantics *replace* rather than merge, the boundary hook could be silently dropped. This should be verified empirically and documented. Recommend agents inherit hooks rather than override.

## §10 — Failure Modes and UX

### Rejection message format (returned in `reason`)

```
[WorkspaceBoundary] Path access denied.
  Requested path: <resolved offending path>
  Allowed boundary: <configured workspace root>
  Operation: <read|write|exec>

This path is outside the configured workspace boundary. To allow access,
add the path to extra_workspace_roots (or extra_read_roots / extra_write_roots)
in the hook configuration.
```

### Complementary `user_message`

Also return `user_message` so the human operator sees rejections in real time:

```python
HookResult(
    action="deny",
    reason="[WorkspaceBoundary] Path access denied. ...",   # → seen by agent
    user_message=f"Boundary violation blocked: {resolved_path}",  # → seen by human
    user_message_level="error",
    user_message_source="workspace-boundary",
)
```

Use `user_message_level="error"` for actual denials and `"warning"` for bash ambiguity cases.

### Internal error policy: FAIL CLOSED

| Failure | Behavior |
|---------|----------|
| Path normalization error | `deny` with reason "could not resolve path — boundary policy requires resolution" |
| Config load error at mount | Log error; fall back to `os.getcwd()` (fail-safe) |
| Exception in handler | `deny` with reason "boundary check error — failing closed" |

This invokes the documented exception clause in `HOOKS_API.md §Error Handling`:

> Hook failures should not crash the kernel or block operations **unless explicitly intended** (e.g., validation failure should return `action="deny"` on purpose).

Security hooks are the explicitly intended case. This is not an override — it's the documented usage.

## §11 — Enforcement Modes

| Mode | Mechanism | Use Case |
|------|-----------|----------|
| `enforce` (default) | `HookResult(action="deny", reason=..., user_message=...)` | Production security |
| `warn` | `HookResult(action="inject_context", context_injection="...", user_message=...)` | Transitional adoption |
| `audit_only` | `HookResult(action="continue")` + emit observability event | Passive monitoring |

**Caveat on `warn` mode:** `inject_context` is subject to budget limits (`session.injection_size_limit` default 10 KB, `session.injection_budget_per_turn` default 10,000 tokens). If a session generates many out-of-boundary calls, warn-mode injections could be silently dropped. Keep warn-mode messages short (one line, no path repetition) to maximize the number of warnings that fit in the budget.

Set `context_injection_role="system"` (the default) — this is environmental feedback, not user input simulation.

## §12 — Override / Escape Hatch

### Primary: `extra_workspace_roots` (static config)

```yaml
config:
  workspace_root: ~/Work/my-project
  extra_workspace_roots:
    - ~/Work/shared-libs    # adjacent repo legitimately needed
    - /mnt/data/datasets    # large data volume
```

Static, auditable, human-controlled. No runtime expansion.

### Secondary: `extra_read_roots` / `extra_write_roots`

Fine-grained permission for specific paths without full workspace root status.

### Secondary: `bash_strict_mode`

Escalates the `bash` ambiguity warnings into denials for high-security contexts.

### Tertiary: User config files (`workspace-boundary.yaml`)

Users can contribute additional safe directories without editing the bundle YAML by placing a `workspace-boundary.yaml` file at two levels:

| Level | Path | Scope |
|-------|------|-------|
| Global | `~/.amplifier/workspace-boundary.yaml` | All workspaces on this machine |
| Workspace | `{workspace_root}/.amplifier/workspace-boundary.yaml` | This project only |

**Accepted keys (path-extension only):**
- `extra_workspace_roots` — full read+write access
- `extra_read_roots` — read-only access
- `extra_write_roots` — write access

**Security constraint:** `enforcement_mode`, `resolve_symlinks`, `bash_strict_mode`, and `strict_unknown_tools` are **not accepted** from user config files. These are bundle-author-only. If present, they are rejected with a warning log and silently ignored.

**Merge semantics:** All three sources (global user config, workspace user config, bundle YAML config) are merged additively. Paths from any source are combined and deduplicated. No source can remove paths declared by another.

**Timing:** User config files are read by `_load_user_config()` during `resolve_boundary()`, which runs once at `mount()` time. They are never re-read mid-session. Changes take effect only on session boundaries — this preserves the static-at-mount-time security guarantee.

**Auditability:** `BoundaryConfig.user_config_sources` records for each config file checked: path, whether it loaded, how many paths it contributed, and any error. The hook logs this at INFO level. This supports incident investigation ("which user config files were active when the boundary allowed this access?").

**Failure handling:** Missing files are silently skipped (expected case). Malformed YAML logs a warning and returns empty (session still starts). Non-mapping YAML, non-list values, and non-string entries are each warned and skipped individually. User config loading failures reduce the boundary (fewer allowed paths) rather than widening it — consistent with the fail-closed philosophy.

**Implementation:** `config.py` functions `_load_user_config(path)` and `_user_config_paths(workspace_root)`. Uses PyYAML (`yaml.safe_load`) with graceful fallback if PyYAML is not available (debug log, skip user config loading entirely).

### Explicitly NOT recommended: runtime mode toggle

A `/mode permissive` runtime escape is an anti-pattern for security hooks — it lets the agent disable its own guardrail. If cross-boundary access is needed, configure it in the mount plan before session start, or use a user config file (`workspace-boundary.yaml`) at the global or workspace level.

## §13 — Observable Events

Use `register_contributor` (NOT `register_capability` — that creates singleton ownership and breaks discovery by `hooks-logging`):

```python
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
```

| Event | Payload | When |
|-------|---------|------|
| `workspace_boundary:path_denied` | `tool_name`, `raw_path`, `resolved_path`, `boundary`, `operation` | `deny` action fired |
| `workspace_boundary:path_allowed` | `tool_name`, `resolved_path` | Path within boundary (audit trail) |
| `workspace_boundary:allowlisted` | `tool_name`, `resolved_path`, `allowlist_rule` | Allowed via allowlist, not boundary |
| `workspace_boundary:bash_warning` | `command_fragment`, `reason` | Static analysis hit ambiguity |
| `workspace_boundary:unknown_tool` | `tool_name` | Tool not in dispatch table |

## §14 — Testing Requirements

Use `TestCoordinator` and `EventRecorder` from `amplifier_core.testing`. Required cases:

| Test case | Expected result |
|-----------|----------------|
| Path within boundary | `action="continue"` |
| Path outside boundary | `action="deny"` with formatted reason |
| Path on read allowlist | `action="continue"` |
| Path on write allowlist (write op) | `action="continue"` |
| `..` traversal escaping boundary | `action="deny"` |
| Symlink resolving outside (resolve_symlinks=true) | `action="deny"` |
| Bash with explicit out-of-boundary path | `action="deny"` |
| Bash with glob patterns (`find -path '*/foo/*'`, `grep --include='*.py'`) | `action="continue"` (glob tokens discarded, not checked) |
| Bash with `$VAR` paths | `action="continue"` + `user_message` warning |
| Bash with `eval` | `action="continue"` + `user_message` warning |
| Bash with `$(...)` | `action="continue"` + `user_message` warning |
| Handler exception | `action="deny"` (fail closed) |
| Sub-session inherits same boundary | identical in parent and child |
| Agent overlay with `hooks: []` | verify whether boundary hook propagates (known edge case) |
| Recipe step with parent-level boundary | verify whether boundary applies inside recipe session |
| Unknown tool name | `action="continue"` + observability event |
| `tool:post` audit handler | emits `workspace_boundary:path_allowed` for in-boundary executions |

## §15 — Module Placement

### Module repo (this repo)

`amplifier-module-hooks-workspace-boundary` — standalone Python module.

```
amplifier-module-hooks-workspace-boundary/
├── pyproject.toml
├── README.md
├── DESIGN.md
└── amplifier_module_hooks_workspace_boundary/
    ├── __init__.py          # mount() entry point
    ├── boundary.py          # path resolution and checking
    ├── bash_parser.py       # static analysis of bash commands
    └── config.py            # configuration loading and defaults
```

### Companion bundle repo (separate)

`amplifier-bundle-workspace-boundary` — thin composition layer.

```
amplifier-bundle-workspace-boundary/
├── bundle.md
├── behaviors/
│   └── workspace-boundary.yaml
├── context/
│   └── instructions.md
```

The bundle references this module via:
```yaml
hooks:
  - module: hooks-workspace-boundary
    source: git+https://github.com/robotdad/amplifier-module-hooks-workspace-boundary@main
    config:
      workspace_root: ...
```

**Rationale for separation:** The module can be reused by any bundle. The bundle is a thin composer that provides behavior YAML and agent context. Different responsibilities. Coupling violates single-responsibility. Foundation as the home would make this mandatory for all foundation users — must remain opt-in.

## §16 — Capability Matrix

| Requirement | Status |
|-------------|--------|
| Hook module type | Kernel-supported |
| `tool:pre` event with rejection capability | Kernel-supported |
| `tool:post` event for audit logging | Kernel-supported |
| `deny` action with `reason` propagation | Kernel-supported |
| `user_message` for human-visible alerts | Kernel-supported |
| `user_message_source` for attribution | Kernel-supported |
| `inject_context` for warn mode | Kernel-supported |
| `register_contributor` observability | Kernel-supported |
| Sub-session inheritance | App-layer via bundle composition |
| Full bash semantic enforcement | NOT supported by any mechanism — best-effort only |
| Runtime widening prevention | Supported by design (no runtime hook API for config) |

## §17 — Design Review Notes

The following corrections and additions were identified during expert review against kernel contracts:

1. **Module naming convention:** Changed from `hook-workspace-boundary` (singular) to `hooks-workspace-boundary` (plural) to match the established convention (`hooks-approval`, `hooks-logging`, `hooks-redaction`).

2. **Added `tool:post` subscription** for audit logging. Defense in depth — even when `enforce` mode denies, an audit record of what was denied belongs in observability.

3. **Fail-closed is not an override** of `HOOKS_API.md` guidance. It invokes the documented exception clause for security hooks. Reframed accordingly.

4. **`priority=5` confirmed correct** for pre-empting `hooks-approval` at `priority=10`. First `deny` short-circuits all remaining handlers.

5. **Do not use `parent_id` filtering.** The sub-agent is typically the offender. Boundary applies equally to all sessions.

6. **Do not use `on_session_ready()`.** `mount()` is sufficient — no cross-module discovery needed.

7. **`user_message_source` field** added for clean attribution in operator UI.

8. **Injection budget warning** added for `warn` mode — keep messages short to avoid silent drops.

9. **Agent overlay edge case** flagged for empirical testing — foundation merge semantics may allow agent overlays to silently drop hooks.

10. **Kernel roadmap checked.** No conflicts with active work (Rust kernel switchover, gRPC v2, WASM modules, unified providers). Hook contracts are stable.

### Prior art

`amplifier-module-hooks-approval` — uses `ask_user` action on `tool:pre`. Closest sibling module. Study its `__init__.py` for the canonical mount/cleanup pattern and `approval_hook.py` for the handler structure including the fail-closed exception pattern.
