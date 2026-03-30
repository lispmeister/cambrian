# TODO: Pin to specific patch version once Python 3.14.0 final releases
FROM python:3.14-slim

# Never write .pyc files — the container is ephemeral, bytecode caching has no benefit,
# and it prevents __pycache__ from leaking into the bind-mounted artifact workspace.
ENV PYTHONDONTWRITEBYTECODE=1

# Install git (needed by Test Rig for artifact inspection)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Create virtualenv — all pip installs (including entry.build) go here
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Install Test Rig dependencies
RUN pip install --no-cache-dir aiohttp pytest pytest-asyncio pytest-aiohttp

# Create a non-root user — the Test Rig should not run as root
RUN useradd -m -s /bin/bash cambrian

# Bake in the Test Rig
COPY test-rig/test_rig.py /test-rig/test_rig.py

WORKDIR /workspace

USER cambrian

# Default entrypoint: run the Test Rig
ENTRYPOINT ["python", "/test-rig/test_rig.py"]
