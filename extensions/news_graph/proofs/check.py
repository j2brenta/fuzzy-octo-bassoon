"""Check pinned Lean sources; no installation, download or inference.

Run from any directory: python3 extensions/news_graph/proofs/check.py --lean /path/to/lean
Optional --report must name a file outside the Git worktree.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lean', default=os.environ.get('LEAN_BIN') or shutil.which('lean'))
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    if not args.lean:
        print('BLOCKED: Lean 4.8.0 is not configured; set LEAN_BIN or --lean. Nothing was installed.')
        return 2
    args.lean = str(Path(shutil.which(args.lean) or args.lean).resolve())
    version = subprocess.run([args.lean, '--version'], capture_output=True, text=True, timeout=15)
    if version.returncode or not re.search(r'\bversion 4\.8\.0(?:,|\))', version.stdout):
        print('BLOCKED: this checkpoint is pinned to Lean 4.8.0.')
        return 2
    results = []
    for name in ('SelectionBudget.lean', 'NewsGraph.lean'):
        source = Path(__file__).parent / name
        code = '\n'.join(line.split('--', 1)[0] for line in source.read_text().splitlines())
        if re.search(r'\b(sorry|admit|axiom|unsafe|native_decide)\b|debug\.skipKernelTC|implemented_by', code):
            print('FAIL: unchecked declaration/shortcut in ' + name)
            return 1
        run = subprocess.run([args.lean, str(source.resolve())], cwd=root, capture_output=True, text=True, timeout=60)
        log = run.stdout + run.stderr
        declared = re.findall(r'^theorem\s+(\w+)', code, re.M)
        audits = re.findall(r"^'[^']+' (?:depends on axioms:|does not depend on any axioms)", log, re.M)
        success = run.returncode == 0 and 'sorryAx' not in log and len(audits) == len(declared)
        if name == 'NewsGraph.lean': success = success and 'NEWS_TRACE_PASS' in log
        results.append({'file': name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                        'status': 'PASS' if success else 'FAIL', 'theorems': len(declared), 'compiler_log': log})
    report = {'lean_version': version.stdout.strip(), 'results': results,
              'status': 'PASS' if all(r['status']=='PASS' for r in results) else 'FAIL',
              'scope': 'Lean specifications and shared examples; not a whole-implementation proof'}
    if args.report:
        dest = args.report.resolve()
        if dest.is_relative_to(root):
            raise ValueError('proof evidence report must be outside the worktree')
        dest.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    for row in results:
        print(f"{row['status']}: {row['file']} ({row['theorems']} theorems)")
        if row['status'] != 'PASS': print(row['compiler_log'])
    return 0 if report['status']=='PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
