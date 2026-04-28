---
name: deploy-ems
description: Deploy the EMS Home Assistant custom integration to the remote HA instance via scp. Triggers on "deploy ems", "deploy the integration", "push to home assistant", "install ems on ha", "test ems on ha", "ship ems to ha", and post-edit flows where the user wants to test changes live. Runs the full pytest suite locally first and aborts the deploy if any test fails — no remote changes made on test failure. Wipes /config/custom_components/ems/ on the remote first (matching HACS update behavior — no stale .py or __pycache__), copies the source tree, runs ha core check, restarts HA, waits, then tails logs for ems-related errors. Silent deploy: no confirmation prompt; only stops if pytest fails or ha core check fails (to protect the running instance).
allowed-tools: PowerShell
---

# Deploy EMS to Home Assistant

## Purpose

Copy the `custom_components/ems/` tree from the repo into `/config/custom_components/ems/` on the Home Assistant instance, then restart HA and verify the integration loaded without errors.

## Workflow

### 1. Verify source

Use the **Glob tool** to check that `custom_components/ems/manifest.json` exists in the repo root. If it does not, abort:

> EMS source not found — expected `custom_components/ems/` in the repo root.

### 2. Run local pytest gate

```powershell
pytest
```

This inherits all settings from `pyproject.toml` (`testpaths = ["tests"]`, `--cov=custom_components.ems --cov-branch`, 80% coverage gate via `[tool.coverage.report] fail_under = 80`).

- Exit code 0 → proceed to Step 3.
- Any non-zero exit code → **abort immediately. Do not make any remote calls.** Surface the full pytest output (failing test names + tracebacks) and report:

  > STATUS: failed — pytest gate failed. Deploy aborted before any remote changes; HA was not touched. Fix the failing tests and re-run.

- If `pytest` is not installed (command not found / ModuleNotFoundError) → abort with:

  > STATUS: failed — pytest not installed. Run `pip install -e ".[dev]"` in the repo root, then re-run the deploy.

This step runs before the remote wipe, so a test failure leaves the previously-deployed version on HA untouched.

### 3. Resolve connection details

Defaults (from the `home-assistant-manager` skill):

| Setting | Default |
|---|---|
| SSH host | `root@192.168.0.12` |
| Remote path | `/config/custom_components/` |

Override only if the user specified a different host or path in their request.

### 4. Wipe remote integration directory

```powershell
ssh root@192.168.0.12 "rm -rf /config/custom_components/ems"
```

This matches HACS update behavior — clean replace ensures no stale `.py`, `.pyc`, or `__pycache__/` entries survive from a previous deploy.

### 5. Copy source tree

```powershell
scp -r custom_components/ems root@192.168.0.12:/config/custom_components/
```

Check exit code. If non-zero, abort and report:

> STATUS: failed — scp exited with code <N>. Check SSH connectivity and that /config/custom_components/ exists on the remote.

### 6. Validate configuration

```powershell
ssh root@192.168.0.12 "ha core check"
```

If exit code is non-zero, **abort before restarting**. Surface the full stderr. The previous version remains running.

> STATUS: failed — ha core check failed (see output above). HA was NOT restarted; previous version is still active.

### 7. Restart Home Assistant

```powershell
ssh root@192.168.0.12 "ha core restart"
```

Custom integrations require a full restart — they cannot be reloaded.

### 8. Wait for HA to come back up

```powershell
Start-Sleep -Seconds 30
```

### 9. Tail logs and check for errors

Run both in sequence:

```powershell
ssh root@192.168.0.12 "ha core logs | grep -i 'ems' | tail -30"
```

```powershell
ssh root@192.168.0.12 "ha core logs | grep -iE 'error|traceback' | tail -20"
```

Scan output for lines mentioning `custom_components.ems` or `homeassistant.components.ems`. Surface any ERROR or traceback lines.

### 10. Report

| Outcome | Report |
|---|---|
| All steps succeeded, no errors in logs | `STATUS: ok — EMS deployed and running. HA restarted successfully.` |
| Any step failed | `STATUS: failed — <step name>: <captured stderr/error>` |
| Errors found in logs | `STATUS: ok (with warnings) — EMS loaded but errors detected in logs:` followed by the relevant lines |
