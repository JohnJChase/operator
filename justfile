setup:
    # Pi needs system python3-lgpio for gpiozero (lgpio pin factory).
    uv venv --clear --system-site-packages
    uv sync --extra dev

run:
    uv run operator-os run

simulate:
    uv run operator-os simulate

selftest:
    uv run operator-os selftest

test:
    uv run pytest

test-hardware:
    uv run operator-os selftest --hardware

lint:
    uv run ruff check .

format:
    uv run ruff format .
