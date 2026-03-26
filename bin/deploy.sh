#!/usr/bin/env bash
# Deploy AIda to Vercel by syncing to the aida-klimatkalkyl repo.
#
# Usage: ./bin/deploy.sh [commit message]
#
# If no message is provided, uses a default with timestamp.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_REPO="https://github.com/henricbarkman/aida-klimatkalkyl.git"
DEPLOY_DIR="/tmp/aida-klimatkalkyl"

# Clone or pull deploy repo
if [ -d "$DEPLOY_DIR/.git" ]; then
    echo "Updating deploy repo..."
    git -C "$DEPLOY_DIR" pull --ff-only -q
else
    echo "Cloning deploy repo..."
    rm -rf "$DEPLOY_DIR"
    git clone -q "$DEPLOY_REPO" "$DEPLOY_DIR"
fi

# Sync source files
echo "Syncing files..."
rsync -a --delete \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    "$SRC_DIR/src/aida/" "$DEPLOY_DIR/src/aida/"

cp "$SRC_DIR/vercel.json" "$DEPLOY_DIR/"
cp "$SRC_DIR/requirements.txt" "$DEPLOY_DIR/"
cp "$SRC_DIR/api/index.py" "$DEPLOY_DIR/api/"

# Check for changes
cd "$DEPLOY_DIR"
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    echo "No changes to deploy."
    exit 0
fi

# Commit and push
MSG="${1:-Deploy from generalassistant $(date +%Y-%m-%d\ %H:%M)}"
git add -A
git commit -m "$MSG"
git push

echo "Pushed to aida-klimatkalkyl. Vercel will build automatically."
