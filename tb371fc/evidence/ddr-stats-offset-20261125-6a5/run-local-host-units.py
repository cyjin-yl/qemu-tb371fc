#!/usr/bin/env python3
"""Run unchanged host unit assertions, routing temporary files to shared memory."""
from pathlib import Path
import runpy
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
TEMP = ROOT / '.work/ddr-host-unit-fixtures'
TEMP.mkdir(parents=True, exist_ok=False)
original_mkdtemp = tempfile.mkdtemp
def shared_memory_mkdtemp(suffix=None, prefix=None, dir=None):
    # Only storage location changes. Assertions, socket operations, subprocess
    # execution and verdict handling remain unchanged; failures are retained.
    if dir is None or str(dir) == '/tmp':
        dir = str(TEMP)
    return original_mkdtemp(suffix=suffix, prefix=prefix, dir=dir)
tempfile.tempdir = str(TEMP)
tempfile.mkdtemp = shared_memory_mkdtemp
script = ROOT / 'tb371fc/scripts/test_ci_device_tests.py'
sys.path.insert(0, str(script.parent))
sys.argv = [str(script), '-v']
runpy.run_path(str(script), run_name='__main__')
