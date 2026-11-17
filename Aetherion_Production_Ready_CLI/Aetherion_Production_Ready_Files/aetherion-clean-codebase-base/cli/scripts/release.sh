#!/usr/bin/env bash
# scripts/release.sh — Build and publish the Aetherion CLI
# Usage: ./scripts/release.sh [patch|minor|major]
set -euo pipefail

BUMP=${1:-patch}
PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Aetherion CLI Release Script"
echo "    Package dir: $PKG_DIR"
echo "    Bump type:   $BUMP"

cd "$PKG_DIR"

# 1. Verify clean working tree
if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: Working tree is dirty. Commit or stash changes first." >&2
  exit 1
fi

# 2. Run tests
echo "==> Running tests..."
python -m pytest tests/ -v --tb=short
echo "==> Tests passed."

# 3. Bump version (requires bump2version or hatch)
echo "==> Bumping version ($BUMP)..."
if command -v hatch &>/dev/null; then
  hatch version "$BUMP"
else
  echo "WARNING: hatch not found — update version in pyproject.toml manually." >&2
fi

NEW_VERSION=$(python -c "from importlib.metadata import version; print(version('aetherion'))" 2>/dev/null || grep '^version' pyproject.toml | head -1 | cut -d'"' -f2)
echo "    New version: $NEW_VERSION"

# 4. Build distribution
echo "==> Building distribution..."
python -m build --wheel --sdist
echo "==> Build complete: dist/"

# 5. Publish to PyPI (requires TWINE_USERNAME / TWINE_PASSWORD or ~/.pypirc)
if [[ "${PUBLISH:-false}" == "true" ]]; then
  echo "==> Publishing to PyPI..."
  python -m twine upload dist/aetherion-"${NEW_VERSION}"-*.whl dist/aetherion-"${NEW_VERSION}".tar.gz
  echo "==> Published."
else
  echo "==> Skipping publish (set PUBLISH=true to enable)."
fi

# 6. Tag
if command -v git &>/dev/null; then
  git add pyproject.toml
  git commit -m "chore: release v${NEW_VERSION}" || true
  git tag "v${NEW_VERSION}"
  echo "==> Tagged: v${NEW_VERSION}"
fi

echo "==> Release complete: v${NEW_VERSION}"
