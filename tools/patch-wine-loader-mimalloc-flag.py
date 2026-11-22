#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[mimalloc-flag]: redirect a program's malloc family to its mimalloc.dll when mimalloc-redirect.dll cannot patch Wine's ucrtbase (64-bit set).

Roblox Studio (RobloxStudioBeta.exe, 2026-11-22) loads mimalloc.dll v3.1.6 and mimalloc-redirect.dll v1.3.3 and later stops with
"RBXCRASH: HardAssert (Expect to have mimalloc working, but it was not detected!)". With MIMALLOC_VERBOSE=1 the redirector says why:
"mimalloc-redirect.dll seems to be initialized after ucrtbase.dll" -- it refuses to patch when ucrtbase's LDR_DATA_TABLE_ENTRY.Flags has
0x80000 (LDR_PROCESS_ATTACHED) set, which no MIMALLOC_* variable skips. In an ARM64EC process the emulator and Wine's own modules attach
ucrtbase long before any program DLL, so this always fails; Wine's ARM64EC ucrtbase holds ARM64 code an x64 jump patch must not be written into.
Studio's check (RobloxStudioBeta.exe+0x584d00) is mi_is_in_heap_region( malloc( 32 ) ) && mi_usable_size(..) >= min, so a flag alone does not do.

So once mimalloc.dll's DLL_PROCESS_ATTACH returned (and mimalloc-redirect.dll is loaded, and mi_is_redirected() is FALSE) the loader redirects
through import tables: every program module (not under C:\windows, not mimalloc*) gets its ucrtbase.dll / api-ms-win-crt-heap-* malloc, calloc,
_aligned_malloc.. pointed at mimalloc, and free, realloc, _msize, _expand, _recalloc, _aligned_free.. at a small x64 stub that sends mimalloc's
pointers (mi_is_in_heap_region) to mimalloc and any other pointer (ucrtbase allocations from before, or from inside Wine) to the original.
Modules loaded later are patched before their DllMain. It also calls mimalloc's _mi_redirect_entry(DLL_PROCESS_ATTACH) so mi_is_redirected()
is TRUE, as the redirector does on success. env.MADEIRA_MIMALLOC_FLAG = 0 turns it all off. Nothing happens for a process without both DLLs.
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
 * an already attached ucrtbase, which in an ARM64EC process it always is. Studio then checks mi_is_in_heap_region( malloc( 32 ) ) and stops with
 * "Expect to have mimalloc working". So the loader does the redirect itself, through import tables instead of ucrtbase's (ARM64EC) code:
 * every program module (not under C:\windows) gets its ucrtbase / api-ms-win-crt-heap malloc family pointed at mimalloc. Calls that take a
 * pointer (free, realloc, _msize, ...) go through a small x64 stub that sends mimalloc's own pointers to mimalloc and every other pointer
 * (allocated by ucrtbase before or inside Wine) to the original function. Modules loaded later are patched before their DllMain.
 * MADEIRA_MIMALLOC_FLAG=0 turns all of it off. Loader lock held. */
