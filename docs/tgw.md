# TGW WebUI Standalone Plan

Last updated: 2026-05-09
Status: Implemented

## Goal

Run TGW WebUI as a standalone utility service for one-off model conversations,
independent from chat/intent/small slot lifecycle.

## What changed

1. TGW WebUI is no longer controlled by chat backend selection.
2. Slot launch path is explicitly decoupled from TGW WebUI:
   - `run/engine_launcher.py` now forwards `--no-webui` by default.
   - Slot services stay API-only unless intentionally launched with `--webui`.
3. TGW WebUI has standalone service endpoints:
	- `GET /engines/tgw-webui/status`
	- `POST /engines/tgw-webui/{start|stop|restart}`
	- `GET /engines/tgw-webui/logs`
	- `POST /engines/tgw-webui/config`
4. `/engines/status` now includes top-level `tgw_webui` state.
5. Dashboard now has a dedicated TGW WebUI card with Start/Stop/Restart/Logs/Open actions.

## Runtime model

- Standalone service unit name is configurable via `SYSTEMD_TGW_WEBUI`.
- Default unit fallback: `llm-tgw-webui.service`.
- TGW WebUI launch URL defaults to `http://<host>:7860/` unless overridden.

## Recommended systemd unit (example)

Create `llm-tgw-webui.service` with a fixed testing model alias such as `webui_active_model`:

```ini
[Unit]
Description=Standalone TGW WebUI (one-off testing)
After=network.target

[Service]
Type=simple
WorkingDirectory=/srv/2bananas/engines/text-generation-webui
Environment=TGW_WEBUI_ENABLED=1
ExecStart=/usr/bin/python3 /srv/2bananas/projects/llm-manager/run/launch_tgw.py --api-port 7861 --model webui_active_model --max-seq-len 8192 --listen-host 127.0.0.1 --webui
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Notes:
- Keep the TGW OpenAI API port separate from normal chat/intent/small lanes.
- TGW WebUI itself uses `TGW_WEBUI_PORT` (default `7860`).
- Point `SYSTEMD_TGW_WEBUI` to this service name if you use a different unit id.
- If you launch through `run/engine_launcher.py`, pass `--webui` for standalone WebUI mode. Without it, launcher forces `--no-webui`.

## Environment keys

- `TGW_WEBUI_ENABLED`
- `TGW_WEBUI_PORT`
- `TGW_WEBUI_BIND_HOST`
- `TGW_WEBUI_PUBLIC_URL`

Legacy `TGW_CHAT_WEBUI_*` keys are still accepted as fallback aliases.

These keys apply when TGW is started in WebUI mode (`--webui` or legacy env fallback when not explicitly overridden). Slot launches through `run/engine_launcher.py` default to API-only (`--no-webui`).

## Validation

Smoke script:
- `python run/tgw_webui_smoke.py`

Coverage:
1. `/engines/status` returns top-level `tgw_webui` payload.
2. `/engines/tgw-webui/config` updates bind/port settings.
3. `/engines/tgw-webui/{start|stop|restart}` service actions work.
