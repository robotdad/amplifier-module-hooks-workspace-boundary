# amplifier-module-hooks-workspace-boundary

Amplifier hook module that enforces filesystem workspace boundaries. Prevents agents — especially sub-agents spawned with minimal context — from reading, writing, or executing outside a configured workspace root.

## The Problem

When an orchestrator delegates work to a sub-agent, the sub-agent may receive little or no context about where it should operate. If the instruction is vague ("find the working copy") rather than explicit ("use `/home/user/Work/project`"), the sub-agent may search the filesystem, discover a same-name sibling repo, or drift into unrelated directories. Without a boundary enforcement mechanism, the kernel has no way to stop this.

This is structural, not behavioral. Improving delegation instructions, propagating context, and adding preferences all help — but they depend on the orchestrator getting it right every time. A filesystem boundary hook is the only defense that works independently of orchestrator behavior.

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

## Status

**Requirements and design phase.** See [DESIGN.md](DESIGN.md) for the full technical specification.

## License

MIT
