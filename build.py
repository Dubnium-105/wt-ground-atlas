#!/usr/bin/env python3
"""Build recommendations from local snapshots, then assemble a bounded static site."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
from build_web import build, check_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true', help='Recalculate recommendations even when input hashes match.')
    parser.add_argument('--skip-decisions', action='store_true', help='Assemble existing recommendations; reject stale inputs.')
    parser.add_argument('--output', type=Path, help='Generated site directory (default: dist/site).')
    parser.add_argument('--probability-mode', choices=('disabled', 'external', 'embedded'))
    parser.add_argument('--probability-base-url', help='External immutable probability release directory; implies external mode.')
    args = parser.parse_args()
    try:
        check_inputs(ROOT)
        if not args.skip_decisions:
            node = os.environ.get('GROUNDOPS_NODE') or shutil.which('node')
            if not node:
                parser.error('Node.js 20+ is required for recommendation builds. Serving dist/site requires only a static HTTP server.')
            command = [node, str(ROOT / 'scripts/build_decisions.mjs')]
            if args.force:
                command.append('--force')
            subprocess.run(command, cwd=ROOT, check=True)
        build(ROOT, args.output, args.probability_mode, args.probability_base_url)
    except (FileNotFoundError, ValueError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
