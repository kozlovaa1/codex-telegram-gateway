## Test Cases: Execution Policies, Preflight, and Session Lifecycle

---

### TC-001: Startup succeeds with valid execution-profile config

**Priority:** High
**Type:** Positive

**Precondition:**

A valid `config.toml` exists with:
- `default`, `ops`, and `break-glass` execution profiles
- valid `command_rule_groups`
- `workspace_defaults`
- a real writable `sqlite_path`, `runtime_dir`, and `log_dir`

**Steps:**

1. Start the gateway with the valid config and env file.
2. Wait for application initialization to complete.
3. Inspect startup logs.

**Expected result:**

The process starts successfully, initializes the database, and logs startup success including `config_validation_succeeded` and `policy_services_bootstrapped`. No config or component wiring exception is logged.

**Test data:**

```text
default_network_mode = "restricted"
break_glass_ttl_seconds = 1800
[workspace_defaults]
server-ops = "/srv/projects/demo"
[execution_profiles.ops]
approval_policy = "on-request"
command_rule_group = "ops"
admin_only = true
```

---

### TC-002: Startup fails for unsafe or inconsistent config

**Priority:** High
**Type:** Negative

**Precondition:**

Prepare invalid variants of `config.toml`.

**Steps:**

1. Set `default_network_mode = "enabled"` and start the gateway.
2. Restore config, then set an execution profile to reference a missing command rule group and start again.
3. Restore config, then set a workspace profile default to a nonexistent profile and start again.

**Expected result:**

Each invalid config causes startup to fail fast before normal app run. The error clearly indicates the validation problem, and the service does not continue into polling mode.

**Test data:**

```text
Variant A: default_network_mode = "enabled"
Variant B: [execution_profiles.ops] command_rule_group = "missing"
Variant C: [workspace_profile_defaults] infra = "missing"
```

---

### TC-003: Legacy SQLite session data migrates cleanly

**Priority:** High
**Type:** Positive

**Precondition:**

A SQLite database exists with the legacy `sessions` table and at least one row for workspace `infra`.

**Steps:**

1. Start the gateway against the legacy database.
2. Let initialization finish.
3. Inspect the database contents after initialization.
4. Query the migrated workspace through the bot or by reading DB rows.

**Expected result:**

The legacy session row is preserved in the new split structure. `execution_policies` contains the migrated sandbox/approval/default network values, `session_states` contains the session id/model/last used fields, and the workspace remains usable for subsequent runs.

**Test data:**

```text
workspace_name = "infra"
session_id = "thread-1"
model = "gpt-5"
sandbox_mode = "workspace-write"
approval_policy = "never"
last_used_at = "2026-04-20T10:05:00Z"
```

---

### TC-004: Non-admin user is denied admin-only bind

**Priority:** High
**Type:** Negative

**Precondition:**

`admin_only.bind = true`, the caller is not in `TELEGRAM_ADMIN_IDS`, and the target workspace path is otherwise valid.

**Steps:**

1. From a non-admin Telegram account, send `/bind demo /srv/projects/demo`.
2. Observe the bot reply.
3. Confirm no binding change was persisted.

**Expected result:**

The bot replies `Admin only.` and the workspace binding is not created or updated.

**Test data:**

```text
User role: non-admin
Command: /bind demo /srv/projects/demo
```

---

### TC-005: `/use` obeys admin-only policy when enabled

**Priority:** High
**Type:** Negative

**Precondition:**

`admin_only.use = true`, workspace alias `demo` already exists, and the caller is not an admin.

**Steps:**

1. From a non-admin Telegram account, send `/use demo`.
2. Observe the reply.
3. Confirm the current chat/topic binding does not change.

**Expected result:**

The bot replies `Admin only.` and the existing binding remains unchanged.

**Test data:**

```text
User role: non-admin
Existing alias: demo -> /srv/projects/demo
Command: /use demo
```

---

