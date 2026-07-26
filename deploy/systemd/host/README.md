# Project-checkout host units

These files are the source for the legacy-named units installed on the current
2bananas host. They intentionally preserve the established names consumed by
the API and other operator tooling.

Install mapping:

- `llm-a.service` -> `/etc/systemd/system/llm-a.service`
- `llm-b.service` -> `/etc/systemd/system/llm-b.service`
- `llm-c.service` -> `/etc/systemd/system/llm-c.service`
- `llm-tgw-webui.service` -> `/etc/systemd/system/llm-tgw-webui.service`
- each `*.service.d/resources.conf` -> the matching systemd drop-in

`llm-embed.service` and `llm-manager-api.project.service` live one directory up.

Engine configuration errors exit with status 78. The units do not restart that
status. Other failures retain bounded restart recovery through `Restart=on-failure`,
backoff, and systemd start limits.
