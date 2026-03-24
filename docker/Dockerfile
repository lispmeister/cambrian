FROM python:3.14-slim

# Install git (needed by Test Rig for artifact inspection)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Create virtualenv — all pip installs (including entry.build) go here
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Install Test Rig dependencies
RUN pip install --no-cache-dir aiohttp pytest pytest-asyncio

# Bake in the Test Rig
COPY test-rig/test_rig.py /test-rig/test_rig.py

WORKDIR /workspace

# Default entrypoint: run the Test Rig
ENTRYPOINT ["python", "/test-rig/test_rig.py"]
