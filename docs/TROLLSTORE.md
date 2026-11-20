# BCD on iOS 16 with TrollStore

This branch (`ios16-trollstore`) builds [BCD](https://github.com/thebcd69/bcd-games)
for **iOS 16.0 and later**, installed with
[TrollStore](https://github.com/opa334/TrollStore) instead of a sideloader.
iOS 16.2 is the target. It is the same port as
[Madeira-TrollStore](https://github.com/m7830380-cyber/Madeira-TrollStore), applied
to BCD 0.1.665. Every build comes from GitHub Actions; no Mac is needed.

## Install

1. Download `BCD-<version>-trollstore.tipa` from the latest release, or from the
   `BCD-trollstore-tipa` artifact of a successful run of
   *Build BCD for iOS 16 (TrollStore .tipa)*.
2. Open it with TrollStore and install it.
3. Start BCD with JIT, in either of two ways:
   - In TrollStore's app list, long-press **BCD** → **Open with JIT**.
   - Or open BCD normally and tap **Enable JIT**. BCD asks TrollStore through
     its `apple-magnifier://enable-jit` URL (TrollStore 2.0.9 or later).

No StikDebug, LocalDevVPN, pairing file or computer is involved, and the
signature does not expire.

## What differs from BCD

| Area | BCD (iOS 18+) | This port (iOS 16) |
|---|---|---|
| Enabling JIT | StikDebug or Built-in StikJIT (iOS 17.4 / 26) | TrollStore (`app/Madeira/TrollStoreJIT.swift`). Below iOS 17 it is the only method; `env.MADEIRA_JIT_TROLLSTORE = 0/1` forces it. |
| JIT memory | A debugger answers BRK `#0xf00d`, or BCD's own debugger-less pool where TXM is absent | Always BCD's debugger-less pool (`JITNoDebuggerPool`, on by default): TrollStore's attach-and-detach leaves `CS_DEBUGGED` set, and iOS 16 has no TXM. Wine's page-0 request to a debugger is only made when one is attached (`build/ntdll-unix/signal_arm64_ios.c`). |
| JIT helper, StikJIT, memory host extension | bundled | removed from the package (iOS 26 / 27 only) |
| Entitlements | the sideloader's profile | `ci/trollstore.entitlements`: `get-task-allow`, increased memory limits, extended virtual addressing, keychain group |
| DXMT shaders | Metal 3.1/3.2, AIR 2.6-2.7, macOS-style libraries | On iOS < 17 airconv writes Metal 3.0 / AIR 2.5 libraries with an iOS 16 header (`ci/patches/dxmt`, from Madeira-TrollStore; `DXMT_LEGACY_AIR=0/1` overrides). DXMT's command library and the i386 DXMT DLLs are built for `air64-apple-ios16.0`, and below iOS 17 the 64-bit games get the DXMT set built from source (`MADEIRA_DXMT_FRESH = 1` by default; a game's own `0` keeps the committed set). |
| Direct3D 12 | MSC runtime, helper kernels for iOS 26 with an iOS 18 fallback | The fallback copy of the helper kernels targets iOS 16 (loads on 16 and 18). |
| Wine | `os_sync_*` behind `__builtin_available(macOS 14.4, *)`, always true on iOS | also checks iOS 17.4 (`ci/patches/wine`) |
| SwiftUI | iOS 17/18 API | `app/Madeira/iOS16SwiftUICompat.swift`: a backport of the two-value `onChange` (BCD's call sites unchanged), `ObservableObject` instead of `@Observable` (`SteamSession`, `BCDThemeStore`), plain fallbacks for scroll phases, search presentation, sheet backgrounds, Settings row separators. The iPhone Duo interface (iOS 26 foldables) is iOS 18 only. |
| libc++ | the iOS 26 SDK's | Float `to_chars` (16.3), `__libcpp_verbose_abort` (16.3) and `std::pmr` (17) are defined in the app (`app/Madeira/iOS16LibcxxCompat.cpp`). |
| Native libraries | iOS 17/18 deployment targets | all iOS 16.0 |
| Vulkan | MoltenVK, KosmicKrisp (Metal 4) | MoltenVK only; KosmicKrisp needs iOS 26 and is not built |

On iOS 16 a colour profile chosen in Settings › Appearance shows at once there
and on the tint; other screens take it as they redraw (BCD relies on iOS 17's
Observation for the rest).

## What CI checks

`ci/package-tipa.sh`, before it signs:

- `MinimumOSVersion` is 16.0;
- every strong libc++ import of the binary exists in iOS 15.6's libc++
  (`ci/libcxx-ios15.6-exports.txt`);
- weak-imported C functions are listed in `ci/weak-imports-audited.txt`
  (any new one fails the build: it is NULL on an iOS that lacks it);
- no framework newer than iOS 16 is linked strongly;
- every linked native archive targets iOS 16.0 or older (the build fails
  otherwise).

Last full build that passed all of these: run 44462979640 (`7edef51`),
`BCD-trollstore-tipa` about 224 MB. The binary and all 16 native archives
target iOS 16.0, and all 160 libc++ imports exist in iOS 15.6's libc++.

What is not verified: running on a device. Logs are under **Settings › Logs**.

## Requirements

- iOS 16.0 or later with TrollStore (A11 or newer; Metal 3 for Direct3D 11/12
  needs an A13 or newer).
- Microsoft's Visual C++ runtime is fetched from Microsoft at build time, as BCD
  does.

## Building

`.github/workflows/build-trollstore.yml` is **generated** from BCD's own
`build-ipa.yml` by `ci/make-trollstore-workflow.py`: every build and patch step
as BCD has it, minus BCD's host checks (`tests/host`), KosmicKrisp and BCD's
private signing, plus this port's steps. After taking a new BCD release, run
`python3 ci/make-trollstore-workflow.py` again.

It runs when the `build-trollstore` branch is pushed
(`git push origin HEAD:build-trollstore`), or for a `v*-trollstore*` tag, which
also publishes the `.tipa` as a release. `.github/workflows/compile-check-ios16.yml` compiles the
app's sources against iOS 16.0 on every push (minutes, no native libraries).

The pieces the port adds:

| File | What |
|---|---|
| `ci/apply-patches.sh` | applies `ci/patches/<submodule>/*.patch`; a patch that does not apply stops the build |
| `ci/fetch-msc-dylib.sh` | Apple's Metal Shader Converter runtime from upstream Madeira at a pinned commit, hash-checked (BCD's public repository leaves it out) |
| `ci/package-tipa.sh` | the checks above, ldid signing, the `.tipa` |
| `ci/pbx-add.py` | adds a source file to the Xcode project |

## Unity games: white screen on iOS 16 (fixed by dxmt patch 0007)

People Playground (Unity, D3D11) ran with sound but never drew a frame on an
iPhone 14 Pro Max with iOS 16.2. The GPU compiler failed four pipelines with
"Compiler encountered an internal error" and then held UnityGfxDeviceWorker in
a blit encoder for good. Every failing vertex shader (dumped to
`C:\dxmt-failed`) was SM 4.x and carried the align-1 vertex loads from BCD's
byte-aligned vertex fetch (tools/patch-dxmt-vb-align.py), which turned on just
before the first failure. Patch 0007 leaves that fetch off below iOS 17
(`MADEIRA_VFETCH_ALIGN1=1` still forces it) and bumps the shader cache version
so shaders converted with it are converted again.

### Still white after 0007: the PSO probe (dxmt patch 0008)

Patch 0007 removed the align-1 loads, but iOS 16.2 still fails People
Playground's trivial blit pipeline with "Compiler encountered an internal
error". Patch 0008 adds a one-shot background probe after the first failed
render pipeline (on by default below iOS 17, `MADEIRA_PSO_PROBE=0` turns it
off). It compiles MSL controls and variants of the failing blit shaders, each
with one change (a bare skeleton, LLVM 14 encoding, other triple/AIR version,
non-packed return struct, no module flags, no vertex pulling, no argument
buffer, no `air.fma`, no NaN select), and logs one `[pso-probe]` line each.
The variants are built by `ci/tools/gen-pso-probe.py` from the dumped
metallibs. Let the game sit on the white screen until `[pso-probe] end`
appears (a failing compile can take ~30 s), then send `bcd-log.txt`.
