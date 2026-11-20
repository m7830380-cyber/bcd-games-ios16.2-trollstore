#!/bin/bash
# iOS 16 / TrollStore port: package the archived BCD.app as a TrollStore .tipa.
#   ci/package-tipa.sh <Madeira.app> <output dir> <build number>
# Checks that the binary can launch on iOS 16.2 (every strong libc++ import
# exists in iOS 15.6's libc++, every weak C import is audited, no system
# framework newer than iOS 16 is linked strongly), drops the iOS 26/27-only
# extensions, signs with ldid and ci/trollstore.entitlements, zips a .tipa.
set -euo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_IN="$1"; OUT="$(mkdir -p "$2" && cd "$2" && pwd)"; BUILD="$3"
export LC_ALL=C
BIN="$APP_IN/Madeira"
[ -f "$BIN" ] || { echo "error: $BIN missing"; exit 1; }

echo "== minimum OS"
MINOS=$(/usr/libexec/PlistBuddy -c 'Print :MinimumOSVersion' "$APP_IN/Info.plist")
echo "MinimumOSVersion $MINOS"
case "$MINOS" in 16.0|16.0.0|15.*) ;; *) echo "error: MinimumOSVersion is $MINOS, not 16.0"; exit 1 ;; esac
otool -l "$BIN" | awk '/LC_BUILD_VERSION/{f=1} f&&/minos/{print "  binary minos " $2; exit}'

echo "== libc++ imports"
# A symbol bound strongly to a system library the OS does not export stops dyld
# before main. Newer ones are defined in app/Madeira/iOS16LibcxxCompat.cpp.
imports=$(nm -m -u "$BIN" | grep -v weak | grep "from libc++" | awk '{print $3}' | sort -u)
too_new=$(comm -23 <(echo "$imports") <(grep -v '^#' "$R/ci/libcxx-ios15.6-exports.txt" | sort -u))
if [ -n "$too_new" ]; then
    echo "error: the binary imports libc++ symbols that iOS 16.2 may not have:"; echo "$too_new"; exit 1
fi
echo "$(echo "$imports" | grep -c .) libc++ imports, all in iOS 15.6's libc++"

echo "== weak C imports"
# Weak-imported C functions are NULL on an iOS that lacks them; a call not
# behind an availability check jumps to 0. __builtin_available(macOS ...) is
# always true on iOS: that trap hit Wine's os_sync calls on 16.2.
audited_file="$R/ci/weak-imports-audited.txt"
weak_c=$(nm -m -u "$BIN" | grep "weak external" | awk '{print $4}' | grep -v '^_\$s\|^_OBJC_\|^_swift\|^__swift\|^_\$S' | sort -u || true)
unaudited=$(comm -23 <(echo "$weak_c") <(grep -v '^#' "$audited_file" | sort -u))
echo "$(echo "$weak_c" | grep -c . || true) weak C imports"
if [ -n "$unaudited" ]; then
    echo "::error::weak-imported C functions not in ci/weak-imports-audited.txt (NULL on older iOS unless the caller checks):"
    echo "$unaudited"
    [ "${STRICT_WEAK:-1}" = 1 ] && exit 1
fi

echo "== strongly linked system libraries"
# Frameworks iOS 16 does not have must be weak (-weak_framework).
otool -L "$BIN" | tail -n +2 | awk '{print $1}' > "$OUT/linked.txt"
otool -l "$BIN" | awk '/cmd LC_LOAD_DYLIB/{getline; getline; print $2}' > "$OUT/strong.txt"
bad=0
for fw in Symbols.framework TranslationUIProvider DeviceDiscoveryUI.framework TipKit.framework SwiftData.framework \
          Observation.framework StoreKitTest AccessorySetupKit.framework Translation.framework CoreHID.framework \
          WorkoutKit.framework JournalingSuggestions.framework SensitiveContentAnalysis.framework; do
    if grep -q "$fw" "$OUT/strong.txt"; then echo "error: $fw (newer than iOS 16) is linked strongly"; bad=1; fi
