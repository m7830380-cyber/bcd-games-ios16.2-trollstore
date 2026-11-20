#!/usr/bin/env python3
"""Build the iOS 16 PSO probe tests and write pso_probe_data.h.

usage: gen-pso-probe.py <ref dir> <workdir> <out.h>

<ref dir> (ci/tools/pso-probe-ref) holds People Playground's failing blit
shaders as airconv wrote them (vs_3139aeb0..., ps_a1f3d64f... .metallib) and
the raw bitcode of DXMT's own vs_blit_quad / fs_blit_quad as Apple's metal
toolchain built them for iOS 16 (carved from d3d11-src.dll's internal
library, which does compile on the device). Every test is built into a
single-function library by the same container writer, so the only thing that
differs between two tests is the bitcode."""
import glob, hashlib, os, re, struct, subprocess, sys

ref, work, out_h = sys.argv[1:4]
os.makedirs(work, exist_ok=True)


def run(*a):
    subprocess.run(a, check=True)


def unwrap(bc):
    """Raw bitcode without a Darwin wrapper (llvm-as adds one for apple triples)."""
    if bc[:4] == b'\xde\xc0\x17\x0b':
        o, s = struct.unpack('<II', bc[8:16])
        return bc[o:o + s]
    assert bc[:4] == b'BC\xc0\xde', bc[:4]
    return bc


def read_metallib(path):
    d = open(path, 'rb').read()
    fl_off = struct.unpack('<Q', d[0x18:0x20])[0]
    po, ps, vo, vs, bo, bs = struct.unpack('<QQQQQQ', d[0x28:0x58])
    q = fl_off + 8
    tags = {}
    while d[q:q + 4] != b'ENDT':
        t = bytes(d[q:q + 4]); l = struct.unpack('<H', d[q + 4:q + 6])[0]
        tags[t] = d[q + 6:q + 6 + l]; q += 6 + l
    return dict(hdr=d[:0x10], pub=d[po:po + ps], priv=d[vo:vo + vs], tags=tags,
                bc=unwrap(d[bo:bo + bs]), raw=d)


def metallib(tmpl, name, ftype, bc):
    """One-function library, laid out as airconv's metallib_writer does."""
    blob = struct.pack('<IIIII', 0x0B17C0DE, 0, 20, len(bc), 0xFFFFFFFF) + bc
    blob += b'\0' * (-len(blob) % 16)
    def tag(t, v):
        return t + struct.pack('<H', len(v)) + v
    fdef = (tag(b'NAME', name.encode() + b'\0') + tag(b'TYPE', bytes([ftype])) +
            tag(b'HASH', hashlib.sha256(blob).digest()) + tag(b'MDSZ', struct.pack('<Q', len(blob))) +
            tag(b'OFFT', bytes(24)) + tag(b'VERS', tmpl['tags'][b'VERS']) + b'ENDT')
    fl_sz = 4 + len(fdef)
    pub_off = 0x58 + fl_sz + 4 + 4
    priv_off = pub_off + len(tmpl['pub'])
    bc_off = priv_off + len(tmpl['priv'])
    size = bc_off + len(blob)
    hdr = tmpl['hdr'][:0x10] + struct.pack('<QQQQQQQQQ', size, 0x58, fl_sz, pub_off, len(tmpl['pub']),
                                           priv_off, len(tmpl['priv']), bc_off, len(blob))
    return (hdr + struct.pack('<II', 1, fl_sz) + fdef + b'ENDT' + tmpl['pub'] + tmpl['priv'] + blob)


def dis(bc, name):
    p = os.path.join(work, name)
    open(p + '.bc', 'wb').write(bc)
    run('llvm-dis-15', p + '.bc', '-o', p + '.ll')
    return open(p + '.ll').read()


def asm(text, name, ver=15):
    p = os.path.join(work, name)
    open(p + '.ll', 'w').write(text)
    run('llvm-as-%d' % ver, p + '.ll', '-o', p + '.bc')
    return unwrap(open(p + '.bc', 'rb').read())


def sub(text, old, new):
    assert old in text, old
    return text.replace(old, new, 1)


def body(t, new_body):
    s = t.index('\n', t.index('define ')) - 1
    assert t[s] == '{'
    e = t.index('\n}\n', s)
    return t[:s + 1] + '\nentry:\n' + new_body + t[e:]


