#!/usr/bin/env python3
"""Derive .github/workflows/build-trollstore.yml from BCD's own build-ipa.yml.

BCD builds every component from source in one macOS job (build-ipa.yml, about
800 steps). This port keeps every build and patch step exactly as BCD has it,
so a new BCD release can be taken by re-running this script, and changes only
what iOS 16 and TrollStore need:

  * runs in this repository, by hand or for a v*-trollstore* tag;
  * drops BCD's host checks (tests/host/*: they check BCD's own logic on the
    runner, need inputs that are not in the public repository, and build
    nothing that ships);
  * drops KosmicKrisp (Mesa's Vulkan on Metal 4: iOS 26 only, never loaded below);
  * fetches the Metal Shader Converter dylib the public repository leaves out;
  * applies ci/patches/<submodule>/*.patch (the iOS 16 changes to Wine and DXMT);
  * replaces BCD's private OTA signing with ci/package-tipa.sh, which signs with
    ldid and TrollStore's entitlements and uploads the .tipa as an artifact and,
    for a tag, a release.

usage: python3 ci/make-trollstore-workflow.py   (from the repository root)
"""
import re
import sys

SRC = '.github/workflows/build-ipa.yml'
DST = '.github/workflows/build-trollstore.yml'
BRANCH = 'ios16-trollstore'

L = open(SRC).read().split('\n')
start = L.index('    steps:')
head, body = L[:start + 1], L[start + 1:]

# --- split the job's steps ------------------------------------------------
steps, cur = [], None
pre = []          # comment lines before the first step
for line in body:
    if re.match(r'^      - ', line):
        cur = {'lines': []}
        steps.append(cur)
    if cur is None:
        pre.append(line)
    else:
        cur['lines'].append(line)

def step_name(s):
    for line in s['lines'][:3]:
        m = re.match(r'^      - name: (.*)$', line) or re.match(r'^        name: (.*)$', line)
        if m:
            return m.group(1).strip().strip('"')
    return s['lines'][0].strip()

def run_lines(s):
    out, inrun = [], False
    for line in s['lines']:
        if re.match(r'^        run: [|>]', line):
            inrun = True
            continue
        m = re.match(r'^        run: (.+)$', line)
        if m:
            out.append(m.group(1))
            continue
        if inrun:
            if re.match(r'^        \S', line):
                inrun = False
                continue
            t = line.strip()
            if t and not t.startswith('#'):
                out.append(t)
    return out

HOST_TEST = re.compile(r'^(ASAN_OPTIONS=\S+ )?(python3|bash|swift|sh) (-\S+ )*tests/host/')

def is_host_check(s):
    r = run_lines(s)
    return bool(r) and all(HOST_TEST.match(x) for x in r)

def strip_host_tests(s):
    """'Check and patch ...' steps: keep the patch, drop the host test lines."""
    out, cont = [], False
    for l in s['lines']:
        if cont or HOST_TEST.match(l.strip()):
            cont = l.rstrip().endswith('\\')   # drop the command's continuation lines too
            continue
        out.append(l)
    s['lines'] = out

DROP_NAMES = (
    'Check the 32-bit MFC download and its session links',   # a host test around a download
    'Sign for OTA install (private bucket)',                 # owner's certificate and bucket
    'Check the IPA reached the private bucket',
    'Package Madeira and Madeira2 unsigned IPAs',            # replaced by ci/package-tipa.sh
    'StikJIT interface for the runner\'s Swift',             # re-added below with || true
    # BCD 0.1.686's post-archive checks hold the app to BCD's oldest iOS (18): with this port's
    # MinimumOSVersion 16.0 every guarded iOS 17/18 API would stop the build. ci/package-tipa.sh
    # checks the app's system imports against iOS 16 instead.
    'Check the app for weak C imports newer than its oldest iOS',
    'Check the app against older iOS runtimes',
    'Check compiler availability warnings above the app\'s oldest iOS',
)

DROP_WITH_COMMENT = DROP_NAMES[-3:]

def is_kosmickrisp(s):
    return 'kosmickrisp' in step_name(s).lower() or 'kosmic' in ' '.join(run_lines(s)).lower() and \
        step_name(s).lower().startswith(('restore kosmic', 'build kosmic', 'save kosmic', 'drop a stale kosmic'))

