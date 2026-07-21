# SIP outside line (digit 9)

Telnyx credential SIP trunk via bundled `tools/pjsua`.

## Portal checklist

1. SIP connection type **Credentials** (name e.g. `we302`)
2. Outbound voice profile linked to that connection
3. A Telnyx number assigned (caller ID)
4. API key + connection ID + SIP username/password in `.env`

```bash
TELNYX_API_KEY=KEY_...
TELNYX_CONNECTION_ID=...
TELNYX_SIP_USER=...
TELNYX_SIP_PASSWORD=...
TELNYX_CALLER_ID=+1...   # optional
```

## Handset flow

1. Dial **9** → crossbar seize + external dial tone (`OUTSIDE_LINE`)
2. Dial the destination (10-digit NANP or 11-digit `1…`)
3. After interdigit silence (`outside_number_interdigit_timeout_ms`, default 2000) → place call
4. Hang up → kills `pjsua` immediately (hardware cutoff)

## pjsua binary

```bash
just build-pjsua
# installs tools/pjsua from pjproject 2.14.1
```

Requires `libasound2-dev` and `libssl-dev` on the Pi.

## Test without the full phone loop

```bash
# With creds in .env and handset ALSA free:
tools/pjsua \
  --id=sip:$TELNYX_SIP_USER@sip.telnyx.com \
  --registrar=sip:sip.telnyx.com \
  --realm='*' \
  --username=$TELNYX_SIP_USER \
  --password=$TELNYX_SIP_PASSWORD \
  --use-srtp=0 --srtp-secure=0 \
  "sip:+17035551212@sip.telnyx.com"
```

Hang up in the pjsua CLI with `h` then `q`.
