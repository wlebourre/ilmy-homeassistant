#!/usr/bin/env bash
#
# install-hooks.sh — wire up the repo-local git hooks (gitleaks pre-commit)
# and verify a gitleaks binary is available.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

git config core.hooksPath .githooks
echo "✓ core.hooksPath set to .githooks (pre-commit hook active)"

if command -v gitleaks >/dev/null 2>&1; then
    echo "✓ gitleaks found: $(command -v gitleaks) (v$(gitleaks version))"
elif [ -x "$REPO_ROOT/../audit/tools/bin/gitleaks" ]; then
    echo "✓ gitleaks found: $REPO_ROOT/../audit/tools/bin/gitleaks (v$("$REPO_ROOT/../audit/tools/bin/gitleaks" version))"
else
    echo "✗ gitleaks NOT found in PATH or ../audit/tools/bin."
    echo "  The pre-commit hook will BLOCK commits until it is installed."
    echo "  Install with: brew install gitleaks"
    exit 1
fi