kept, dropped = [], {'host checks': 0, 'kosmickrisp': 0, 'named': 0}
for s in steps:
    n = step_name(s)
    if n in DROP_NAMES:
        dropped['named'] += 1
        # the dropped step's own comment block ends the step before it
        if kept and n in DROP_WITH_COMMENT:
            prev = kept[-1]['lines']
            while prev and prev[-1].lstrip().startswith('#'):
                prev.pop()
        continue
    if is_kosmickrisp(s):
        dropped['kosmickrisp'] += 1
        continue
    if is_host_check(s) and not any(re.match(r'^        id: ', l) for l in s['lines']):
        dropped['host checks'] += 1
        continue
    if any(HOST_TEST.match(x) for x in run_lines(s)):
        strip_host_tests(s)
    kept.append(s)

def find(name):
    for i, s in enumerate(kept):
        if step_name(s) == name:
            return i
    sys.exit(f'step not found: {name}')

def block(text):
    return {'lines': text.rstrip('\n').split('\n')}

# --- the port's own steps -------------------------------------------------
PREPARE = block('''\
      # iOS 16 / TrollStore port (ci/make-trollstore-workflow.py): the converter
      # dylib the public repository leaves out, and the iOS 16 patches to the
      # submodules, before any of BCD's own patch steps run.
      - name: "iOS 16: fetch the Metal Shader Converter dylib"
        run: bash ci/fetch-msc-dylib.sh

      - name: "iOS 16: apply ci/patches to the submodules"
        run: bash ci/apply-patches.sh

      - name: "iOS 16: drop the missing third-party docs from their SHA256SUMS"
        run: bash ci/fix-third-party-sums.sh''')

PACKAGE = block('''\
      # iOS 16 / TrollStore port: sign with ldid and TrollStore's entitlements
      # (ci/trollstore.entitlements), drop the iOS 26/27-only extensions, check
      # every system import exists on iOS 16, and zip a .tipa.
      - name: "iOS 16: package the TrollStore .tipa"
        run: |
          brew install ldid || true
          bash ci/package-tipa.sh build-out/Madeira.xcarchive/Products/Applications/Madeira.app build-out ${{ steps.bn.outputs.n }}

      - uses: actions/upload-artifact@v4
        with:
          name: BCD-trollstore-tipa
          path: build-out/*.tipa
          if-no-files-found: error

      - name: "iOS 16: release (tags only)"
        if: startsWith(github.ref, 'refs/tags/')
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release create "${GITHUB_REF_NAME}" build-out/*.tipa --title "BCD ${GITHUB_REF_NAME} (TrollStore, iOS 16)" \\
            --notes "BCD for iOS 16 with TrollStore. Install the .tipa with TrollStore, then open BCD with Open with JIT. See docs/TROLLSTORE.md." \\
            || gh release upload "${GITHUB_REF_NAME}" build-out/*.tipa --clobber''')

STIKJIT = block('''\
      - name: StikJIT interface for the runner's Swift
        shell: bash
        run: python3 tools/stikjit-interface-compat.py app/Frameworks/StikJIT.xcframework || true''')

GNUTLS = block('''\
      # iOS 16 / TrollStore port: the app links app/Madeira/lib{gmp,nettle,hogweed,gnutls}.a,
      # which BCD commits built for iOS 17; the stack this job builds (or restores) is for iOS 16.
      - name: "iOS 16: link the iOS 16 GnuTLS stack"
        run: |
          for l in gmp nettle hogweed gnutls; do
            cp toolchains/gnutls-ios/lib/lib$l.a app/Madeira/lib$l.a
          done''')

METAL = block('''\
      # iOS 16 fork: the Metal compiler must exist before the first DXMT build (Build d3d11-src.dll, below). The
      # xcode-27-arm64 image of 2026-11-19 ships Xcode without the Metal Toolchain; that build then fell back to upstream's
      # macOS 14 library of DXMT's internal shaders, which iOS 16 refuses ("This library format is not supported on this
      # platform"), and DXMT aborted at device creation. The later "Ensure Metal compiler" step finds it present.
      - name: Ensure Metal compiler (before the DXMT builds)
        shell: bash
        run: |
          if xcrun -sdk iphoneos metal --version 2>&1 | head -3 && xcrun -sdk iphoneos metal --version > /dev/null 2>&1; then
            echo "::notice::metal compiler already present"
          else
            echo "metal missing -- downloading the Metal Toolchain"
            xcodebuild -downloadComponent MetalToolchain
            xcrun -sdk iphoneos metal --version || { echo "::error::no Metal compiler after the download"; exit 1; }
          fi''')

