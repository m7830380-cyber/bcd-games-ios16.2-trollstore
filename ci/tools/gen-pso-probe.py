#!/usr/bin/env python3
"""Build the iOS 16 PSO probe variants from People Playground's failing blit
shaders (vs_3139aeb0 / ps_a1f3d64f) and write pso_probe_data.h.

usage: gen_probe.py <vs.metallib> <ps.metallib> <workdir> <out.h>
Each variant changes one thing; the probe compiles each on the device."""
import hashlib, os, re, struct, subprocess, sys

vs_ml, ps_ml, work, out_h = sys.argv[1:5]
os.makedirs(work, exist_ok=True)


def run(*a):
    subprocess.run(a, check=True)


def blob_of(ml):
    d = open(ml, 'rb').read()
    off, sz = struct.unpack('<QQ', d[0x48:0x58])
    return d, d[off:off + sz]


def raw_bc(blob):
    o, s = struct.unpack('<II', blob[8:16])
    return blob[o:o + s]


def metallib(template, bc, major=None, minor=None, os_byte=None):
    """Template's container with its bitcode replaced (same function name)."""
    d = bytearray(template)
    fl_off, fl_sz = struct.unpack('<QQ', d[0x18:0x28])
    bc_off = struct.unpack('<Q', d[0x48:0x50])[0]
    blob = struct.pack('<IIIII', 0x0B17C0DE, 0, 20, len(bc), 0xFFFFFFFF) + bc
    blob += b'\0' * (-len(blob) % 16)
    q = fl_off + 8
    while d[q:q + 4] != b'ENDT':
        t = bytes(d[q:q + 4]); l = struct.unpack('<H', d[q + 4:q + 6])[0]; v = q + 6
        if t == b'HASH': d[v:v + 32] = hashlib.sha256(blob).digest()
        if t == b'MDSZ': d[v:v + 8] = struct.pack('<Q', len(blob))
        q = v + l
    d = d[:bc_off] + blob
    struct.pack_into('<Q', d, 0x10, len(d))
    struct.pack_into('<Q', d, 0x50, len(blob))
    if major is not None: struct.pack_into('<HH', d, 6, major, minor)
    if os_byte is not None: d[11] = os_byte
    return bytes(d)


def dis(bc, name):
    p = os.path.join(work, name)
    open(p + '.bc', 'wb').write(bc)
    run('llvm-dis-15', p + '.bc', '-o', p + '.ll')
    return open(p + '.ll').read()


def asm(text, name, ver=15):
    p = os.path.join(work, name)
    open(p + '.ll', 'w').write(text)
    run('llvm-as-%d' % ver, p + '.ll', '-o', p + '.bc')
    return open(p + '.bc', 'rb').read()


vs_tmpl, vs_blob = blob_of(vs_ml)
ps_tmpl, ps_blob = blob_of(ps_ml)
vs = dis(raw_bc(vs_blob), 'vs_orig')
ps = dis(raw_bc(ps_blob), 'ps_orig')
vs_fn = re.search(r'define [^@]*@(\w+)', vs).group(1)
ps_fn = re.search(r'define [^@]*@(\w+)', ps).group(1)


def sub(text, old, new, count=1):
    assert old in text, old
    return text.replace(old, new) if count == 0 else text.replace(old, new, count)


def nofma(t):
    def rep(m):
        a, b, c = m.group(2).split(', <2 x float> ')
        a = a.replace('<2 x float> ', '', 1)
        tmp = '%fmam' + m.group(1)[1:]
        return '%s = fmul <2 x float> %s, %s\n  %s = fadd <2 x float> %s, %s' % (
            tmp, a, b, m.group(1), tmp, c)
    t2 = re.sub(r'(%\w+) = call <2 x float> @air\.fma\.v2f32\((.*)\)', rep, t)
    assert t2 != t
    return t2


def body(t, new_body):
    """Replace the function body of the only defined function."""
    s = t.index('\n', t.index('define ')) - 1
    assert t[s] == '{'
    e = t.index('\n}\n', s)
    return t[:s + 1] + '\nentry:\n' + new_body + t[e:]


PK = '<{ <4 x float>, <4 x float> }>'
NP = '{ <4 x float>, <4 x float> }'
vs_variants = []   # (name, metallib bytes)
vs_variants.append(('orig', open(vs_ml, 'rb').read()))
vs_variants.append(('reas', metallib(vs_tmpl, asm(vs, 'vs_reas'))))
vs_variants.append(('llvm14', metallib(vs_tmpl, asm(vs, 'vs_l14', 14))))
vs_variants.append(('nopack', metallib(vs_tmpl, asm(vs.replace(PK, NP), 'vs_nopack'))))
vs_variants.append(('noflags', metallib(vs_tmpl, asm(re.sub(r'!llvm\.module\.flags = .*\n', '', vs), 'vs_noflags'))))
t = sub(vs, 'br i1 %11, label %19, label %12', 'br i1 true, label %19, label %12')
t = sub(t, 'br i1 %27, label %entrybb, label %28', 'br i1 true, label %entrybb, label %28')
vs_variants.append(('nopull', metallib(vs_tmpl, asm(t, 'vs_nopull'))))
t = re.sub(r'%43 = getelementptr .*', '%43 = add i32 0, 0', vs)
t = re.sub(r'%44 = load .*', '%44 = add i32 0, 0', t)
t = re.sub(r'%45 = load .*', '%45 = bitcast <4 x float> <float 1.0, float 1.0, float 0.0, float 0.0> to <4 x i32>', t)
vs_variants.append(('noab', metallib(vs_tmpl, asm(t, 'vs_noab'))))
vs_variants.append(('nofma', metallib(vs_tmpl, asm(nofma(vs), 'vs_nofma'))))
t = re.sub(r'%62 = select .*', '%62 = fadd <4 x float> %.sroa.01.12.vec.insert, zeroinitializer', vs)
vs_variants.append(('nosel', metallib(vs_tmpl, asm(t, 'vs_nosel'))))
RET = ('  ret %s <{ <4 x float> <float 0.0, float 0.0, float 0.0, float 1.0>, <4 x float> zeroinitializer }>\n' % PK)
bare = body(vs, RET)
bare = re.sub(r'declare <2 x float> @air\.fma.*\n', '', bare)
vs_variants.append(('bare', metallib(vs_tmpl, asm(bare, 'vs_bare'))))