ATTRS = ('nounwind "approx-func-fp-math"="true" "frame-pointer"="all" "min-legal-vector-width"="0" "no-builtins" '
         '"no-infs-fp-math"="true" "no-nans-fp-math"="true" "no-signed-zeros-fp-math"="true" '
         '"no-trapping-math"="true" "stack-protector-buffer-size"="8" "unsafe-fp-math"="true"')


def add_ident(t):
    return t + ('\n!llvm.ident = !{!900}\n'
                '!900 = !{!"Apple metal version 32023.925 (metalfe-32023.925.6)"}\n')


def add_opts(t):
    return t + ('\n!air.compile_options = !{!901, !902, !903}\n!901 = !{!"air.compile.denorms_disable"}\n'
                '!902 = !{!"air.compile.fast_math_enable"}\n!903 = !{!"air.compile.framebuffer_fetch_enable"}\n')


def add_attrs(t):
    t = re.sub(r'^(define [^\n]*?) \{$', r'\1 #90 {', t, count=1, flags=re.M)
    t = sub(t, '\ndefine ', '\n@llvm.global_ctors = appending global [0 x { i32, void ()*, i8* }] zeroinitializer\n\ndefine ')
    return t + '\nattributes #90 = { %s }\n' % ATTRS


def meta(t):
    return add_attrs(add_opts(add_ident(t)))


def sdk(t, a, b):
    return re.sub(r'!"SDK Version", \[2 x i32\] \[i32 \d+, i32 \d+\]',
                  '!"SDK Version", [2 x i32] [i32 %d, i32 %d]' % (a, b), t)


vs_ml = read_metallib(glob.glob(os.path.join(ref, 'vs_*.metallib'))[0])
ps_ml = read_metallib(glob.glob(os.path.join(ref, 'ps_*.metallib'))[0])
vs = dis(vs_ml['bc'], 'vs_orig')
ps = dis(ps_ml['bc'], 'ps_orig')
vs_fn = re.search(r'define [^@]*@(\w+)', vs).group(1)
ps_fn = re.search(r'define [^@]*@(\w+)', ps).group(1)
# the writer must reproduce airconv's own file byte for byte
assert metallib(vs_ml, vs_fn, 0, vs_ml['bc']) == vs_ml['raw'], 'container writer differs from airconv (vs)'
assert metallib(ps_ml, ps_fn, 1, ps_ml['bc']) == ps_ml['raw'], 'container writer differs from airconv (ps)'
a_vs_bc = unwrap(open(os.path.join(ref, 'apple_vs_blit_quad.bc'), 'rb').read())
a_fs_bc = unwrap(open(os.path.join(ref, 'apple_fs_blit_quad.bc'), 'rb').read())
a_vs = dis(a_vs_bc, 'apple_vs')
a_fs = dis(a_fs_bc, 'apple_fs')


def V(bc):
    return (metallib(vs_ml, vs_fn, 0, bc), vs_fn)


def P(bc):
    return (metallib(ps_ml, ps_fn, 1, bc), ps_fn)


def AV(bc):
    return (metallib(vs_ml, 'vs_blit_quad', 0, bc), 'vs_blit_quad')


def AF(bc):
    return (metallib(ps_ml, 'fs_blit_quad', 1, bc), 'fs_blit_quad')


# skeleton: no arguments, one position output, airconv's module header
head = vs[:vs.index('%argument_buffer_struct')]
skel = head + ('define { <4 x float> } @%s() local_unnamed_addr {\nentry:\n'
               '  ret { <4 x float> } { <4 x float> <float 0.0, float 0.0, float 0.0, float 1.0> }\n}\n\n' % vs_fn)
md = vs[vs.index('!llvm.module.flags'):]
md = re.sub(r'!8 = .*', '!8 = !{{ <4 x float> } ()* @%s, !9, !12}' % vs_fn, md)
md = re.sub(r'!9 = .*', '!9 = !{!10}', md)
md = re.sub(r'!12 = .*', '!12 = !{}', md)
md = re.sub(r'!1[13-9] = .*\n|!20 = .*\n', '', md)
skel += md
PK = '<{ <4 x float>, <4 x float> }>'
RET = ('  ret %s <{ <4 x float> <float 0.0, float 0.0, float 0.0, float 1.0>, <4 x float> zeroinitializer }>\n' % PK)
bare = re.sub(r'declare <2 x float> @air\.fma.*\n', '', body(vs, RET))

