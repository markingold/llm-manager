<!--
id: RB-ENGINES-LAYOUT
version: 1.0
last_updated: 2026-05-17
title: Canonical /srv/2bananas/engines Layout And Migration Runbook
purpose:
  Define the source-of-truth filesystem layout for llm-manager engine and model
  assets, then provide a safe migration and validation checklist.
-->
# Canonical /srv/2bananas/engines Layout And Migration Runbook

## Purpose

Use this runbook to keep llm-manager as the control plane while engine installs,
runtime paths, and model assets stay canonical under `/srv/2bananas/engines`.

## Quick reference

- llm-manager repo (control plane): `/srv/2bananas/projects/llm-manager`
- canonical engines root: `/srv/2bananas/engines`
- canonical shared model storage: `/srv/2bananas/engines/models`
- effective runtime model directory: `/srv/2bananas/engines/text-generation-webui/user_data/models`
- active slot symlinks:
  - `/srv/2bananas/engines/text-generation-webui/user_data/models/chat_active_model`
  - `/srv/2bananas/engines/text-generation-webui/user_data/models/intent_active_model`
  - `/srv/2bananas/engines/text-generation-webui/user_data/models/small_active_model`
- systemd slot units: `/etc/systemd/system/llm-a.service`, `/etc/systemd/system/llm-b.service`, `/etc/systemd/system/llm-c.service`
- API unit: `/etc/systemd/system/llm-manager-api.service`

## Canonical layout

```text
/srv/2bananas/engines/
  models/                                # source-of-truth model storage
  text-generation-webui/
    user_data/models/                    # runtime-facing model dir for slot links
      chat_active_model -> /srv/2bananas/engines/models/<model_dir>
      intent_active_model -> /srv/2bananas/engines/models/<model_dir>
      small_active_model -> /srv/2bananas/engines/models/<model_dir>
  exllamav2/                             # optional source/toolchain checkout
  vllm-env/                              # optional backend-specific venv
  <other backend assets>/                # optional, still under /srv/2bananas/engines
```

Policy:

- Keep large engine installs and model weight stores under `/srv/2bananas/engines`.
- Keep llm-manager repo focused on API, router logic, metadata/state, and UI.
- Do not create a second unmanaged model-weight store under the llm-manager repo.

## Migration procedure

### 1) Preflight inventory

```bash
sudo systemctl status llm-a llm-b llm-c llm-manager-api --no-pager
ls -la /srv/2bananas/engines
ls -la /srv/2bananas/engines/text-generation-webui/user_data/models
```

### 2) Confirm unit wiring points at launcher wrappers

```bash
sudo systemctl cat llm-a llm-b llm-c | rg 'ExecStart|WorkingDirectory'
sudo systemctl cat llm-manager-api | rg 'SERVER_MODELS_DIR|MODELS_DIR|SYSTEMD_LLM_'
```

Expected:

- slot units launch through `run/engine_launcher.py`
- API unit sets `SERVER_MODELS_DIR` and `MODELS_DIR` to runtime models directory under `/srv/2bananas/engines/text-generation-webui/user_data/models`

### 3) Stage canonical shared model storage

If models already live under `/srv/2bananas/engines/models`, skip this step.

```bash
sudo mkdir -p /srv/2bananas/engines/models
# Replace <OLD_MODEL_STORE> with current non-canonical source path
sudo rsync -a --info=progress2 <OLD_MODEL_STORE>/ /srv/2bananas/engines/models/
```

### 4) Repoint slot symlinks to canonical model targets

Preferred path is API-managed switching:

```bash
curl -X POST http://localhost:8101/switch \
  -H 'Content-Type: application/json' \
  -d '{"mode":"chat","model_dir":"<model_dir>","bounce":true}' | python3 -m json.tool
```

Verify links:

```bash
readlink -f /srv/2bananas/engines/text-generation-webui/user_data/models/chat_active_model
readlink -f /srv/2bananas/engines/text-generation-webui/user_data/models/intent_active_model
readlink -f /srv/2bananas/engines/text-generation-webui/user_data/models/small_active_model
```

Each target should resolve under `/srv/2bananas/engines/models`.

### 5) Restart and validate sequentially

```bash
sudo systemctl restart llm-manager-api
sudo systemctl restart llm-a
curl "http://localhost:8101/test-chat?q=layout-check" | python3 -m json.tool

sudo systemctl restart llm-b
curl "http://localhost:8101/test-intent?q=layout-check" | python3 -m json.tool

sudo systemctl restart llm-c
curl "http://localhost:8101/test-util?q=layout-check" | python3 -m json.tool
```

### 6) Final contract checks

```bash
curl http://localhost:8101/models | python3 -m json.tool
curl http://localhost:8101/engines/status | python3 -m json.tool
```

Confirm:

- active models match intended slot symlinks
- slot endpoints and slot backends resolve as expected
- no duplicate runtime model storage outside `/srv/2bananas/engines`

## Verification checklist

- [ ] slot units use `run/engine_launcher.py`
- [ ] API unit points `SERVER_MODELS_DIR` and `MODELS_DIR` to `/srv/2bananas/engines/text-generation-webui/user_data/models`
- [ ] active slot symlinks resolve to `/srv/2bananas/engines/models/*`
- [ ] `/models` and `/engines/status` agree on active slot state
- [ ] chat, intent, and small probe endpoints pass after sequential restarts

## Pitfalls

- Editing symlinks manually while operators are also switching via API can cause drift.
- Restarting all lanes simultaneously during migration can hide slot-specific issues.
- Copying model directories without preserving symlink targets can break active slot links.

## Related

- `docs/plans/BACKEND_ARCHITECTURE_PLAN.md`
- `docs/CLI_SYSOP_GUIDE.md`
- `docs/API.md`
- `docs/plans/NEXT_PUSH.md`