### TC-006: `/session profile ops` applies privileged profile and forces a fresh session

**Priority:** High
**Type:** Positive

**Precondition:**

An admin user is bound to a valid workspace with an existing non-empty session id.

**Steps:**

1. From the admin account, send `/session profile ops`.
2. Observe the bot reply.
3. Check `/session show`.
4. Send the next normal prompt in the same workspace.

**Expected result:**

The command succeeds, the bot confirms profile change and fresh-session behavior, the stored session id is cleared before the next run, and `/session show` reflects `ops` policy values.

**Test data:**

```text
Initial session_id: session-1
Command: /session profile ops
Expected profile: ops
Expected approvals: on-request
Expected network_mode: restricted
```

---

### TC-007: `/session profile break-glass` enables temporary elevation with expiry

**Priority:** High
**Type:** Positive

**Precondition:**

An admin user is bound to a valid workspace and `break_glass_ttl_seconds` is set to a short known value for testing.

**Steps:**

1. Send `/session profile break-glass` or `/session profile bg`.
2. Observe the response text.
3. Run `/session show` immediately.
4. Wait until after the TTL expires.
5. Send a normal prompt or `/session show` again.

**Expected result:**

The bot reports that break-glass is enabled until a concrete timestamp. While active, the effective policy is `break-glass`. After expiry and the next resolution point, the overlay is removed and the workspace returns to its lower-privilege baseline policy.

**Test data:**

```text
Command alias: /session profile bg
TTL for test: 60 seconds
Expected active network_mode: enabled
Expected active sandbox_mode: danger-full-access
```

---

### TC-008: Legacy `/execmode` and `/approvals` commands still work and reset the next session

**Priority:** High
**Type:** Positive

**Precondition:**

Admin user, valid bound workspace, and existing session id present.

**Steps:**

1. Send `/execmode readonly`.
2. Observe the reply.
3. Send `/approvals untrusted`.
4. Observe the reply.
5. Run `/session show`.
6. Send the next prompt.

**Expected result:**

Both commands succeed, both mention that a fresh session will be used for the next run, the resolved sandbox/approval settings update accordingly, and the next prompt starts without reusing the old session id.

**Test data:**

```text
/execmode readonly
/approvals untrusted
Expected sandbox after alias normalization: read-only
Expected approval_policy: untrusted
```

---

### TC-009: `/session restart`, `/session reset`, `/newsession`, and `/resetsession` clear session state without leaking across workspaces

**Priority:** High
**Type:** Positive

**Precondition:**

Two workspaces are available, each with its own existing session id and bound chat or topic.

**Steps:**

1. In workspace A, run `/session restart`.
2. In workspace A, verify the next run starts fresh.
3. In workspace B, confirm its session id is unchanged.
4. Repeat with `/session reset`, `/newsession`, and `/resetsession`.

**Expected result:**

Each command clears only the targeted workspace session state, updates restart metadata, and does not affect the session id or policy state of any other workspace/chat/topic.

**Test data:**

```text
Workspace A session_id: alpha-1
Workspace B session_id: beta-1
Commands: /session restart, /session reset, /newsession, /resetsession
```

---

### TC-010: Writable workspace passes preflight and `.codex` is prepared

**Priority:** High
**Type:** Positive

**Precondition:**

A workspace directory exists inside `allowed_roots` and is writable by the service user.

**Steps:**

1. Bind or use the writable workspace.
2. Send a normal prompt.
3. Inspect the workspace filesystem after the attempt.
4. Inspect logs.

**Expected result:**

Preflight succeeds, `.codex` exists after the check if it did not already exist, the prompt is allowed to proceed to `codex exec`, and logs include successful preflight/start events.

**Test data:**

```text
Workspace path: /srv/projects/demo
Prompt: "Summarize the current repository structure."
```

---

### TC-011: Invalid or unsafe workspace fails preflight with clear user feedback

