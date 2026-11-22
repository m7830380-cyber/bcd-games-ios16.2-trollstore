#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[mimalloc-flag]: tell a program's mimalloc.dll that malloc is redirected when mimalloc-redirect.dll cannot patch Wine's ucrtbase (64-bit set).

Roblox Studio (RobloxStudioBeta.exe, 2026-11-22) loads mimalloc.dll v3.1.6 and mimalloc-redirect.dll v1.3.3 and later stops with
"RBXCRASH: HardAssert (Expect to have mimalloc working, but it was not detected!)". With MIMALLOC_VERBOSE=1 the redirector says why:
"mimalloc-redirect.dll seems to be initialized after ucrtbase.dll" -- it refuses to patch when ucrtbase's LDR_DATA_TABLE_ENTRY.Flags has
0x80000 (LDR_PROCESS_ATTACHED) set, which no MIMALLOC_* variable skips (FORCE_REDIRECT does not reach that check). In an ARM64EC process the
emulator and Wine's own modules attach ucrtbase long before any program DLL, so this always fails; the app's own x64 ucrtbase cannot be used
either (ucrt-shadow, virtual_ios.c), and Wine's ARM64EC ucrtbase holds ARM64 code an x64 jump patch must not be written into.

So, after mimalloc.dll's DLL_PROCESS_ATTACH returned, when mimalloc-redirect.dll is loaded and mimalloc's mi_is_redirected() is FALSE, the loader
calls mimalloc's own _mi_redirect_entry(DLL_PROCESS_ATTACH) -- the call the redirector makes on success -- so mi_is_redirected() is TRUE.
malloc/free stay Wine's ucrtbase and operator new/mi_* stay mimalloc (what an unredirected run does anyway); only the flag changes.
env.MADEIRA_MIMALLOC_FLAG = 0 leaves the flag alone (the line still prints). Nothing happens for a process without both DLLs.
WHERE THIS REACHES: the 64-bit ntdll through the rebuilt set (tools/build-wine-fresh-dlls.sh; env.MADEIRA_WIN64_FRESH = 1). Idempotent; fails by
name when an anchor moved.
Usage: patch-wine-loader-mimalloc-flag.py WINE-DIR
"""
from pathlib import Path
import sys

MARKER = "madeira-bcd: [mimalloc-flag]"
LOADER = "dlls/ntdll/loader.c"
HELPER_ANCHOR = "static NTSTATUS process_attach( LDR_DDAG_NODE *node, LPVOID lpReserved )\n{\n"
HELPER = r'''/* madeira-bcd: [mimalloc-flag] (tools/patch-wine-loader-mimalloc-flag.py; Roblox Studio 2026-11-22). mimalloc-redirect.dll refuses to patch
 * an already attached ucrtbase, which in an ARM64EC process it always is; mark the program's mimalloc.dll redirected the way the redirector does on
 * success, so the program's "mimalloc working" check passes. MADEIRA_MIMALLOC_FLAG=0 leaves it alone. Loader lock held. */
static void ios_mimalloc_flag( WINE_MODREF *wm )
{
    static int on = -1;
    BOOL (CDECL *is_redirected)(void) = NULL;
    void (CDECL *redirect_entry)(DWORD) = NULL;
    ANSI_STRING fn;

    if (!wm->ldr.BaseDllName.Buffer || wcsicmp( wm->ldr.BaseDllName.Buffer, L"mimalloc.dll" )) return;
    if (!find_basename_module( L"mimalloc-redirect.dll" )) return;
    if (on < 0)
    {
        UNICODE_STRING nm, val;
        WCHAR buf[8];
        RtlInitUnicodeString( &nm, L"MADEIRA_MIMALLOC_FLAG" );
        val.Buffer = buf; val.Length = 0; val.MaximumLength = sizeof(buf);
        on = !(!RtlQueryEnvironmentVariable_U( NULL, &nm, &val ) && val.Length == sizeof(WCHAR) && buf[0] == '0');
    }
    RtlInitAnsiString( &fn, "mi_is_redirected" );
    if (LdrGetProcedureAddress( wm->ldr.DllBase, &fn, 0, (void **)&is_redirected ) || !is_redirected)
    {
        ERR( "[mimalloc-flag] %s has no mi_is_redirected export; left alone\n", debugstr_w( wm->ldr.FullDllName.Buffer ) );
        return;
    }
    RtlInitAnsiString( &fn, "_mi_redirect_entry" );
    if (LdrGetProcedureAddress( wm->ldr.DllBase, &fn, 0, (void **)&redirect_entry ) || !redirect_entry)
    {
        ERR( "[mimalloc-flag] %s has no _mi_redirect_entry export; left alone\n", debugstr_w( wm->ldr.FullDllName.Buffer ) );
        return;
    }
    if (is_redirected())
    {
        ERR( "[mimalloc-flag] %s: malloc already redirected by mimalloc-redirect.dll; nothing to do\n", debugstr_w( wm->ldr.FullDllName.Buffer ) );
        return;
    }
    if (!on)
    {
        ERR( "[mimalloc-flag] %s: not redirected (MADEIRA_MIMALLOC_FLAG=0 leaves it so)\n", debugstr_w( wm->ldr.FullDllName.Buffer ) );
        return;
    }
    redirect_entry( DLL_PROCESS_ATTACH );
    ERR( "[mimalloc-flag] %s: mimalloc-redirect.dll could not patch ucrtbase; mimalloc marked redirected (_mi_redirect_entry), "
         "mi_is_redirected now %d; malloc/free stay ucrtbase (MADEIRA_MIMALLOC_FLAG=0 disables)\n",
         debugstr_w( wm->ldr.FullDllName.Buffer ), (int)is_redirected() );
}

'''
SITE_OLD = "            wm->ldr.Flags |= LDR_PROCESS_ATTACHED;\n        }\n        else\n"
SITE_NEW = ("            wm->ldr.Flags |= LDR_PROCESS_ATTACHED;\n"
            "            ios_mimalloc_flag( wm );   /* madeira-bcd: [mimalloc-flag] (tools/patch-wine-loader-mimalloc-flag.py) */\n"
            "        }\n        else\n")


def main():
    loader = Path(sys.argv[1] if len(sys.argv) > 1 else "wine") / LOADER
    text = loader.read_text()
    if MARKER in text:
        print(f"mimalloc-flag: already applied ({loader})")
        return
    for what, anchor in (("process_attach", HELPER_ANCHOR), ("the attached flag of process_attach", SITE_OLD)):
        if text.count(anchor) != 1:
            sys.exit(f"patch-wine-loader-mimalloc-flag: the anchor of {what} is not in {loader} exactly once")
    text = text.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR).replace(SITE_OLD, SITE_NEW)
    loader.write_text(text)
    print(f"mimalloc-flag: applied ({loader})")


if __name__ == "__main__":
    main()
