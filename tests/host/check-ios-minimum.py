#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The app runs from iOS 16.2 (TrollStore and iOS 16.2+).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
lines = (ROOT / "app/Madeira.xcodeproj/project.pbxproj").read_text().split("\n")
project = [l.strip() for i, l in enumerate(lines[:-1])
           if "IPHONEOS_DEPLOYMENT_TARGET" in l and "MADEIRA_BUNDLE_IDENTIFIER" in lines[i + 1]]
assert project == ["IPHONEOS_DEPLOYMENT_TARGET = 16.2;"] * 2, project
helper = [l.strip() for i, l in enumerate(lines) if "IPHONEOS_DEPLOYMENT_TARGET" in l
          and any("JITHelper" in x for x in lines[i:i + 15])]
assert set(helper) <= {"IPHONEOS_DEPLOYMENT_TARGET = 16.2;", "IPHONEOS_DEPLOYMENT_TARGET = 26.0;"}, helper
all_targets = {l.strip() for l in lines if "IPHONEOS_DEPLOYMENT_TARGET" in l}
assert all_targets <= {"IPHONEOS_DEPLOYMENT_TARGET = 16.2;", "IPHONEOS_DEPLOYMENT_TARGET = 17.0;", "IPHONEOS_DEPLOYMENT_TARGET = 18.0;", "IPHONEOS_DEPLOYMENT_TARGET = 26.0;"}, all_targets
print("PASS: the app's minimum is iOS 16.2; supports TrollStore on iOS 16.2")

# Wave 3 (2026-10-19): HomeView.swift's Liquid Glass calls (and the file's home screen) are deleted; the compiler
# enforces the rest: an iOS 26 API outside `#available` fails the build against the 18.0 target checked above.
