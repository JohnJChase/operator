# SMS (Block 4)

Telnyx Messaging + local webhook. SQLite: `data/operator.sqlite3`.

Public inbound uses **Tailscale Funnel** on this Pi (Telnyx cannot reach
tailnet-only Serve URLs).

## Portal + Tailscale

1. Create a **Messaging profile** and assign your DID (SMS enabled; same number as voice is fine).
2. Join this Pi to your tailnet (`sudo tailscale up`), then expose the webhook
   **once** (persists in Tailscale node state across reboot with `tailscaled`):

   ```bash
   # operator-os must listen on 127.0.0.1:8787 (systemd or just run)
   sudo tailscale funnel --bg 8787
   ```

   Current Funnel host: `https://operator.tail2276c3.ts.net/`

   Telnyx messaging profile webhook:
   `https://operator.tail2276c3.ts.net/webhooks/telnyx/sms`

   Reboot persistence for Funnel + `operator-os` is documented in
   [systemd.md](systemd.md) (enable the unit when doing the appliance package).

3. Copy **Public Key** (Keys & Credentials) into `TELNYX_PUBLIC_KEY` so signatures are verified.
4. Paid/funded account for outbound beyond trial limits.

## `.env`

```bash
TELNYX_API_KEY=...
TELNYX_CALLER_ID=+1...          # voice + default SMS from
TELNYX_MESSAGING_PROFILE_ID=... # if required by your profile
# TELNYX_SMS_FROM=+1...         # only if different from caller ID
TELNYX_PUBLIC_KEY=...           # base64 ed25519 public key
OPERATOR_SMS_WEBHOOK_PORT=8787
```

## Behavior

- **Inbound:** webhook upserts row (idempotent on Telnyx message id). If the phone is on-hook idle → ring; answer → speak “Message from …”; no answer → stay queued. Busy (off-hook / SIP / desk) → queue only.
- **Outbound:** info desk `prepare_message(to, text)` → `confirm_message(true)` → Telnyx send. Bodies are not written to EventLog JSONL.
- **Menu:** digit 0 mentions unheard count when any exist.
- Desk tools: `list_messages`, `read_message`.

## Local test (no Telnyx)

With `just run` up:

```bash
just sms-inject +15551234567 'Testing the desk'
# or:
uv run operator-os sms-inject --from +15551234567 --text "Testing the desk"
```

Outbound smoke:

```bash
uv run operator-os sms-send --to +15551234567 --text "hello"
```
