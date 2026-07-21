#!/bin/sh
# Point git at the repo-tracked hooks in scripts/hooks (currently: pre-push,
# the local eval gate). Run once per clone, from the repo root.
set -e
git config core.hooksPath scripts/hooks
echo "core.hooksPath set to scripts/hooks - tracked git hooks are now active."
echo "bypass a single push with: git push --no-verify"
