#!/usr/bin/env bash
# =============================================================================
# Pre-commit validation hook
# =============================================================================
# Run before every commit to catch spec/impl drift.
#
# Setup once:
#   cp scripts/pre-commit-validate.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit
#
# Or run manually:
#   bash scripts/pre-commit-validate.sh

set -e

cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)"

echo "━━━ Running validation agents ━━━"

python -m engine.validation_agents

EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "⚠️  Validation failed. Options:"
    echo "  1. Fix the issues (see 💡 Fix hints above)"
    echo "  2. If intentional, update spec docs to match new behavior"
    echo "  3. To skip (emergency only): git commit --no-verify"
    exit 1
fi

echo "━━━ Running unit tests ━━━"
python -m pytest tests/ -q

echo ""
echo "✅ All validations passed"