# (name, vertex (lib, fn) or None for the MSL v_tc, fragment (lib, fn) or None)
# Ordered by what they decide: first whether Apple's own bitcode passes in our
# container (control), then whether LLVM 15/14's writer breaks it, then
# Apple's module metadata on airconv's shaders.
tests = [
    ('apple VS raw',            AV(a_vs_bc), None),
    ('apple VS+FS raw',         AV(a_vs_bc), AF(a_fs_bc)),
    ('apple VS llvm15',         AV(asm(a_vs, 'a_vs_15')), None),
    ('apple VS llvm14',         AV(asm(a_vs, 'a_vs_14', 14)), None),
    ('apple VS+FS llvm15',      AV(asm(a_vs, 'a_vs_15')), AF(asm(a_fs, 'a_fs_15'))),
    ('skel',                    V(asm(skel, 'skel')), None),
    ('skel llvm14',             V(asm(skel, 'skel_14', 14)), None),
    ('skel +meta',              V(asm(meta(skel), 'skel_meta')), None),
    ('skel +ident',             V(asm(add_ident(skel), 'skel_ident')), None),
    ('skel +opts',              V(asm(add_opts(skel), 'skel_opts')), None),
    ('skel +attrs',             V(asm(add_attrs(skel), 'skel_attrs')), None),
    ('skel sdk27.3',            V(asm(sdk(skel, 27, 3), 'skel_sdk')), None),
    ('skel plain-triple',       V(asm(skel.replace('air64_v25-', 'air64-'), 'skel_tri')), None),
    ('VS orig +meta',           V(asm(meta(vs), 'vs_meta')), None),
    ('VS orig +meta sdk27.3',   V(asm(sdk(meta(vs), 27, 3), 'vs_meta_sdk')), None),
    ('VS orig llvm15',          V(asm(vs, 'vs_15')), None),
    ('VS orig llvm14',          V(asm(vs, 'vs_14', 14)), None),
    ('VS bare',                 V(asm(bare, 'vs_bare')), None),
    ('VS orig',                 (vs_ml['raw'], vs_fn), None),
    ('PS orig +meta',           None, P(asm(meta(ps), 'ps_meta'))),
    ('PS orig llvm15',          None, P(asm(ps, 'ps_15'))),
    ('PS orig',                 None, (ps_ml['raw'], ps_fn)),
    ('VS+PS orig +meta',        V(asm(meta(vs), 'vs_meta')), P(asm(meta(ps), 'ps_meta'))),
]

blobs = {}
def ident_of(data):
    h = hashlib.sha1(data).hexdigest()[:12]
    blobs.setdefault(h, data)
    return 'pso_probe_lib_' + h


with open(out_h, 'w') as f:
    f.write('/* Madeira iOS 16: PSO probe tests for People Playground\'s blit shaders (%s, %s),\n'
            ' * which iOS 16.2 fails with "Compiler encountered an internal error", next to\n'
            ' * DXMT\'s own Apple-built vs/fs_blit_quad. Built offline by\n'
            ' * ci/tools/gen-pso-probe.py from ci/tools/pso-probe-ref. Generated, do not edit. */\n\n'
            % (vs_fn[:11], ps_fn[:11]))
    rows = []
    for name, v, p in tests:
        rows.append((name, (ident_of(v[0]), v[1]) if v else None, (ident_of(p[0]), p[1]) if p else None))
    for h, data in blobs.items():
        f.write('static const unsigned char pso_probe_lib_%s[%d] = {\n' % (h, len(data)))
        for i in range(0, len(data), 24):
            f.write('  ' + ','.join(str(b) for b in data[i:i + 24]) + ',\n')
        f.write('};\n')
    f.write('static const struct {\n  const char *name;\n  const unsigned char *vs; unsigned vs_size; const char *vs_fn;\n'
            '  const unsigned char *fs; unsigned fs_size; const char *fs_fn;\n} pso_probe_tests[] = {\n')
    for name, v, p in rows:
        vv = '%s, sizeof(%s), "%s"' % (v[0], v[0], v[1]) if v else '0, 0, 0'
        pp = '%s, sizeof(%s), "%s"' % (p[0], p[0], p[1]) if p else '0, 0, 0'
        f.write('  {"%s", %s, %s},\n' % (name, vv, pp))
    f.write('};\n')
print(len(tests), 'tests,', len(blobs), 'libraries,', sum(map(len, blobs.values())), 'bytes')
