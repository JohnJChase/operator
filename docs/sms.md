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

- **Inbound:** webhook upserts row (idempotent on Telnyx message id). If the phone is on-hook idle → **quick double-ring**, then a quiet pickup window (`sms_pickup_window_ms`, default 30s). Answer in that window → speak “Message from …”, then “Flash to reply, or hang up.” (dictate → hear draft → flash to send). Miss the window → stay queued for digit **5**. Busy (off-hook / SIP / desk) → queue only.
- **Outbound:** info desk `prepare_message(to, text)` → `confirm_message(true)` → Telnyx send. Bodies are not written to EventLog JSONL.
- **MWI:** any unheard SMS or voicemail → stutter dial tone on off-hook (`timing.mwi_stutter_ms`), then continuous dial.
- **Menu:** digit 0 says “N waiting messages” (SMS+VM) and “dial 5 for messages.” Digit **5** is the universal inbox (SMS + voicemail, oldest first); after each SMS, same flash-to-reply flow.
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