done
[ $bad = 0 ] || exit 1
echo "$(wc -l < "$OUT/strong.txt" | tr -d ' ') strong, $(($(wc -l < "$OUT/linked.txt") - $(wc -l < "$OUT/strong.txt"))) weak"

echo "== native archives"
for f in "$R"/app/Madeira/lib*.a "$R"/FEX/build-ios/FEXCore/Source/*.a; do
    [ -f "$f" ] || continue
    v=$(otool -l "$f" 2>/dev/null | awk '$1=="minos"{print $2}' | sort -uV | tail -1)
    printf '  %-32s %s\n' "$(basename "$f")" "${v:-?}"
    if [ -n "$v" ] && [ "$(printf '%s\n16.0\n' "$v" | sort -V | tail -1)" != "16.0" ]; then
        echo "::error::$(basename "$f") targets iOS $v, newer than 16.0"; bad=1
    fi
done
[ $bad = 0 ] || exit 1

echo "== stage"
STAGE="$OUT/stage"; rm -rf "$STAGE"; mkdir -p "$STAGE/Payload"
ditto --norsrc --noextattr --noqtn --noacl "$APP_IN" "$STAGE/Payload/Madeira.app"
A="$STAGE/Payload/Madeira.app"
# iOS 26/27 only: the JIT helper (26.0) and StikJIT (17.4), the memory host
# extension (an iOS 27 extension point). JIT comes from TrollStore here.
rm -rf "$A/PlugIns/MadeiraJITHelper.appex" "$A/PlugIns/MadeiraMemoryHost.appex" "$A/Frameworks/StikJIT.framework"
rmdir "$A/PlugIns" 2>/dev/null || true
# KosmicKrisp is iOS 26 only and never loaded below it.
rm -f "$A/gl/libvulkan_kosmickrisp.dylib" "$A/gl/libvulkan_kosmickrisp.version" "$A/gl/libvulkan_kosmickrisp_real.dylib"
# Bundled libraries are loaded with dlopen, so one built for a newer iOS shows up only at run time: name it here.
while IFS= read -r f; do
    v=$(otool -l "$f" 2>/dev/null | awk '$1=="minos"{print $2}' | sort -uV | tail -1)
    if [ -n "$v" ] && [ "$(printf '%s\n16.0\n' "$v" | sort -V | tail -1)" != "16.0" ]; then
        echo "::warning::${f#$A/} targets iOS $v, newer than 16.0"
    fi
done < <(find "$A" -type f \( -name '*.dylib' -o -name '*.so' \))
find "$A" -name '_CodeSignature' -type d -prune -exec rm -rf {} +
rm -f "$A/embedded.mobileprovision"
/usr/libexec/PlistBuddy -c "Delete :MadeiraPort" "$A/Info.plist" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Add :MadeiraPort string ios16-trollstore" "$A/Info.plist"

echo "== sign"
n=0
while IFS= read -r f; do
    if file "$f" | grep -q "Mach-O" && [ "$f" != "$A/Madeira" ]; then ldid -S "$f"; n=$((n+1)); fi
done < <(find "$A" -type f \( -name '*.dylib' -o -name '*.so' -o -perm -u+x \) ! -name '*.dll' ! -name '*.exe')
while IFS= read -r fw; do ldid -S "$fw"; n=$((n+1)); done < <(find "$A/Frameworks" -maxdepth 2 -type f -perm -u+x 2>/dev/null || true)
ldid -S"$R/ci/trollstore.entitlements" "$A/Madeira"
echo "$n nested Mach-O files signed ad hoc; the main binary with:"
ldid -e "$A/Madeira"

VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$A/Info.plist")
T="$OUT/BCD-$VERSION-trollstore.tipa"
(cd "$STAGE" && ditto -c -k --norsrc --noextattr --noqtn --noacl --keepParent Payload "$T")
rm -rf "$STAGE"
ls -l "$T"
echo "::notice::BCD $VERSION (build $BUILD) for TrollStore, MinimumOSVersion $MINOS: $(basename "$T")"
