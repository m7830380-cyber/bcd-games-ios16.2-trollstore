#!/usr/bin/env python3
"""Add a source file of app/Madeira to the Madeira target (next to StikJITHelper.swift).
usage: pbx-add.py <filename> <buildfile-id> <fileref-id> <lastKnownFileType>"""
import sys
name, bid, fid, ftype = sys.argv[1:5]
p = 'app/Madeira.xcodeproj/project.pbxproj'
s = open(p).read()
if f'/* {name} */' in s:
    print(f'{name}: already in the project'); sys.exit(0)
anchor_b = '\t\tA1000035 /* StikJITHelper.swift in Sources */ = {isa = PBXBuildFile; fileRef = A2000036 /* StikJITHelper.swift */; };\n'
anchor_f = '\t\tA2000036 /* StikJITHelper.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = StikJITHelper.swift; sourceTree = "<group>"; };\n'
anchor_g = '\t\t\t\tA2000036 /* StikJITHelper.swift */,\n'
anchor_s = '\t\t\t\tA1000035 /* StikJITHelper.swift in Sources */,\n'
for a in (anchor_b, anchor_f, anchor_g, anchor_s):
    assert s.count(a) == 1, a
s = s.replace(anchor_b, anchor_b + f'\t\t{bid} /* {name} in Sources */ = {{isa = PBXBuildFile; fileRef = {fid} /* {name} */; }};\n')
s = s.replace(anchor_f, anchor_f + f'\t\t{fid} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = {ftype}; path = {name}; sourceTree = "<group>"; }};\n')
s = s.replace(anchor_g, anchor_g + f'\t\t\t\t{fid} /* {name} */,\n')
s = s.replace(anchor_s, anchor_s + f'\t\t\t\t{bid} /* {name} in Sources */,\n')
open(p, 'w').write(s)
print(f'{name}: added')
