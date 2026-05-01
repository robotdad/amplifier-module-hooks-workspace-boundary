---
bundle:
  name: workspace-boundary-test
  version: 0.1.0
  description: Test bundle for hooks-workspace-boundary integration testing

hooks:
  - module: hooks-workspace-boundary
    source: ../
    config:
      workspace_root: /tmp/workspace-boundary-test
      enforcement_mode: enforce
      resolve_symlinks: true
      bash_strict_mode: false
---

# Workspace Boundary Test Bundle

Test bundle for verifying hooks-workspace-boundary in a live Amplifier session.
Configures the boundary to `/tmp/workspace-boundary-test` for safe testing.
