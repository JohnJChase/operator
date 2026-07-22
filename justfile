build-pjsua:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p tools
    if [[ -x tools/pjsua ]]; then
      echo "tools/pjsua present"
      exit 0
    fi
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    git clone --depth 1 --branch 2.14.1 https://github.com/pjsip/pjproject.git "$work/pjproject"
    (
      cd "$work/pjproject"
      ./configure --disable-video --disable-libwebrtc CFLAGS="-O2"
      make dep
      make -j"$(nproc)"
    )
    cp "$work"/pjproject/pjsip-apps/bin/pjsua-*-unknown-linux-gnu tools/pjsua
    chmod +x tools/pjsua
    echo "installed tools/pjsua"

setup:
    # Pi needs system python3-lgpio for gpiozero (lgpio pin factory).
    uv venv --clear --system-site-packages
    uv sync --extra dev
    just setup-voices

setup-voices:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p voices/hfc_female
    dest="voices/hfc_female/en_US-hfc_female-medium.onnx"
    if [[ -f "$dest" ]]; then
        echo "voice present: $dest"
        exit 0
    fi
    proto="$HOME/we302-first-prototype/voices/hfc_female/en_US-hfc_female-medium.onnx"
    if [[ -f "$proto" ]]; then
        cp "$proto" "${proto}.json" voices/hfc_female/
        echo "copied voice from we302-first-prototype"
        exit 0
    fi
    echo "Missing $dest — place en_US-hfc_female-medium.onnx (+ .onnx.json) in voices/hfc_female/" >&2
    exit 1

run:
    uv run operator-os run

simulate *args:
    uv run operator-os simulate {{args}}

selftest:
    uv run operator-os selftest

status:
    uv run operator-os status

test:
    uv run pytest

test-hardware:
    uv run operator-os selftest --hardware

trace-hook:
    uv run operator-os trace-hook

trace-dial:
    uv run operator-os trace-dial

ring-test seconds="2":
    uv run operator-os ring-test --seconds {{seconds}}

audio-test tone="440" seconds="2":
    uv run operator-os audio-test --tone {{tone}} --seconds {{seconds}}

mic-test seconds="5":
    uv run operator-os mic-test --seconds {{seconds}}

speak-test text="This is the operator.":
    uv run operator-os speak-test --text "{{text}}"

crossbar-test:
    uv run operator-os crossbar-test

operator-test text="What is the weather?":
    uv run operator-os operator-test --text "{{text}}"

# Browser OAuth on this Pi → writes GOOGLE_OAUTH_REFRESH_TOKEN into .env
calendar-auth *args:
    uv run operator-os calendar-auth {{args}}

# Positional args (just does not use name=value for recipe params):
#   just sms-inject +12023061203 'desk test'
sms-inject sender="+15551234567" body="Test message":
    uv run operator-os sms-inject --from "{{sender}}" --text "{{body}}"

#   just sms-send +15551234567 'hello'
sms-send to="+15551234567" text="hello":
    uv run operator-os sms-send --to "{{to}}" --text "{{text}}"

refresh *args:
    uv run operator-os refresh {{args}}

lint:
    uv run ruff check .

format:
    uv run ruff format .
