## Test Plan: Execution Policies, Preflight, and Session Lifecycle

**Date:** 2026-04-20
**Branch / Version:** `main`
**Environment:** local development or staging deployment with real filesystem permissions

---

### 1. Testing Goal

Verify that the gateway’s new policy-aware runtime works correctly end to end: configuration loads safely, existing SQLite state migrates cleanly, workspace execution is gated by preflight and policy rules, Telegram commands enforce the right admin boundaries, and session restart/reset behavior remains isolated per workspace.

---

### 2. Test Scope

**In Scope** — we test:

- Execution profile parsing and startup validation from `config.toml`
- Workspace policy resolution, durable overrides, and break-glass activation/expiry
- SQLite migration from legacy `sessions` into `execution_policies` and `session_states`
- Preflight checks before run and restart
- Telegram command behavior for `/bind`, `/use`, `/status`, `/execmode`, `/approvals`, `/session show`, `/session profile`, `/session restart`, `/session reset`, `/newsession`, and `/resetsession`
- Gateway-side prompt rule enforcement before launching `codex exec`
- Session lifecycle behavior: busy/idle state, stop reason, last restart, resume, and fresh-session transitions
- Default workspace behavior, topic/thread isolation, and `/stop` regression coverage

**Out of Scope** — we don't test:

- Unrelated Telegram Bot API failures or network outages not caused by this change
- systemd packaging details beyond confirming startup behavior and logs
- Pure documentation wording unless it mismatches observed behavior
- Performance benchmarking, since the change is primarily behavioral and operational

---

### 3. Test Types

| Type | Priority | Area |
|-------------------|------------|----------------------------------------|
| Functional | 🔴 High | Execution profiles, `/session` flows, startup wiring, session resets/restarts |
| Regression | 🟡 Medium | Default workspace resolution, topic isolation, resume behavior, `/stop` |
| Edge cases | 🟡 Medium | Break-glass expiry, empty session ids, workspace selectors, busy workspace transitions |
| Negative | 🟡 Medium | Invalid config, denied admin actions, preflight failure, blocked prompts |
| Security | 🔴 High | Admin-only transitions, privileged profiles, path safety, command-rule rejection |

---

### 4. Test Data

| Category | Data | Purpose |
|-------------------|-----------|---------------------|
| Valid data | Admin user id from `TELEGRAM_ADMIN_IDS`, workspace under `allowed_roots`, profile names `default`, `ops`, `break-glass` | Happy path for policy and session commands |
| Boundary values | `break_glass_ttl_seconds=60`, workspace with existing legacy session row, empty session id, workspace selector by name and by `path:` prefix | Verify migration and expiry boundaries |
| Invalid data | Unknown profile name, unknown command rule group, unsafe default network mode, workspace outside `allowed_roots`, non-admin caller | Negative and authorization scenarios |
| Special cases | Writable workspace, readable-but-not-writable workspace, symlink escape, prompt containing `sudo systemctl`, prompt with normal safe text | Preflight and command-rule enforcement coverage |

---

### 5. Preconditions

- [ ] A copy of the gateway is available with the current code and a writable test SQLite database
- [ ] At least one allowed workspace exists and is writable by the service user
- [ ] At least one workspace path exists that is outside `allowed_roots` or intentionally unwritable
- [ ] One admin Telegram user and one non-admin Telegram user are available for manual command checks
- [ ] `config.toml` includes execution profiles, command rule groups, workspace defaults, and admin-only settings
- [ ] A legacy SQLite fixture or backup exists with the old `sessions` table format
- [ ] Logs are accessible so policy, preflight, and migration events can be confirmed

---

### 6. Acceptance Criteria

- [ ] All 🔴 high-priority policy, migration, preflight, and session-lifecycle scenarios pass
- [ ] Existing workspace bindings and resumable sessions remain usable after database initialization/migration
- [ ] Non-admin users are blocked from all configured privileged transitions and admins are allowed through
- [ ] Invalid or unsafe workspaces fail with clear Telegram-visible preflight messages and corresponding logs
- [ ] Session-changing commands produce a fresh next run without cross-workspace leakage
- [ ] Restricted prompts are blocked only when the active command rule group requires it
- [ ] Startup fails fast for unsafe or inconsistent config and succeeds for valid config

---

### 7. Plan Risks

| Risk | Impact | Mitigation |
|----------|----------------------|----------------|
| Local-only testing misses real service-user permission problems | High | Run at least one pass in a deployment-like environment with the same user and filesystem permissions as production |
| Command and policy behavior depends on admin configuration choices | Medium | Test both configured-admin and configured-non-admin variants using the real `admin_only` settings |
| Migration coverage misses edge cases from older databases | High | Use a legacy fixture with pre-existing session ids, model values, and last-used timestamps before initialization |
| Break-glass timing is time-sensitive | Medium | Use short TTL test data and verify both active and expired states explicitly |

### 8. Checklist

| Check | Priority |
|-----------|-----------------------|
| Start the app with a valid config and confirm `policy_services_bootstrapped` plus clean startup | High |
| Start the app with invalid profile/rule-group/network settings and confirm startup fails clearly | High |
| Initialize against a legacy DB and verify session data migrates into split policy/session tables correctly | High |
| Verify `/bind` and `/use` behavior for admin and non-admin users under configured restrictions | High |
| Verify `/session show` and `/status` reflect resolved profile, sandbox, approvals, network mode, rule set, busy state, and restart metadata | High |
| Verify `/session profile ops` and `/session profile break-glass` create the expected fresh-session behavior | High |
| Verify `/execmode` and `/approvals` still work as legacy aliases and force a fresh next session | High |
| Verify `/session restart`, `/session reset`, `/newsession`, and `/resetsession` clear the active session id without mixing workspace state | High |
| Verify a valid writable workspace passes preflight and creates `.codex` when needed | High |
| Verify invalid, symlink-escape, or unwritable workspaces fail preflight with actionable messages | High |
| Verify safe prompts run and blocked prompts are rejected according to the active command rule group | High |
| Verify break-glass expires and the effective policy returns to the lower-privilege baseline | Medium |
| Verify `/stop`, default workspace resolution, and topic/thread isolation still behave correctly | Medium |
| Confirm docs and config example match observed command names and runtime behavior | Low |
