#!/bin/bash
# iOS 16 / TrollStore port: BCD's public repository leaves out the README and
# LICENSE markdown of third-party/*, but their SHA256SUMS still list them, so
# tools/build-d3d8to9-dll.sh and build-cnc-ddraw-dll.sh refuse the tree. Drop
# the lines for listed .md files that are not there (documentation only).
set -e
for sums in third-party/*/SHA256SUMS; do
    python3 -I - "$sums" <<'PY'
import os, sys
p = sys.argv[1]; d = os.path.dirname(p)
keep = []
for line in open(p, newline=''):
    f = line.rstrip('\n')[66:]
    if f.endswith('.md') and not os.path.exists(os.path.join(d, f)):
        print(f'fix-third-party-sums: {d}: {f} not in the repository, dropped'); continue
    keep.append(line)
open(p, 'w', newline='').write(''.join(keep))
PY
done
