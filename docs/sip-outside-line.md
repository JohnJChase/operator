# SIP outside line (digit 9) and inbound ring

Telnyx credential SIP trunk via bundled `tools/pjsua`.

## Portal checklist

1. SIP connection type **Credentials** (name e.g. `we302`)
2. Outbound voice profile linked to that connection
3. A Telnyx number **assigned to that connection** (caller ID + inbound DID)
4. API key + connection ID + SIP username/password + caller ID in `.env`
5. **Trial / level-1 accounts:** add each *outbound* destination under
   Mission Control → *Verified Numbers* (or upgrade). Unverified destinations
   get `403 Can not make calls to non-verified numbers`.
6. Inbound works once the DID is on the connection and the phone is registered
   (on-hook idle). Call the Telnyx number from your mobile to test.

```bash
TELNYX_API_KEY=KEY_...
TELNYX_CONNECTION_ID=...
TELNYX_SIP_USER=...
TELNYX_SIP_PASSWORD=...
TELNYX_CALLER_ID=+1...   # required — Telnyx number on the connection
```

## Handset flow — outbound

1. Dial **9** → crossbar seize + external dial tone (`OUTSIDE_LINE`)
2. Dial the destination (10-digit NANP or 11-digit `1…`)
3. After interdigit silence (`outside_number_interdigit_timeout_ms`, default 3500;
   timer resets on every pulse so slow rotary digits are not cut off) → place call
4. Hang up → kills `pjsua` immediately (hardware cutoff)

## Handset flow — inbound

1. On-hook idle → softphone **REGISTERs** to Telnyx (SIP username as AOR)
2. Inbound INVITE → mechanical ring (`INCOMING_RINGING`, max
   `inbound_ring_timeout_ms`, default 45s)
3. Lift handset → answer → `SIP_CALL`
4. Hang up or remote BYE → idle and re-register
5. Lifting the handset for dial tone **unregisters** until you hang up again
   (frees the SIP port for outbound)

## Behavior notes

- **Outbound** uses digest auth on INVITE (`--outbound`); From =
  `TELNYX_CALLER_ID`. No REGISTER (Telnyx rejects REGISTER when `--id` is the DID).
- **Inbound** REGISTERs over **TCP** (`sip:sip.telnyx.com;transport=tcp`) with
  `--id=sip:$TELNYX_SIP_USER@…` and `--auto-answer=180` (provisional Ringing so
  the PSTN caller hears ringback; lift still answers with 200).
- TCP lets Telnyx reuse the NAT connection for INVITEs (UDP alone often yields
  “number not in service” behind home NAT).
- Only one of inbound/outbound runs at a time (both use local port **5080**).
- Last pjsua log is copied to `/tmp/operator-last-pjsua.log` on hangup.

## Test without the full phone loop

Outbound:

```bash
tools/pjsua \
  --id=sip:$TELNYX_CALLER_ID@sip.telnyx.com \
  --outbound=sip:sip.telnyx.com \
  --realm='*' \
  --username=$TELNYX_SIP_USER \
  --password=$TELNYX_SIP_PASSWORD \
  --local-port=5080 \
  --use-srtp=0 --srtp-secure=0
# then: m   and   sip:+17035551212@sip.telnyx.com
```

Inbound (register over TCP and wait; answer with `a`):

```bash
tools/pjsua \
  --id=sip:$TELNYX_SIP_USER@sip.telnyx.com \
  --registrar='sip:sip.telnyx.com;transport=tcp' \
  --realm='*' \
  --username=$TELNYX_SIP_USER \
  --password=$TELNYX_SIP_PASSWORD \
  --local-port=5080 \
  --reg-timeout=180 \
  --auto-answer=180 \
  --use-srtp=0 --srtp-secure=0
```
