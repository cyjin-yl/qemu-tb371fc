#!/usr/bin/env python3
"""Pinned local entry point; no shell, endpoint discovery or automatic retry."""
import hashlib
from pathlib import Path
import types

MODULE_SHA256 = '8409e1ada6b41f6a1a5ec685cb267d890e3ed2151d5c6d265122d51c361a791a'
p = Path(__file__).with_name('storage_supervision_batch_v3.py')
raw = p.read_bytes()
if hashlib.sha256(raw).hexdigest() != MODULE_SHA256:
    raise SystemExit('supervision module identity mismatch')
m = types.ModuleType('storage_supervision')
m.__file__ = str(p)
exec(compile(raw, str(p), 'exec'), m.__dict__)
if __name__ == '__main__':
    raise SystemExit(m.entry('relay'))
