#!/usr/bin/env bash
# Install git hooks for the SRE assistant repository.
set -euo pipefail

HOOKS_DIR="$(git rev-parse --show-toplevel)/.git/hooks"

cat > "$HOOKS_DIR/pre-push" << 'HOOK'
#!/usr/bin/env bash
# Pre-push hook: run make check before pushing.
# Installed by: make hooks
set -euo pipefail

echo "Running make check before push..."
if ! make check; then
    echo ""
    echo "Pre-push hook FAILED — push blocked."
    echo "Fix the issues above, then try again."
    exit 1
fi
HOOK

chmod +x "$HOOKS_DIR/pre-push"
echo "Installed pre-push hook at $HOOKS_DIR/pre-push"
