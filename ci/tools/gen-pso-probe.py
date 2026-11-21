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


# ---- round 2: the encoding and metadata are cleared (skel/bare pass, orig fails
# with Apple's metadata too), so bisect the body. Each variant keeps one feature.
def renum(t):
    """Name unnamed values >= first so cut-down bodies need no renumbering."""
    s = t.index('define '); e = t.index('\n}\n', s)
    f = re.sub(r'%(\d+)\b', lambda m: '%' + m.group(1) if int(m.group(1)) < NARGS[0] else '%v' + m.group(1), t[s:e])
    return t[:s] + f + t[e:]


def ret_vs(pos, tc):
    return ('  %%r0 = insertvalue %s zeroinitializer, <4 x float> %s, 0\n'
            '  %%r1 = insertvalue %s %%r0, <4 x float> %s, 1\n  ret %s %%r1\n' % (PK, pos, PK, tc, PK))


ONE = '<float 0.0, float 0.0, float 0.0, float 1.0>'
NOFMA = re.compile(r'declare <2 x float> @air\.fma.*\n')
def vsb(code, pos=ONE, tc='zeroinitializer'):
    return NOFMA.sub('', body(vs, code + ret_vs(pos, tc)))

def f4(v, ty):
    """<4 x float> from an i64/float/ptr value, as code + result name."""
    if ty == 'i64':
        return ('  %%c0 = trunc i64 %s to i32\n  %%c1 = bitcast i32 %%c0 to float\n'
                '  %%c2 = insertelement <4 x float> zeroinitializer, float %%c1, i64 0\n' % v, '%c2')
    if ty == 'float':
        return ('  %%c2 = insertelement <4 x float> zeroinitializer, float %s, i64 0\n' % v, '%c2')
    c, r = f4('%c9', 'i64')
    return ('  %%c9 = ptrtoint %s %s to i64\n' % (ty, v)) + c, r

AB_CB = ('  %a0 = getelementptr inbounds %argument_buffer_struct, %argument_buffer_struct addrspace(2)* %5, i64 0, i32 0\n'
         '  %a1 = load <4 x i32> addrspace(2)*, <4 x i32> addrspace(2)* addrspace(2)* %a0, align 8\n')
VB = ('  %p0 = bitcast i32 addrspace(2)* %0 to i8 addrspace(1)* addrspace(2)*\n'
      '  %p1 = load i8 addrspace(1)*, i8 addrspace(1)* addrspace(2)* %p0, align 8\n'
      '  %p2 = mul i32 %1, 20\n  %p3 = sext i32 %p2 to i64\n'
      '  %p4 = getelementptr i8, i8 addrspace(1)* %p1, i64 %p3\n')

c, r = f4('%a1', '<4 x i32> addrspace(2)*')
vs_ab_ptr = vsb(AB_CB + c, tc=r)
vs_ab_cb = vsb(AB_CB + '  %a2 = load <4 x i32>, <4 x i32> addrspace(2)* %a1, align 16\n'
               '  %a3 = bitcast <4 x i32> %a2 to <4 x float>\n', tc='%a3')
c, r = f4('%p1', 'i8 addrspace(1)*')
vs_vb_ptr = vsb(VB + c, tc=r)
vs_vb_f1 = vsb(VB + '  %p5 = bitcast i8 addrspace(1)* %p4 to float addrspace(1)*\n'
               '  %p6 = load float, float addrspace(1)* %p5, align 4\n' + f4('%p6', 'float')[0], tc='%c2')
vs_vb_f3 = vsb(VB + '  %p5 = bitcast i8 addrspace(1)* %p4 to <3 x float> addrspace(1)*\n'
               '  %p6 = load <3 x float>, <3 x float> addrspace(1)* %p5, align 4\n'
               '  %p7 = shufflevector <3 x float> %p6, <3 x float> undef, <4 x i32> <i32 0, i32 1, i32 2, i32 undef>\n', tc='%p7')
vs_vb_f4 = vsb(VB + '  %p5 = bitcast i8 addrspace(1)* %p4 to <4 x float> addrspace(1)*\n'
               '  %p6 = load <4 x float>, <4 x float> addrspace(1)* %p5, align 4\n', tc='%p6')
vs_math = body(vs, '  %m0 = uitofp i32 %1 to float\n  %m1 = insertelement <2 x float> zeroinitializer, float %m0, i64 0\n'
               '  %m2 = call <2 x float> @air.fma.v2f32(<2 x float> %m1, <2 x float> <float 2.0, float 2.0>, <2 x float> <float -1.0, float -1.0>)\n'
               '  %m3 = shufflevector <2 x float> %m2, <2 x float> undef, <4 x i32> <i32 0, i32 1, i32 undef, i32 undef>\n'
               '  %m4 = bitcast <4 x float> %m3 to <4 x i32>\n'
               '  %m5 = and <4 x i32> %m4, <i32 2139095040, i32 2139095040, i32 2139095040, i32 2139095040>\n'
               '  %m6 = icmp ne <4 x i32> %m5, <i32 2139095040, i32 2139095040, i32 2139095040, i32 2139095040>\n'
               '  %m7 = extractelement <4 x i1> %m6, i64 0\n'
               '  %m8 = select i1 %m7, <4 x float> %m3, <4 x float> zeroinitializer\n' + ret_vs('%m8', '%m3'))
NARGS = [6]
i0 = vs.index('entry:\n') + len('entry:\n'); i1 = vs.index('\n', vs.index('\nentrybb:') + 1) + 1
vs_nopull = renum(vs[:i0] + '  %20 = add <4 x i32> zeroinitializer, zeroinitializer\n'
                  + re.sub(r'  %35 = phi .*', '  %35 = add <4 x i32> zeroinitializer, zeroinitializer', vs[i1:]))
