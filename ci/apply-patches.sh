#!/bin/bash
# iOS 16 / TrollStore port: apply ci/patches/<submodule>/*.patch in order.
# A patch that neither applies nor is already applied stops the build: a
# silently skipped one ships the bug it fixes.
set -euo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for dir in "$R"/ci/patches/*/; do
    sub=$(basename "$dir")
    for p in "$dir"*.patch; do
        [ -f "$p" ] || continue
        if git -C "$R/$sub" apply --check "$p" 2>/dev/null; then
            git -C "$R/$sub" apply "$p" && echo "applied $sub/$(basename "$p")"
        elif git -C "$R/$sub" apply --reverse --check "$p" 2>/dev/null; then
            echo "already applied $sub/$(basename "$p")"
        else
            echo "::error::patch does not apply: $sub/$(basename "$p")" >&2
            git -C "$R/$sub" apply --check "$p"
            exit 1
        fi
    done
done
