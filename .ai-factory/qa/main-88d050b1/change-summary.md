## Change Summary

**Commits:** 2 in `main~1..main`, plus uncommitted working-tree changes
**Changed files:** 22
**Risk level:** 🔴 High

---

### What Changed

The gateway was extended from simple per-workspace session storage into a policy-aware runtime. The change adds execution profiles, admin-gated policy transitions, workspace preflight checks before runs and restarts, richer session lifecycle handling, and a split SQLite model for execution policy versus session state. It also adds new Telegram controls around `/session`, updates command enforcement before `codex exec`, and broadens test coverage for bootstrap, config validation, policy resolution, preflight, and migration behavior.

---

### Affected Areas

| Component | Change type | Description |
|---------------|-------------------------------|--------------------------|
| `config.py` | Changed | Added execution profile, command-rule-group, workspace-profile-default, admin-only, network-mode, and break-glass TTL configuration parsing and validation. |
| `execution_policy.py` | Added | Central resolver for effective workspace policy, admin authorization, workspace defaults, durable overrides, and break-glass overlay behavior. |
| `workspace_preflight.py` | Added | New filesystem safety and readiness checks before execution or restart, including allowed-root validation, `.codex` setup, and write/delete probe. |
| `workspace_store.py` | Changed | Split legacy session data into `execution_policies` and `session_states`, added migrations, new policy/session update paths, busy-state tracking, and session-id normalization. |
| `models.py` | Changed | Reworked session-related models to expose execution policy and session state separately through a combined `SessionRecord`. |
| `session_manager.py` | Changed | Added preflight before run/restart, effective policy resolution, break-glass expiry handling, policy-change restarts, busy-state persistence, and richer stop/restart reasons. |
| `codex_adapter.py` | Changed | Switched runtime input from loose sandbox/approval values to a resolved policy object, removed unsupported CLI approvals handling, and added prompt command-rule enforcement in the gateway. |
| `app.py` | Changed | Added `/session` command flow, admin-aware authorization checks for bind/use/policy changes, richer `/status`, and user-facing handling for preflight and policy failures. |
| `__main__.py` | Changed | Bootstraps policy resolver and preflight checker, wires them into the app and session manager, and adds startup dependency error logging. |
| Docs and config example | Changed | README, AGENTS, architecture notes, and sample config now describe profiles, policy precedence, preflight, and new runtime commands. |
| Tests | Added / Changed | Added targeted tests for config, policy resolver, bootstrap wiring, preflight, app session controls, and store migration; updated existing session and adapter tests. |

---

### Risks

🔴 **Critical** (must verify):

- SQLite migration correctness for existing deployments. The schema split from `sessions` into `execution_policies` plus `session_states` is high-impact and must preserve legacy session ids, models, approval settings, and usable defaults without corrupting live state.
- Policy enforcement may block legitimate work or allow unintended elevation. Authorization now depends on both `admin_only` flags and resolved profiles, so command transitions like `/use`, `/execmode`, `/approvals`, and `/session profile` need verification with admin and non-admin users.
- Preflight may prevent valid workspaces from running in production. The new read/write checks, `.codex` creation, and write/delete probe depend on real service-user filesystem permissions and can fail even when path binding succeeds.
- Runtime/session reset semantics changed. `/newsession`, `/resetsession`, `/session restart`, profile changes, and legacy policy commands now trigger fresh-session behavior, so resumability and state isolation need careful verification.

🟡 **Medium** (should verify):

- Gateway-side command rule enforcement may reject prompts that previously ran, especially for ops-like instructions containing fragments such as `sudo`, `systemctl`, `docker`, `/etc/`, or `/var/`.
- `/status` now reports resolved policy rather than only stored session fields, so displayed profile, network mode, rule set, busy state, and break-glass expiry should be checked against actual runtime behavior.
- Break-glass expiry relies on ISO timestamp comparison and policy cleanup before runs. Expired overlays and active overlays both need verification to ensure the effective profile returns to baseline correctly.
- Config validation is stricter. Existing configs with unknown rule groups, unsafe default profiles, invalid workspace profile mappings, or unsafe default network mode will now fail startup.

🟢 **Low** (nice to verify):

- Documentation and sample config now describe behavior that should stay aligned with implementation.
- Startup bootstrap logging and dependency wiring add observability, but should be checked once in logs to confirm expected events fire.

---

### Testing Recommendations

**First priority:**

- [ ] Upgrade an existing SQLite database and verify that bound workspaces, stored session ids, policy defaults, and subsequent runs all still work after initialization.
- [ ] Exercise `/bind`, `/use`, `/execmode`, `/approvals`, `/session show`, `/session profile ops`, `/session profile break-glass`, `/session restart`, and `/session reset` with both admin and non-admin users.
- [ ] Run prompts from a writable workspace, a read-only workspace, and an invalid/out-of-root workspace to verify preflight messaging, logging, and fresh-session behavior.
- [ ] Verify that allowed prompts still run while restricted prompts are rejected according to the active command rule group.

**Regression:**

- [ ] Re-check default workspace resolution, topic/thread isolation, `/stop`, and session resume behavior after the new policy/preflight hooks.
- [ ] Re-check startup with real `config.toml` values and confirm the service boots cleanly with the new execution-profile settings.
