# TODO: Pin to specific patch version once Python 3.14.0 final releases
FROM python:3.14-slim

# Never write .pyc files — the container is ephemeral, bytecode caching has no benefit,
# and it prevents __pycache__ from leaking into the bind-mounted artifact workspace.
ENV PYTHONDONTWRITEBYTECODE=1

# Install git (needed by Test Rig for artifact inspection)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Install uv — used for all dependency installation (host parity, 10-100x faster than pip)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Create virtualenv via uv — all installs (including entry.build) go here
RUN uv venv /venv
ENV PATH="/venv/bin:$PATH"

# Install Test Rig dependencies + organism core deps (pre-installed so minor requirements.txt
# issues don't kill the build; every viable generation needs these packages)
RUN uv pip install --no-cache-dir aiohttp pytest pytest-asyncio pytest-aiohttp pyyaml structlog anthropic pydantic

# Create a non-root user — the Test Rig should not run as root
RUN useradd -m -s /bin/bash cambrian && chown -R cambrian:cambrian /venv

# Bake in the Test Rig
COPY test-rig/ /test-rig/

WORKDIR /workspace

# Ensure intra-package imports (from src.loop import ...) work regardless of invocation form.
# The manifest validation gate catches script-form start commands at Stage 1; this is a
# belt-and-suspenders fallback for any import edge cases that slip through.
ENV PYTHONPATH=/workspace

USER cambrian

# Default entrypoint: run the Test Rig
ENTRYPOINT ["python", "/test-rig/test_rig.py"]
