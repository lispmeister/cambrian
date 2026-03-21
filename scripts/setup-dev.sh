#!/bin/bash
# Cambrian development environment setup.
# Idempotent — safe to run multiple times.
# Run from project root: ./scripts/setup-dev.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}!${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }
step() { echo -e "\n${GREEN}▸${NC} $1"; }

# ─── Prerequisites ──────────────────────────────────────────────

step "Checking prerequisites"

# Python 3.14t (free-threaded)
if command -v python3.14t &>/dev/null; then
  ok "python3.14t found: $(python3.14t --version)"
elif command -v python3.14 &>/dev/null; then
  warn "python3.14 found but not the free-threaded build (python3.14t)"
  warn "Install free-threaded Python 3.14: https://docs.python.org/3/howto/free-threading-python.html"
elif command -v python3 &>/dev/null; then
  warn "python3 found: $(python3 --version) — project requires Python 3.14t"
else
  fail "Python not found. Install Python 3.14t (free-threaded build)."
fi

# Docker
if command -v docker &>/dev/null; then
  if docker info &>/dev/null; then
    ok "Docker is running"
  else
    warn "Docker is installed but daemon is not running. Start Docker Desktop."
  fi
else
  fail "Docker not found. Install from https://docker.com"
fi

# Git
if command -v git &>/dev/null; then
  ok "git found: $(git --version | head -1)"
else
  fail "git not found"
fi

# ─── uv ─────────────────────────────────────────────────────────

step "Setting up uv (package manager)"

if command -v uv &>/dev/null; then
  ok "uv found: $(uv --version)"
else
  warn "uv not found. Installing..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  ok "uv installed: $(uv --version)"
fi

# ─── Virtual environment and dependencies ────────────────────────

step "Setting up virtual environment and dependencies"

if [ -f pyproject.toml ]; then
  uv sync
  ok "Dependencies installed via uv sync"
else
  warn "No pyproject.toml found — skipping dependency install"
  warn "This will be created during Phase 0 (bootstrap)"
fi

# ─── Git hooks ──────────────────────────────────────────────────

step "Installing git hooks"

mkdir -p .git/hooks

cat > .git/hooks/commit-msg << 'HOOK'
#!/bin/bash
# Enforce bead reference in every commit message.
# Format: "bd-NNN: description" or "bd-NNN description" anywhere in the message.
# Co-authored-by lines and merge commits are exempt.

MSG=$(cat "$1")

# Allow merge commits
if echo "$MSG" | head -1 | grep -qE '^Merge '; then
  exit 0
fi

if ! echo "$MSG" | grep -qE 'bd-[0-9]+'; then
  echo ""
  echo "ERROR: Commit message must reference a bead (bd-NNN)"
  echo ""
  echo "  Create one with:  bd create --title=\"...\" --type=task"
  echo "  List ready work:  bd ready"
  echo ""
  echo "  Your message was:"
  echo "  $MSG"
  echo ""
  exit 1
fi
HOOK
chmod +x .git/hooks/commit-msg
ok "commit-msg hook installed (enforces bead references)"

# ─── Beads ──────────────────────────────────────────────────────

step "Setting up beads (issue tracking)"

if command -v bd &>/dev/null; then
  ok "bd found: $(bd version 2>/dev/null | head -1 || echo 'installed')"
  if [ ! -d .beads ]; then
    warn "Beads not initialized. Run: bd init"
  else
    ok "Beads already initialized"
  fi
else
  warn "bd (beads) not found. Install from: https://github.com/steveyegge/beads"
fi

# ─── .env ───────────────────────────────────────────────────────

step "Checking environment"

if [ -f .env ]; then
  if grep -q "ANTHROPIC_API_KEY" .env; then
    ok ".env exists with ANTHROPIC_API_KEY"
  else
    warn ".env exists but ANTHROPIC_API_KEY not found. Add it:"
    warn "  echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env"
  fi
else
  warn "No .env file. Create one with your API key:"
  warn "  echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env"
fi

# ─── Claude Code setup ──────────────────────────────────────────

step "Claude Code environment"

if command -v claude &>/dev/null; then
  ok "Claude Code CLI found"
  echo "  Run ./scripts/setup-claude.sh to install plugins and skills"
else
  warn "Claude Code CLI not found — skip this if you're not using Claude Code"
fi

# ─── Docker image ───────────────────────────────────────────────

step "Docker image"

if [ -f docker/Dockerfile ]; then
  if docker image inspect cambrian-base &>/dev/null; then
    ok "cambrian-base image exists"
  else
    warn "cambrian-base image not built. Run: ./docker/build.sh"
  fi
else
  warn "No Dockerfile yet — will be created during Phase 0 (bootstrap)"
fi

# ─── Summary ────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Setup complete. Next steps:"
echo ""
echo "  1. Ensure .env has ANTHROPIC_API_KEY"
echo "  2. Run ./scripts/setup-claude.sh (if using Claude Code)"
echo "  3. Start working: bd ready"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