kept.insert(find('Select newest Xcode') + 1, PREPARE)
# the comment lines that close 'Select newest Xcode' introduce the step after it: keep them below METAL
_sel = kept[find('Select newest Xcode')]['lines']
_tail = []
while _sel and (_sel[-1].strip().startswith('#') or not _sel[-1].strip()):
    _tail.insert(0, _sel.pop())
_sel.append('')
while _tail and not _tail[0].strip():
    _tail.pop(0)
if _tail:
    METAL['lines'] += [''] + _tail
kept.insert(find('Select newest Xcode') + 1, METAL)
GNUTLS['lines'].append('')
kept.insert(find('Verify GnuTLS iOS headers and libs') + 1, GNUTLS)
kept.insert(find('Archive (unsigned)'), STIKJIT)
kept.insert(find('Verify the macdrv symbols DXMT resolves at runtime') + 1, PACKAGE)

# --- the job header -------------------------------------------------------
# BCD's header up to "on:" is its licence notes; from "concurrency:" on it is
# the job, whose repository gate and runner change.
lic = [l for l in head[:head.index('on:')] if l.startswith('#')]
job = head[head.index('concurrency:'):]
out = lic + [
    'name: Build BCD for iOS 16 (TrollStore .tipa)',
    '',
    '# The whole build takes hours: started by a release tag, by pushing to the',
    '# build-trollstore branch (git push origin HEAD:build-trollstore), or by hand',
    '# (Actions > Run workflow, once this file is on the default branch); not by',
    '# every push to the port branch, which would cancel the run in progress.',
    'on:',
    '  push:',
    '    branches: [build-trollstore]',
    "    tags: ['v*-trollstore*']",
    '  workflow_dispatch:',
    '',
]
for line in job:
    if line == '    timeout-minutes: 360':
        # iOS 16 accepts Metal libraries up to Metal 3.0 / AIR 2.5 for an iOS 16 target: DXMT's
        # command library and the i386 farm's DXMT are built that way (tools/build-d3d11-dll.sh,
        # build/wine-i386/build.sh, build/dxmt-ios/build.sh read these).
        out += [line, '    env:', '      DXMT_METAL_SDK: iphoneos', '      DXMT_METAL_TARGET: air64-apple-ios16.0',
                '      DXMT_METAL_STD: metal3.0', '      MADEIRA_IOS_MIN: "16.0"']
        continue
    if line.startswith('  group: build-ipa'):
        line = '  group: build-trollstore-${{ github.ref }}'
    if re.match(r"^    if: github.repository == ", line):
        continue
    if line.strip() == 'runs-on: macos-15':
        line = '    runs-on: xcode-27'
    out.append(line)

text = [
    '# GENERATED by ci/make-trollstore-workflow.py from build-ipa.yml (BCD\'s own build).',
    '# Do not edit by hand: change the generator or BCD\'s workflow, then re-run it.',
] + out + pre
for s in kept:
    text += s['lines']
# BCD's GnuTLS cache key names only the library versions, so a stack cached by a
# build for a newer iOS would be restored as is; key it to the iOS 16.0 build.
text = [t.replace('gnutls3.8.9-v1', 'gnutls3.8.9-ios16.0-v1') for t in text]
# BCD only warns when d3d11-src.dll embeds upstream's internal shaders; on iOS 16 that library is refused and
# DXMT aborts at device creation, so the port fails the build instead.
WARN = '            echo "::warning::d3d11-src.dll has no [dxmt-shaders] switch: env.MADEIRA_DXMT_FRESH = 1 links the fresh set with upstream\'s internal shaders"'
FAIL = ['            # iOS 16 fork: upstream\'s internal shaders are a macOS 14 library that iOS 16 refuses, so DXMT aborts: fail here.',
        '            echo "::error::d3d11-src.dll has no [dxmt-shaders] switch: env.MADEIRA_DXMT_FRESH = 1 links the fresh set with upstream\'s macOS internal shaders, which iOS 16 refuses"',
        '            exit 1']
if text.count(WARN) != 1:
    sys.exit(f'[dxmt-shaders] warning found {text.count(WARN)} times, expected 1')
i = text.index(WARN)
text[i:i + 1] = FAIL
open(DST, 'w').write('\n'.join(text).rstrip('\n') + '\n')
print(f'{DST}: {len(kept)} steps kept of {len(steps)}; dropped {dropped}')
