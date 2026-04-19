# TGW WebUI Toggle Plan

Last updated: 2026-04-18
Status: In progress

## Goal

Add a chat-slot control path in the llm-manager dashboard that can:

1. Enable or disable the text-generation-webui Gradio interface for the chat lane.
2. Restart the chat engine so the setting takes effect.
3. Open the chat lane TGW WebUI in a new browser tab.

## Scope

- Chat slot only.
- TGW backend lane only.
- Persist settings in `secrets/.env`.
- Apply the TGW WebUI mode change on engine restart.
- Expose the launch URL in the dashboard.

## Constraints

- TGW decides whether to launch Gradio at process start, so this cannot be toggled without a restart.
- The current launcher always passes `--nowebui`.
- The current engine controls only expose generic `start|stop|restart` actions.
- Other local backends in this repo do not expose a comparable built-in web UI.

## Implementation Plan

### 1. Live plan and tracking

Status: Complete

- Create this document.
- Update the status of each phase as implementation proceeds.

### 2. Backend API and persisted settings

Status: Complete

- Add persisted TGW chat WebUI settings to the API knobs model and example env.
- Add chat-specific TGW WebUI request/response helpers.
- Add an API endpoint to enable or disable the TGW chat WebUI and restart the chat engine.
- Extend engine status payloads with TGW chat WebUI state, readiness, and launch URL.

### 3. TGW launcher changes

Status: Complete

- Teach the TGW launcher to read the persisted TGW chat WebUI settings from `secrets/.env`.
- Keep current API-only behavior when disabled.
- Omit `--nowebui` and add `--listen-port` when enabled.

### 4. Dashboard controls

Status: Complete

- Add chat card controls to enable or disable the TGW WebUI.
- Add a button to open the TGW WebUI in a new tab.
- Show TGW WebUI status and target URL in the chat engine card.
- Disable or hide controls when chat is not on the TGW backend.

### 5. Supporting docs

Status: Complete

- Document the new env keys.
- Document the new endpoint and dashboard behavior.
- Call out that a restart is required for the setting to take effect.
- Document optional use of a public URL override when direct port access is not desired.

### 6. Validation

Status: Complete

- Add or extend in-process smoke coverage for the TGW toggle path.
- Validate the launcher command construction in both disabled and enabled modes.
- Validate that the dashboard-facing status payload includes TGW WebUI data.

Smoke:
- `python run/tgw_webui_smoke.py`

## Completion Checklist

- [x] Plan document created.
- [x] Backend settings and endpoint added.
- [x] TGW launcher reads persisted WebUI config.
- [x] Dashboard toggle and launch controls added.
- [x] Docs updated.
- [x] Validation completed.

## Notes

- The first implementation path will use a direct launch URL with an optional public URL override.
- Apache proxying for a friendlier same-origin TGW path can be added later without changing the core toggle behavior.