# The skeleton: no arguments, one position output, airconv's module header.
head = vs[:vs.index('%argument_buffer_struct')]
skel = head + ('define { <4 x float> } @%s() local_unnamed_addr {\nentry:\n'
               '  ret { <4 x float> } { <4 x float> <float 0.0, float 0.0, float 0.0, float 1.0> }\n}\n\n' % vs_fn)
md = vs[vs.index('!llvm.module.flags'):]
md = re.sub(r'!8 = .*', '!8 = !{{ <4 x float> } ()* @%s, !9, !12}' % vs_fn, md)
md = re.sub(r'!9 = .*', '!9 = !{!10}', md)
md = re.sub(r'!12 = .*', '!12 = !{}', md)
md = re.sub(r'!1[13-9] = .*\n|!20 = .*\n', '', md)
skel += md
vs_variants.append(('skel', metallib(vs_tmpl, asm(skel, 'vs_skel'))))
vs_variants.append(('skel-llvm14', metallib(vs_tmpl, asm(skel, 'vs_skel_l14', 14))))
vs_variants.append(('skel-triple', metallib(vs_tmpl, asm(
    skel.replace('air64_v25-apple-ios16.0.0', 'air64-apple-ios16.0.0'), 'vs_skel_tri'))))
vs_variants.append(('skel-v26', metallib(vs_tmpl, asm(
    skel.replace('air64_v25-apple-ios16.0.0', 'air64_v26-apple-ios16.0.0')
        .replace('!{i32 2, i32 5, i32 0}', '!{i32 2, i32 6, i32 0}')
        .replace('!"Metal", i32 3, i32 0, i32 0', '!"Metal", i32 3, i32 1, i32 0'), 'vs_skel_v26'))))
skel_bc = asm(skel, 'vs_skel')
vs_variants.append(('skel-hdr26', metallib(vs_tmpl, skel_bc, 2, 6)))
vs_variants.append(('skel-hdr-macos', metallib(vs_tmpl, skel_bc, os_byte=0x81)))

ps_variants = []
ps_variants.append(('orig', open(ps_ml, 'rb').read()))
ps_variants.append(('reas', metallib(ps_tmpl, asm(ps, 'ps_reas'))))
ps_variants.append(('nopack', metallib(ps_tmpl, asm(ps.replace('<{ <4 x float> }>', '{ <4 x float> }'), 'ps_nopack'))))
t = body(ps, '  ret <{ <4 x float> }> <{ <4 x float> <float 1.0, float 0.0, float 1.0, float 1.0> }>\n')
t = re.sub(r'declare \{ <4 x float>, i8 \} @air\.sample.*\n', '', t)
ps_variants.append(('bare', metallib(ps_tmpl, asm(t, 'ps_bare'))))

# Likely passes first, so a short session still logs the informative ones.
ORDER = ['skel', 'skel-llvm14', 'skel-triple', 'skel-v26', 'skel-hdr26', 'skel-hdr-macos', 'bare', 'nopack',
         'noflags', 'nopull', 'noab', 'nofma', 'nosel', 'llvm14', 'reas', 'orig']
vs_variants.sort(key=lambda v: ORDER.index(v[0]))
ps_variants.sort(key=lambda v: ['bare', 'nopack', 'reas', 'orig'].index(v[0]))
with open(out_h, 'w') as f:
    f.write('/* Madeira iOS 16: variants of People Playground\'s blit shaders (%s, %s),\n'
            ' * which iOS 16.2 fails with "Compiler encountered an internal error". Each\n'
            ' * changes one thing; built offline with LLVM 15 (14 where named) by\n'
            ' * ci/tools/gen-pso-probe.py. Generated, do not edit. */\n\n' % (vs_fn[:11], ps_fn[:11]))
    for kind, lst in (('vs', vs_variants), ('ps', ps_variants)):
        for name, data in lst:
            ident = 'pso_probe_%s_%s' % (kind, re.sub(r'\W', '_', name))
            f.write('static const unsigned char %s[%d] = {\n' % (ident, len(data)))
            for i in range(0, len(data), 24):
                f.write('  ' + ','.join(str(b) for b in data[i:i + 24]) + ',\n')
            f.write('};\n')
        f.write('static const struct { const char *name; const unsigned char *data; unsigned size; } '
                'pso_probe_%s[] = {\n' % kind)
        for name, data in lst:
            ident = 'pso_probe_%s_%s' % (kind, re.sub(r'\W', '_', name))
            f.write('  {"%s", %s, sizeof(%s)},\n' % (name, ident, ident))
        f.write('};\n')
    f.write('static const char pso_probe_vs_function[] = "%s";\n' % vs_fn)
    f.write('static const char pso_probe_ps_function[] = "%s";\n' % ps_fn)
print('vs:', [n for n, _ in vs_variants]); print('ps:', [n for n, _ in ps_variants])