**Priority:** High
**Type:** Negative

**Precondition:**

Prepare three invalid targets:
- a path outside `allowed_roots`
- a symlink escaping outside an allowed root
- a workspace that is readable but not writable by the service user

**Steps:**

1. Bind or target the invalid workspace case.
2. Attempt a prompt or `/session restart`.
3. Observe the Telegram-visible error.
4. Inspect logs.

**Expected result:**

Execution is blocked before starting `codex exec`. The bot returns a preflight failure message naming the failing check, and logs include `preflight_failed` with the failing reason. Existing session state is preserved when restart preflight fails.

**Test data:**

```text
Case A: /outside/root/project
Case B: /srv/projects/link -> /tmp/escaped
Case C: /srv/projects/readonly-workspace
```

---

### TC-012: Safe prompt runs but restricted prompt is rejected by command rules

**Priority:** High
**Type:** Negative

**Precondition:**

The workspace is bound and valid. Run once under `default` profile and optionally once under `ops`.

**Steps:**

1. Send a safe prompt such as `List the Python modules in this repository.`
2. Confirm the run starts normally.
3. Send a restricted prompt such as `Please run sudo systemctl restart nginx`.
4. Observe the bot reply and logs.

**Expected result:**

The safe prompt proceeds normally. The restricted prompt is rejected by the gateway before process launch, the user sees a policy rejection message, and logs include `command_rule_violation`.

**Test data:**

```text
Safe prompt: "List the Python modules in this repository."
Restricted prompt: "Please run sudo systemctl restart nginx"
```

---

### TC-013: `/status` and `/session show` expose resolved runtime and policy state accurately

**Priority:** High
**Type:** Positive

**Precondition:**

A workspace exists with a known profile, known approvals mode, and either active or idle runtime state.

**Steps:**

1. Run `/status`.
2. Run `/session show`.
3. If possible, trigger an active run and check status during execution.
4. Compare the displayed values with the known workspace policy and runtime state.

**Expected result:**

Displayed fields match the effective state: workspace, path, profile, session id, busy flag, runtime, sandbox mode, approvals, network mode, rule set, model, break-glass expiry, last used, and last restart.

**Test data:**

```text
Profile: ops
Approval policy: on-request
Network mode: restricted
Busy state variants: idle and active run
```

---

### TC-014: `/stop`, default workspace resolution, and topic isolation still behave correctly

**Priority:** Medium
**Type:** Regression

**Precondition:**

Default workspace is configured, and at least one forum topic or threaded private conversation can be used.

**Steps:**

1. In an unbound chat, send a prompt and confirm the default workspace is used.
2. In two separate topics or threads, bind or use different workspaces.
3. Start a long-running prompt in one topic and send `/stop`.
4. Verify the other topic’s session and binding remain unaffected.

**Expected result:**

Default workspace fallback still works, topic/thread session isolation is preserved, `/stop` only interrupts the targeted workspace runtime, and unrelated chats/topics do not inherit session or policy state.

**Test data:**

```text
Default workspace: server-ops
Topic A workspace: demo
Topic B workspace: infra
Long-running prompt: "Explain this repository in detail, one file at a time."
```

## Test Data (based on test design techniques)

### Positive

* Admin user id included in `TELEGRAM_ADMIN_IDS`
* Workspace `/srv/projects/demo` writable by the service user
* Existing session ids such as `session-1`, `alpha-1`, `beta-1`
* Profiles `default`, `ops`, `break-glass`
* Safe prompt: `List the Python modules in this repository.`

### Negative

* Non-admin user id not included in `TELEGRAM_ADMIN_IDS`
* Workspace outside allowed roots such as `/outside/root/project`
* Symlink escape from allowed root to `/tmp/escaped`
* Read-only workspace path
* Restricted prompt: `Please run sudo systemctl restart nginx`
* Invalid config values: unknown rule group, unknown profile, unsafe default network mode
