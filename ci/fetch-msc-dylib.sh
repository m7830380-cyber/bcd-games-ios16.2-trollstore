#!/bin/bash
# Apple's Metal Shader Converter runtime for iOS (libmetalirconverter.dylib).
# BCD tracks it in app/Madeira/d3d12/ in its private repository, but not in the
# public one. Upstream Madeira tracks the same file (same SHA-256, pinned in
# build/madeira-d3d12/deps.sh), so take it from there at a fixed commit and
# check the hash. Distributed under Apple's agreement: app/Madeira/d3d12/NOTICE.txt.
set -euo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$R/app/Madeira/d3d12/libmetalirconverter.dylib"
SHA=073f903be98e973ff38f4d79f2c48d61ef938754a77b1caedda79c9f05a068c2
URL=https://raw.githubusercontent.com/willfaust/Madeira/035fe82c1cdbbf3bfd2319e213747b51f74d2dce/app/Madeira/d3d12/libmetalirconverter.dylib
if [ -f "$DEST" ] && [ "$(shasum -a 256 "$DEST" | cut -d' ' -f1)" = "$SHA" ]; then
    echo "libmetalirconverter.dylib already in place"; exit 0
fi
curl -fsSL --retry 3 -o "$DEST.tmp" "$URL"
got=$(shasum -a 256 "$DEST.tmp" | cut -d' ' -f1)
[ "$got" = "$SHA" ] || { echo "error: libmetalirconverter.dylib hash mismatch ($got)"; rm -f "$DEST.tmp"; exit 1; }
mv "$DEST.tmp" "$DEST"
chmod 755 "$DEST"
echo "libmetalirconverter.dylib: $(wc -c < "$DEST" | tr -d ' ') bytes, sha256 verified"