enum ios_mi_kind { IOS_MI_DIRECT, IOS_MI_PTR };
static const struct { const char *name; const char *mi; enum ios_mi_kind kind; } ios_mi_map[] =
{
    { "malloc", "mi_malloc", IOS_MI_DIRECT },             { "_malloc_base", "mi_malloc", IOS_MI_DIRECT },
    { "calloc", "mi_calloc", IOS_MI_DIRECT },             { "_calloc_base", "mi_calloc", IOS_MI_DIRECT },
    { "_aligned_malloc", "mi_malloc_aligned", IOS_MI_DIRECT },
    { "_aligned_offset_malloc", "mi_malloc_aligned_at", IOS_MI_DIRECT },
    { "free", "mi_free", IOS_MI_PTR },                    { "_free_base", "mi_free", IOS_MI_PTR },
    { "realloc", "mi_realloc", IOS_MI_PTR },              { "_realloc_base", "mi_realloc", IOS_MI_PTR },
    { "_recalloc", "mi_recalloc", IOS_MI_PTR },           { "_recalloc_base", "mi_recalloc", IOS_MI_PTR },
    { "_msize", "mi_usable_size", IOS_MI_PTR },           { "_msize_base", "mi_usable_size", IOS_MI_PTR },
    { "_expand", "mi_expand", IOS_MI_PTR },               { "_expand_base", "mi_expand", IOS_MI_PTR },
    { "_aligned_free", "mi_free", IOS_MI_PTR },
    { "_aligned_realloc", "mi_realloc_aligned", IOS_MI_PTR },
    { "_aligned_offset_realloc", "mi_realloc_aligned_at", IOS_MI_PTR },
    { "_aligned_recalloc", "mi_recalloc_aligned", IOS_MI_PTR },
    { "_aligned_offset_recalloc", "mi_recalloc_aligned_at", IOS_MI_PTR },
    { "_aligned_msize", "mi_usable_size", IOS_MI_PTR },
};
#define IOS_MI_COUNT (sizeof(ios_mi_map) / sizeof(ios_mi_map[0]))
#define IOS_MI_STUB 80
static void *ios_mi_fn[IOS_MI_COUNT];      /* mimalloc's function per entry (NULL: export missing, entry left alone) */
static void *ios_mi_in_heap;               /* mimalloc's mi_is_in_heap_region; non-NULL once the redirect is on */
static HMODULE ios_mi_dll;
static BYTE *ios_mi_stubs;
static SIZE_T ios_mi_stub_used, ios_mi_stub_size;
static struct { void *orig; unsigned int idx; void *stub; } ios_mi_stub_tab[128];
static unsigned int ios_mi_stub_count;

/* x64: test rcx,rcx; jz mi; push rcx,rdx,r8,r9; sub rsp,28h; call [in_heap]; add rsp,28h; pop r9,r8,rdx,rcx; test al,al; jz orig;
 * mi: jmp [mi_fn]; orig: jmp [orig]; then the three pointers at +48/+56/+64. A NULL pointer goes to mimalloc (realloc(NULL) allocates there). */
static void *ios_mi_stub( unsigned int idx, void *orig )
{
    static const BYTE code[47] = { 0x48,0x85,0xc9, 0x74,0x1e, 0x51, 0x52, 0x41,0x50, 0x41,0x51, 0x48,0x83,0xec,0x28,
                                   0xff,0x15,27,0,0,0, 0x48,0x83,0xc4,0x28, 0x41,0x59, 0x41,0x58, 0x5a, 0x59, 0x84,0xc0, 0x74,0x06,
                                   0xff,0x25,15,0,0,0, 0xff,0x25,17,0,0,0 };
    unsigned int i;
    BYTE *stub;

    for (i = 0; i < ios_mi_stub_count; i++)
        if (ios_mi_stub_tab[i].idx == idx && ios_mi_stub_tab[i].orig == orig) return ios_mi_stub_tab[i].stub;
    if (ios_mi_stub_count == ARRAY_SIZE(ios_mi_stub_tab)) return NULL;
    if (!ios_mi_stubs)
    {
        ios_mi_stub_size = 0x10000;
        if (NtAllocateVirtualMemory( NtCurrentProcess(), (void **)&ios_mi_stubs, 0, &ios_mi_stub_size, MEM_COMMIT | MEM_RESERVE,
                                     PAGE_EXECUTE_READWRITE ))
        {
            ios_mi_stubs = NULL;
            return NULL;
        }
    }
    if (ios_mi_stub_used + IOS_MI_STUB > ios_mi_stub_size) return NULL;
    stub = ios_mi_stubs + ios_mi_stub_used;
    memcpy( stub, code, sizeof(code) );
    memset( stub + sizeof(code), 0xcc, 48 - sizeof(code) );
    memcpy( stub + 48, &ios_mi_in_heap, sizeof(void *) );
    memcpy( stub + 56, &ios_mi_fn[idx], sizeof(void *) );
    memcpy( stub + 64, &orig, sizeof(void *) );
    memset( stub + 72, 0xcc, IOS_MI_STUB - 72 );
    NtFlushInstructionCache( NtCurrentProcess(), stub, IOS_MI_STUB );
    ios_mi_stub_used += IOS_MI_STUB;
    ios_mi_stub_tab[ios_mi_stub_count].orig = orig;
    ios_mi_stub_tab[ios_mi_stub_count].idx = idx;
    ios_mi_stub_tab[ios_mi_stub_count++].stub = stub;
    return stub;
}

