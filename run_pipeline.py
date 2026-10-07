"""Run comparable experiments, evaluation and offline presentation export."""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=config.LLM_MODEL)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--test-python', help='Target test interpreter, required for refactoring')
    parser.add_argument('--reports-only', action='store_true', help='No API calls; regenerate saved reports')
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('workers must be positive')
    steps = []

    def run(label, script, *options):
        print(f'\n=== {label} ===', flush=True)
        result = subprocess.run([sys.executable, '-u', script, *options], cwd=config.PROJECT_DIR)
        steps.append({'step': label, 'returncode': result.returncode})
        return result.returncode == 0

    if not args.reports_only:
        common = ['--model', args.model, '--mode', 'chain', '--workers', str(args.workers)]
        ranked = run('LLM with RAG', 'run_prioritisation.py', *common)
        if ranked:
            run('Matched ablation (same model and agent chain)', 'run_prioritisation.py', *common, '--no-context')
            if args.test_python:
                run('Refactoring and test gate', 'run_refactoring.py', '--model', args.model,
                    '--test-python', str(Path(args.test_python).resolve()))
        else:
            print('LLM run incomplete; dependent experiments deferred. Inspect the error above.')
    run('Evaluation', 'run_evaluation.py')
    run('Offline dashboard', 'run_dashboard.py')
    run('Readiness audit', 'run_doctor.py')
    status = {'generated': datetime.now(timezone.utc).isoformat(), 'steps': steps}
    Path(config.DATA_DIR, 'pipeline_status.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
    return int(any(s['returncode'] for s in steps))


if __name__ == '__main__':
    raise SystemExit(main())
