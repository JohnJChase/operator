# Virtual SIP line + handset bridge

Softphone audio and the Western Electric handset are **different devices**.

| Device | Role |
|--------|------|
| `snd-aloop` (Loopback) | SIP “line” — pjsua always opens this via a private `.asoundrc` |
| USB handset (`plughw:…`) | Cradle mic/speaker — Piper/local services, and live calls only |
| `alsaloop` (`HandsetBridge`) | Joins Loopback ↔ handset **only** while a live SIP call is up |

## Why

On-hook features (voicemail) must not be able to light the handset. Stock pjsua
bridges its sound device into every answered call; if that device is the USB
card, voicemail inherits mic/speaker. Putting pjsua on a virtual loopback means
the cradle stays dark until we explicitly start the bridge.

## Boot

```bash
sudo cp deploy/modules-load.d/operator-aloop.conf /etc/modules-load.d/
sudo cp deploy/modprobe.d/operator-aloop.conf /etc/modprobe.d/
sudo modprobe snd-aloop
aplay -l | grep -i loop
```

`operator-os` also calls `ensure_loopback_card()` when inbound SIP starts (tries
`sudo -n modprobe` if the card is missing).

## Lifecycle

- **Voicemail / on-hook answer:** pjsua on Loopback; bridge **down**; OGM + record
  on the conference only.
- **Human answer / outbound / VM intercept:** `HandsetBridge.start()` then SIP
  media; hangup / on-hook → `HandsetBridge.stop()`.

Local digit services (news, desk, …) still use the USB device through
`AudioRouter` and do not start the bridge.