static BOOL ios_mi_program_module( const LDR_DATA_TABLE_ENTRY *mod )
{
    const WCHAR *base = mod->BaseDllName.Buffer, *full = mod->FullDllName.Buffer;
    if (!base || !full || mod->DllBase == ios_mi_dll) return FALSE;
    if (!wcsnicmp( full, windows_dir, wcslen( windows_dir ) )) return FALSE;
    if (!wcsnicmp( base, L"mimalloc", 8 )) return FALSE;
    return TRUE;
}

/* point one program module's ucrt heap imports at mimalloc */
static void ios_mi_patch_imports( LDR_DATA_TABLE_ENTRY *mod )
{
    const IMAGE_IMPORT_DESCRIPTOR *imp;
    BYTE *base = mod->DllBase;
    unsigned int done = 0, failed = 0;
    ULONG size;

    if (!ios_mi_in_heap || !ios_mi_program_module( mod )) return;
    if (!(imp = RtlImageDirectoryEntryToData( (HMODULE)base, TRUE, IMAGE_DIRECTORY_ENTRY_IMPORT, &size ))) return;
    for (; imp->Name && imp->FirstThunk; imp++)
    {
        const char *dll = (const char *)(base + imp->Name);
        const IMAGE_THUNK_DATA *names;
        IMAGE_THUNK_DATA *iat;

        if (_stricmp( dll, "ucrtbase.dll" ) && _strnicmp( dll, "api-ms-win-crt-heap-", 20 )) continue;
        if (!imp->OriginalFirstThunk) continue;
        names = (const IMAGE_THUNK_DATA *)(base + imp->OriginalFirstThunk);
        iat = (IMAGE_THUNK_DATA *)(base + imp->FirstThunk);
        for (; names->u1.Ordinal; names++, iat++)
        {
            const IMAGE_IMPORT_BY_NAME *by_name;
            unsigned int i;
            void *target, *addr;
            SIZE_T len;
            ULONG old;

            if (IMAGE_SNAP_BY_ORDINAL( names->u1.Ordinal )) continue;
            by_name = (const IMAGE_IMPORT_BY_NAME *)(base + (DWORD)names->u1.AddressOfData);
            for (i = 0; i < IOS_MI_COUNT; i++) if (!strcmp( (const char *)by_name->Name, ios_mi_map[i].name )) break;
            if (i == IOS_MI_COUNT || !ios_mi_fn[i]) continue;
            if (ios_mi_stubs && (BYTE *)iat->u1.Function >= ios_mi_stubs && (BYTE *)iat->u1.Function < ios_mi_stubs + ios_mi_stub_size) continue;
            if (ios_mi_map[i].kind == IOS_MI_DIRECT) target = ios_mi_fn[i];
            else if (!(target = ios_mi_stub( i, (void *)iat->u1.Function ))) { failed++; continue; }
            if ((void *)iat->u1.Function == target) continue;
            addr = &iat->u1.Function;
            len = sizeof(iat->u1.Function);
            if (NtProtectVirtualMemory( NtCurrentProcess(), &addr, &len, PAGE_READWRITE, &old )) { failed++; continue; }
            iat->u1.Function = (ULONG_PTR)target;
            NtProtectVirtualMemory( NtCurrentProcess(), &addr, &len, old, &old );
            done++;
        }
    }
    if (done || failed)
        ERR( "[mimalloc-flag] %s: %u ucrt heap import(s) now mimalloc%s\n", debugstr_w( mod->BaseDllName.Buffer ), done,
             failed ? " (some could not be patched)" : "" );
}