vs_nosel = re.sub(r'select i1 (%\w+), i1 true, i1 (%\w+)', r'or i1 \1, \2', vs)
assert vs_nosel != vs

# PS
def psb(code, col):
    return re.sub(r'declare \{ <4 x float>, i8 \} @air\.sample.*\n', '', body(ps,
        code + '  %%r0 = insertvalue <{ <4 x float> }> zeroinitializer, <4 x float> %s, 0\n  ret <{ <4 x float> }> %%r0\n' % col))
ABF = lambda i: '  %%g%d = getelementptr inbounds %%argument_buffer_struct, %%argument_buffer_struct addrspace(2)* %%1, i64 0, i32 %d\n' % (i, i)
ps_bare = psb('', '<float 1.0, float 0.0, float 1.0, float 1.0>')
c, r = f4('%l4', 'i64')
ps_ab_meta = psb(ABF(4) + '  %l4 = load i64, i64 addrspace(2)* %g4, align 8\n' + c, r)
ps_ab_tex = psb(ABF(3) + '  %l3 = load %struct._texture_2d_t addrspace(1)*, %struct._texture_2d_t addrspace(1)* addrspace(2)* %g3, align 8\n'
                + f4('%l3', '%struct._texture_2d_t addrspace(1)*')[0], '%c2')
ps_lod0 = re.sub(r'i1 false, float %\d+, float %\d+, i32 0\)', 'i1 false, float 0.0, float 0.0, i32 0)', ps)
assert ps_lod0 != ps
# direct texture + sampler arguments, like Apple's fs_blit_quad (no argument buffer)
fn_line = re.search(r'define .*\n', ps).group(0)
ps_direct = ps.replace(fn_line, fn_line.replace('%argument_buffer_struct addrspace(2)* %1',
            '%struct._texture_2d_t addrspace(1)* %1, %struct._sampler_t addrspace(2)* %2'))
ps_direct = body(ps_direct,
    '  %s0 = bitcast <4 x float> %0 to <4 x i32>\n'
    '  %s1 = shufflevector <4 x float> %0, <4 x float> undef, <2 x i32> <i32 0, i32 1>\n'
    '  %s2 = call { <4 x float>, i8 } @air.sample_texture_2d.v4f32(%struct._texture_2d_t addrspace(1)* %1, %struct._sampler_t addrspace(2)* %2, <2 x float> %s1, i1 true, <2 x i32> zeroinitializer, i1 false, float 0.0, float 0.0, i32 0)\n'
    '  %s3 = extractvalue { <4 x float>, i8 } %s2, 0\n'
    '  %r0 = insertvalue <{ <4 x float> }> zeroinitializer, <4 x float> %s3, 0\n  ret <{ <4 x float> }> %r0\n')
ps_direct = ps_direct.replace('(<4 x float>, %argument_buffer_struct addrspace(2)*)*',
                              '(<4 x float>, %struct._texture_2d_t addrspace(1)*, %struct._sampler_t addrspace(2)*)*')
ps_direct = re.sub(r'!11 = .*', '!11 = !{!12, !30, !31}', ps_direct)
ps_direct = re.sub(r'!1[3-9] = .*\n', '', ps_direct)
ps_direct += ('!30 = !{i32 1, !"air.texture", !"air.location_index", i32 0, i32 1, !"air.sample", !"air.arg_type_name", !"texture2d<float, sample>", !"air.arg_name", !"t0"}\n'
              '!31 = !{i32 2, !"air.sampler", !"air.location_index", i32 0, i32 1, !"air.arg_type_name", !"sampler", !"air.arg_name", !"s0"}\n')
ps_direct = ps_direct.replace('%argument_buffer_struct = type { %struct._sampler_t addrspace(2)*, %struct._sampler_t addrspace(2)*, i64, %struct._texture_2d_t addrspace(1)*, i64 }\n', '')
# lod 0 plus Apple's spelling of the texture type name
ps_ab_min = ps_lod0.replace('!"texture2d<float,sample>"', '!"texture2d<float, sample>"')

# ---- round 3: round 2 failed exactly the tests that call an air.* intrinsic
# (fma, sample_texture_2d); airconv declares those willreturn nofree nosync,
# Apple's iOS 16 toolchain never does. Strip only those and retry.
def noattr(t):
    out = re.sub(r'^(attributes #\d+ = \{)(.*)\}$',
                 lambda m: m.group(1) + re.sub(r'\s(willreturn|nofree|nosync|mustprogress|nocallback)(?=\s)', '', ' ' + m.group(2) + ' ').rstrip() + ' }',
                 t, flags=re.M)
    assert out != t
    return out.replace('{  ', '{ ')

# (name, vertex (lib, fn) or None for the MSL v_tc, fragment (lib, fn) or None)
tests = [
    ('VS bare',                    V(asm(bare, 'vs_bare')), None),
    ('VS math (as round 2)',       V(asm(vs_math, 'vs_math')), None),
    ('VS math -willreturn/nofree', V(asm(noattr(vs_math), 'vs_math_na')), None),
    ('PS direct -willreturn/nofree', None, P(asm(noattr(ps_direct), 'ps_direct_na'))),
    ('VS orig -willreturn/nofree', V(asm(noattr(vs), 'vs_orig_na')), None),
    ('PS orig -willreturn/nofree', None, P(asm(noattr(ps), 'ps_orig_na'))),
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
        f.write('  {"%s", %s, %s},\n' % (name.replace("\\", "\\\\").replace("\"", "\\\""), vv, pp))
    f.write('};\n')
print(len(tests), 'tests,', len(blobs), 'libraries,', sum(map(len, blobs.values())), 'bytes')
