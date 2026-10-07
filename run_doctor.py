"""Read-only project audit; writes a transparent readiness report, never invents results."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import config
from agents.prompts import read_function_source
from evaluation.expert import load_raters


def audit():
    paths = config.dataset_paths()
    def load(key, default):
        path = Path(paths[key])
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default
    items = load('items', [])
    ids = {i['id'] for i in items}
    truth = load('ground_truth', {})
    checks = []
    def check(name, ok, detail):
        checks.append({'check': name, 'passed': bool(ok), 'detail': detail})
    check('Dataset integrity', bool(items) and len(ids) == len(items) and ids <= set(truth),
          f'{len(items)} candidate functions, {len(truth)} ground-truth labels')
    source_count = sum(read_function_source(i) != '<source unavailable>' for i in items)
    check('Snapshot source available', bool(items) and source_count == len(items), f'{source_count}/{len(items)} readable')
    for key, label in [('llm_rankings', 'LLM + RAG experiment'), ('llm_rankings_nocontext', 'Matched no-context experiment')]:
        rows = load(key, [])
        valid = {r['id'] for r in rows if 'llm_rank' in r and 'error' not in r}
        check(label, bool(items) and ids == valid, f'{len(valid)}/{len(items)} successful rankings')
    raters = load_raters(config.EXPERT_DIR)
    sample = set(load('expert_sample', []))
    complete_raters = sum(bool(sample) and sample <= set(r['scores']) for r in raters)
    check('Independent human evaluation', complete_raters >= 2,
          f'{complete_raters} complete reviewers; requires at least two real independent reviewers')
    ref = load('refactoring', {}).get('summary', {})
    check('Measured refactoring experiment', ref.get('attempted', 0) > 0,
          f"{ref.get('succeeded', 0)}/{ref.get('attempted', 0)} proposals passed the gate")
    check('API credential configured', bool(os.getenv('OPENAI_API_KEY') or os.getenv('ANTHROPIC_API_KEY')),
          'Presence only; account balance and access are not verified by this audit')
    return {'generated': datetime.now(timezone.utc).isoformat(),
            'research_complete': all(c['passed'] for c in checks), 'checks': checks}


def main():
    result = audit()
    Path(config.DATA_DIR, 'readiness.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    lines = ['# Readiness audit', '', f"Generated: {result['generated']}", '',
             'This audit checks saved evidence. It does not certify production security or fabricate missing experiments.', '']
    for c in result['checks']:
        lines.append(f"- {'PASS' if c['passed'] else 'PENDING'} — {c['check']}: {c['detail']}")
    Path(config.REPORTS_DIR, 'READINESS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
