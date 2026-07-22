# systemd unit for WE302 Operator OS

Install on the Pi (paths assume checkout at `/home/john/operator`):

```bash
sudo cp deploy/operator-os.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now operator-os.service
sudo systemctl status operator-os.service
```

`--config` must come **before** the `run` subcommand (see unit `ExecStart`).

## Persistence checklist (reboot-safe appliance)

| Piece | Who owns it | Persist how |
|-------|-------------|-------------|
| `operator-os` phone loop + SMS webhook `:8787` | systemd | `enable --now operator-os.service` |
| Tailscale / MagicDNS | `tailscaled` | installed enabled; `tailscale up` once |
| Public SMS HTTPS | Tailscale **Funnel** | `sudo tailscale funnel --bg 8787` once — stored in Tailscale node state, restored when `tailscaled` starts |
| Secrets | `.env` | on disk (gitignored), not in the unit file |

Funnel is **not** a separate systemd unit. After Funnel is configured once, reboot should keep:

`https://operator.tail2276c3.ts.net/` → `http://127.0.0.1:8787`

Telnyx webhook path: `https://operator.tail2276c3.ts.net/webhooks/telnyx/sms`

Verify after reboot:

```bash
systemctl is-active operator-os.service tailscaled
tailscale funnel status
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/webhooks/telnyx/sms
```

(POST-only webhook may return 404/405 on bare GET — the check is that something is listening.)

## Behavior

- Starts after sound + network; ordered after `tailscaled` so Funnel is ready.
- `WorkingDirectory` is the repo root so `config/` and `data/` resolve.
- Uses project `.venv` via `uv run`.
- On stop/restart, systemd sends SIGTERM; the app idles to on-hook via cleanup.
- Ring GPIO is initialized **off** (`OutputDevice(..., initial_value=False)`).

## Logs

```bash
journalctl -u operator-os.service -f
```

App events also append to `data/events.jsonl` (bounded; see `EventLog`).
Do not put API keys in the unit file — use `.env` in the repo (gitignored).

## Unclean restart

After reboot or crash, the next start opens GPIO with ring off, loads the
profile, and enters `ON_HOOK_IDLE` until the handset is lifted.
