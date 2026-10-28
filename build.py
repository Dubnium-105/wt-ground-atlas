#!/usr/bin/env python3
"""Rebuild decisions and static shards from the checked-in versioned snapshots."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--force',action='store_true',help='Force recalculation rather than reuse identical input hashes.')
parser.add_argument('--skip-decisions',action='store_true',help='Only assemble static assets; rejects stale decision inputs.')
args=parser.parse_args()
if not args.skip_decisions:
    node=os.environ.get('GROUNDOPS_NODE') or shutil.which('node')
    if not node:raise SystemExit('Node.js is required for reproducible recommendation builds. The prebuilt web/ directory runs without Node.')
    command=[node,str(ROOT/'scripts/build_decisions.mjs')]
    if args.force:command.append('--force')
    subprocess.run(command,cwd=ROOT,check=True)
subprocess.run([sys.executable,str(ROOT/'scripts/build_web.py')],cwd=ROOT,check=True)
