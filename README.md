# amplifier-module-hooks-workspace-boundary

Amplifier hook module that enforces filesystem workspace boundaries. Prevents agents — especially sub-agents spawned with minimal context — from reading, writing, or executing outside a configured workspace root.

## The Problem

When an orchestrator delegates work to a sub-agent, the sub-agent may receive little or no context about where it should operate. If the instruction is vague ("find the working copy") rather than explicit ("use `/home/user/Work/project`"), the sub-agent may search the filesystem, discover a same-name sibling repo, or drift into unrelated directories. Without a boundary enforcement mechanism, the kernel has no way to stop this.

This is structural, not behavioral. Improving delegation instructions, propagating context, and adding preferences all help — but they depend on the orchestrator getting it right every time. A filesystem boundary hook is the only defense that works independently of orchestrator behavior.

See [docs/examples.md](docs/examples.md) for 9 real-world scenarios showing how agents drift and how the hook catches each one.

## Design

This is a **hook module** (one of Amplifier's 5 module types) that subscribes to `tool:pre` events and uses `HookResult(action="deny")` to reject out-of-boundary tool calls before execution. It also subscribes to `tool:post` for audit logging.

### Key Properties

- **General-purpose**: Not tied to any specific bundle or workflow. Any Amplifier user can opt in.
- **Fail-closed**: On internal errors, the hook denies rather than allowing. Security hooks must not fail open.
- **Zero kernel changes**: Uses existing hook contracts (`tool:pre`, `deny` action, `user_message`).
- **Configurable**: Workspace root via explicit config, marker file discovery, or CWD default. Read/write allowlists. Enforcement modes (enforce, warn, audit-only).
- **Sub-agent safe**: Applies equally to root and child sessions. No `parent_id` filtering.

### Module Identity

| Property | Value |
|----------|-------|
| Module type | Hook |
| Module ID | `hooks-workspace-boundary` |
| Python package | `amplifier_module_hooks_workspace_boundary` |
| Events subscribed | `tool:pre` (rejection), `tool:post` (audit) |
| Priority | 5 (runs before `hooks-approval` at 10) |

### Companion Bundle

This repo contains the module only. A companion bundle (`amplifier-bundle-workspace-boundary`) provides the composition layer: behavior YAML, agent context instructions, and module source reference. The module can also be referenced directly from any bundle via `source: git+https://...`.

### Bash Scope

The hook enforces **filesystem** boundaries. It does not intercept network access, container-internal paths, or non-filesystem tokens.

**Pre-filters** strip tokens that are not host filesystem paths before boundary checking:

- **URLs** (`scheme://...`) — `curl http://host:3000/admin/pages` does not block on `/admin/pages`
- **Container exec** (`docker exec`, `incus exec`, `kubectl exec`, `amplifier-digital-twin exec`, etc.) — paths after the `--` separator are container-internal and are not checked against the host boundary
- **Glob patterns** — tokens containing `*` or `?` are discarded after extraction. Real filesystem paths never contain glob metacharacters; these appear in flag arguments like `find -path '*/foo/*'`, `grep --include='*.py'`, `rsync --exclude='*.log'`
- **Device nodes** — `/dev/null`, `/dev/stdout`, etc. are on the default read allowlist

**Known dynamic bypass vectors** (cannot be blocked without OS-level sandboxing):

- `CMD=/outside/path; cat $CMD`
- `$(find / -name secret)`
- `eval "cat /outside/path"`
- `source /outside/script.sh`

The hook warns on these ambiguous patterns by default. `bash_strict_mode: true` escalates warnings to denials. Full bash confinement requires OS-level sandboxing outside this hook's scope.

## Usage

Reference the module in a bundle behavior YAML:

```yaml
hooks:
  - module: hooks-workspace-boundary
    source: git+https://github.com/robotdad/amplifier-module-hooks-workspace-boundary@main
    config:
      workspace_root: ~/Work/my-project
      enforcement_mode: enforce       # enforce | warn | audit_only
      resolve_symlinks: true
      bash_strict_mode: false
      extra_workspace_roots: []       # additional workspace directories
      extra_read_roots: []            # additional read-only paths
      extra_write_roots: []           # additional writable paths
```

**Zero-config default:** If no `workspace_root` is set, the hook captures the process CWD at mount time. For sessions launched from a project directory, this Just Works.

### User Config Files

Users can declare additional safe directories without editing the bundle YAML by placing a `workspace-boundary.yaml` file at either or both levels:

| Level | Path | Scope |
|-------|------|-------|
| Global | `~/.amplifier/workspace-boundary.yaml` | All workspaces on this machine |
| Workspace | `.amplifier/workspace-boundary.yaml` | This workspace only |

**Accepted keys** (path-extension only):

```yaml
# ~/.amplifier/workspace-boundary.yaml (global)
extra_read_roots:
  - ~/shared-datasets

# .amplifier/workspace-boundary.yaml (workspace-level)
extra_workspace_roots:
  - ~/Work/shared-libs
extra_write_roots:
  - /mnt/output
```

**Merge behavior:** All three layers (global user config, workspace user config, bundle YAML config) are additive. Paths from any layer are merged together and deduplicated. No layer can remove paths declared by another.

**Security:** Only `extra_workspace_roots`, `extra_read_roots`, and `extra_write_roots` are accepted from user config files. Security-sensitive keys (`enforcement_mode`, `resolve_symlinks`, `bash_strict_mode`, `strict_unknown_tools`) are bundle-author-only — they are rejected with a warning if found in user config files.

**Timing:** User config files are read once at mount time. Changes take effect only when a new session starts, preserving the static-at-mount-time security guarantee.

**Auditability:** The `user_config_sources` field on `BoundaryConfig` records which files were checked, whether they loaded successfully, and how many paths each contributed. The hook logs this at INFO level.

## Testing

### Unit Tests

190 unit tests covering boundary checks, bash parsing (including URL, container exec, and glob pattern filters), config loading (including user config files), mount registration, enforcement modes, fail-closed behavior, and unknown tool handling.

```bash
# Clone and set up
git clone https://github.com/robotdad/amplifier-module-hooks-workspace-boundary
cd amplifier-module-hooks-workspace-boundary
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev]"

# Run tests
.venv/bin/python -m pytest tests/ -v
```

The tests mock `amplifier_core` imports (peer dependency), so they run standalone without the full Amplifier runtime.

### Test Bundle

`test-bundle/` contains a minimal Amplifier bundle for integration testing with a live session. It mounts the hook with `enforcement_mode: enforce` and a configurable `workspace_root`.

### DTU Integration Testing

A [Digital Twin Universe](https://github.com/microsoft/amplifier-bundle-digital-twin-universe) profile is included for end-to-end validation in an isolated environment.

**What the DTU provisions:**
- Ubuntu 24.04 with Python 3.12 and the module installed
- A **test workspace** at `/workspace/test-project/` (git repo with source files) — the boundary root
- A **decoy directory** at `/workspace/decoy-project/` (with `secret.txt`) — simulates a sibling repo the hook must block
- A validation script at `/opt/validation/validate_hook.py` that exercises 14 boundary scenarios

**Launch the DTU:**

```bash
amplifier-digital-twin launch dtu-profile.yaml
```

**Run the validation suite inside the DTU:**

```bash
# Unit tests (142 tests)
amplifier-digital-twin exec hooks-boundary-test -- bash -lc \
  'cd /opt/hooks-workspace-boundary && .venv/bin/python -m pytest tests/ -v'

# Direct validation scenarios (14 cases)
amplifier-digital-twin exec hooks-boundary-test -- \
  /opt/hooks-workspace-boundary/.venv/bin/python /opt/validation/validate_hook.py
```

**What the validation script checks:**

| Path | Operation | Expected |
|------|-----------|----------|
| `/workspace/test-project/src/main.py` | read | ALLOW |
| `/workspace/test-project/src/new.py` | write | ALLOW |
| `/workspace/decoy-project/secret.txt` | read | DENY |
| `/workspace/decoy-project/evil.txt` | write | DENY |
| `/tmp/output.log` | write | ALLOW (write allowlist) |
| `/home/user/.ssh/id_rsa` | read | DENY |
| `/etc/passwd` | read | DENY |
| `/workspace/test-project/../decoy-project/secret.txt` | read | DENY (traversal caught) |
| `/dev/null` | read | ALLOW (device allowlist) |

Plus 21 bash-parser checks: path extraction, ambiguous-pattern detection, URL pre-filter (4 cases), container exec pre-filter (5 cases), and glob pattern post-filter (6 cases).

**Interactive access:**

```bash
amplifier-digital-twin exec --visual-id "" hooks-boundary-test
```

**Tear down:**

```bash
amplifier-digital-twin destroy hooks-boundary-test
```

## Documentation

| Document | Contents |
|----------|----------|
| [DESIGN.md](DESIGN.md) | Full technical specification — 17 sections covering contracts, configuration, allowlists, sub-session inheritance, failure modes, testing requirements |
| [docs/examples.md](docs/examples.md) | 9 real-world scenarios showing what the hook guards against |
| [dtu-profile.yaml](dtu-profile.yaml) | Digital Twin Universe profile for isolated end-to-end testing |

## Status

Implemented with full test coverage. Design validated against Amplifier kernel contracts.

## License

MIT
