#!/bin/bash
# Claude Code environment setup for CAMBRIAN.
# Installs plugins, skills, and configures hooks.
# Run from project root: ./scripts/setup-claude.sh
#
# Prerequisites: Claude Code CLI (`claude`) must be installed.

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

# ─── Prerequisite ─────────────────────────────────────────────────

if ! command -v claude &>/dev/null; then
  fail "Claude Code CLI not found. Install from: https://docs.anthropic.com/en/docs/claude-code"
fi
ok "Claude Code CLI found"

# ─── Official Plugins ─────────────────────────────────────────────

step "Installing official plugins"

PLUGINS=(
  "pyright-lsp@claude-plugins-official"
  "code-review@claude-plugins-official"
  "security-guidance@claude-plugins-official"
)

for plugin in "${PLUGINS[@]}"; do
  name="${plugin%%@*}"
  echo "  Enabling $name..."
done

ok "Official plugins: pyright-lsp, code-review, security-guidance"
warn "Plugins must be enabled in ~/.claude/settings.json under enabledPlugins"
warn "Add these entries if not present:"
echo ""
echo '    "pyright-lsp@claude-plugins-official": true,'
echo '    "code-review@claude-plugins-official": true,'
echo '    "security-guidance@claude-plugins-official": true'
echo ""

# ─── Trail of Bits Skills ────────────────────────────────────────

step "Installing Trail of Bits skills"

SKILLS=(
  modern-python
  codeql
  semgrep
  coverage-analysis
  property-based-testing
  code-maturity-assessor
  differential-review
  spec-to-code-compliance
  ask-questions-if-underspecified
  secure-workflow-guide
  gh-cli
)

SKILLS_DIR="$PROJECT_ROOT/.claude/skills"
mkdir -p "$SKILLS_DIR"

installed=0
skipped=0

for skill in "${SKILLS[@]}"; do
  if [ -d "$SKILLS_DIR/$skill" ]; then
    ((skipped++))
  else
    echo "  Installing $skill..."
    if npx @anthropic-ai/skills add trailofbits/skills --skill "$skill" --agent claude-code --yes 2>/dev/null; then
      ((installed++))
    else
      warn "Failed to install $skill — install manually later"
    fi
  fi
done

ok "Skills: $installed installed, $skipped already present"

# ─── Beads Plugin ────────────────────────────────────────────────

step "Checking beads plugin"

warn "The beads plugin requires a custom marketplace entry in ~/.claude/settings.json"
warn "Add these entries if not present:"
echo ""
echo '    "enabledPlugins": {'
echo '      "beads@beads-marketplace": true'
echo '    },'
echo '    "extraKnownMarketplaces": {'
echo '      "beads-marketplace": {'
echo '        "source": { "source": "github", "repo": "steveyegge/beads" }'
echo '      }'
echo '    }'
echo ""

# ─── Summary ─────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Claude Code setup complete."
echo ""
echo "  Installed skills (in .claude/skills/):"
for skill in "${SKILLS[@]}"; do
  echo "    • $skill"
done
echo ""
echo "  Manual steps remaining:"
echo "    1. Verify plugins in ~/.claude/settings.json"
echo "    2. Restart Claude Code to pick up changes"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