static void ios_mimalloc_flag( WINE_MODREF *wm )
{
    static int on = -1;
    BOOL (CDECL *is_redirected)(void) = NULL;
    void (CDECL *redirect_entry)(DWORD) = NULL;
    void *in_heap = NULL;
    ANSI_STRING fn;
    LIST_ENTRY *mark, *entry;
    unsigned int i;

    if (!wm->ldr.BaseDllName.Buffer || wcsicmp( wm->ldr.BaseDllName.Buffer, L"mimalloc.dll" )) return;
    if (!find_basename_module( L"mimalloc-redirect.dll" )) return;
    if (ios_mi_in_heap) return;
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
    RtlInitAnsiString( &fn, "mi_is_in_heap_region" );
    if (LdrGetProcedureAddress( wm->ldr.DllBase, &fn, 0, &in_heap ) || !in_heap)
    {
        ERR( "[mimalloc-flag] %s has no mi_is_in_heap_region export; left alone\n", debugstr_w( wm->ldr.FullDllName.Buffer ) );
        return;
    }
    for (i = 0; i < IOS_MI_COUNT; i++)
    {
        RtlInitAnsiString( &fn, ios_mi_map[i].mi );
        if (LdrGetProcedureAddress( wm->ldr.DllBase, &fn, 0, &ios_mi_fn[i] )) ios_mi_fn[i] = NULL;
    }
    ios_mi_dll = wm->ldr.DllBase;
    ios_mi_in_heap = in_heap;
    RtlInitAnsiString( &fn, "_mi_redirect_entry" );
    if (!LdrGetProcedureAddress( wm->ldr.DllBase, &fn, 0, (void **)&redirect_entry ) && redirect_entry)
        redirect_entry( DLL_PROCESS_ATTACH );
    ERR( "[mimalloc-flag] %s: mimalloc-redirect.dll could not patch ucrtbase; redirecting program modules' malloc family to mimalloc "
         "through their import tables, mi_is_redirected now %d (MADEIRA_MIMALLOC_FLAG=0 disables)\n",
         debugstr_w( wm->ldr.FullDllName.Buffer ), (int)is_redirected() );
    mark = &NtCurrentTeb()->Peb->LdrData->InLoadOrderModuleList;
    for (entry = mark->Flink; entry != mark; entry = entry->Flink)
        ios_mi_patch_imports( CONTAINING_RECORD( entry, LDR_DATA_TABLE_ENTRY, InLoadOrderLinks ) );
}

'''
PRE_OLD = "        status = MODULE_InitDLL( wm, DLL_PROCESS_ATTACH, lpReserved );\n"
PRE_NEW = ("        ios_mi_patch_imports( &wm->ldr );   /* madeira-bcd: [mimalloc-flag] (tools/patch-wine-loader-mimalloc-flag.py) */\n"
           "        status = MODULE_InitDLL( wm, DLL_PROCESS_ATTACH, lpReserved );\n")
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
    for what, anchor in (("process_attach", HELPER_ANCHOR), ("the attached flag of process_attach", SITE_OLD),
                         ("the DllMain call of process_attach", PRE_OLD)):
        if text.count(anchor) != 1:
            sys.exit(f"patch-wine-loader-mimalloc-flag: the anchor of {what} is not in {loader} exactly once")
    text = text.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR).replace(SITE_OLD, SITE_NEW).replace(PRE_OLD, PRE_NEW)
    loader.write_text(text)
    print(f"mimalloc-flag: applied ({loader})")


if __name__ == "__main__":
    main()
