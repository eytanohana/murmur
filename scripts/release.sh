#!/usr/bin/env bash
# One-command release: bump the version, commit, tag, and push.
# The pushed tag triggers .github/workflows/release.yml, which verifies the
# tag matches pyproject.toml, runs CI, publishes to PyPI, and creates the
# GitHub Release.
#
# Usage:
#   ./scripts/release.sh patch|minor|major   # bump, commit, tag, push
#   ./scripts/release.sh current             # tag the current version as-is
set -euo pipefail

# uv colorizes output when it detects a TTY; escape codes must never leak
# into version strings used for commit messages and tag names.
export NO_COLOR=1

BUMP="${1:?usage: release.sh patch|minor|major|current}"

if [ -n "$(git status --porcelain)" ]; then
  echo "error: working tree is dirty, commit or stash first" >&2
  exit 1
fi

if [ "$(git branch --show-current)" != "main" ]; then
  echo "error: releases are cut from main" >&2
  exit 1
fi

git pull --ff-only

if [ "$BUMP" != "current" ]; then
  uv version --bump "$BUMP"
  uv lock
  VERSION="$(uv version --short)"
  git add pyproject.toml uv.lock
  git commit -m "release: v${VERSION}"
else
  VERSION="$(uv version --short)"
fi

TAG="v${VERSION}"
if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  echo "error: tag ${TAG} already exists" >&2
  exit 1
fi

git tag -a "$TAG" -m "Release ${TAG}"
git push origin main "$TAG"

echo "Pushed ${TAG}, release workflow: https://github.com/eytanohana/murmur/actions"
