# systemd unit for WE302 Operator OS

Install on the Pi (paths assume checkout at `/home/john/operator`):

```bash
sudo cp deploy/operator-os.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now operator-os.service
sudo systemctl status operator-os.service
```

## Behavior

- Starts after `multi-user.target` / sound device available.
- `WorkingDirectory` is the repo root so `config/` and `data/` resolve.
- Uses project `.venv` via `uv run` (or the venv binary — see unit file).
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
