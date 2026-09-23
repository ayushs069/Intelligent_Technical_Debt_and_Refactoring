# Intelligent Technical Debt & Refactoring

> **Phase 1 — Static Analysis Foundation**

A Python static-analysis pipeline that runs **Radon**, **Ruff**, **Pylint**, and **Vulture** against any Python repository and produces a unified technical-debt dataset.

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run analysis on a target repository

```bash
# Analyse a specific repo
python run_analysis.py --target /path/to/python/repo

# Analyse the current directory
python run_analysis.py

# Custom output location
python run_analysis.py --target /path/to/repo --output results.json
```

### 3. Output

Results are saved to `data/debt_dataset.json` (default). Each issue follows this schema:

```json
{
  "file": "path/to/file.py",
  "function": "function_name",
  "line": 42,
  "issue_type": "high_complexity | lint | convention | dead_code | maintainability",
  "tool": "radon | ruff | pylint | vulture",
  "severity": "HIGH | MEDIUM | LOW",
  "complexity": 15,
  "maintainability_index": 45.2,
  "loc": 120,
  "message": "Description of the issue",
  "static_score": 7.5
}
```

**`static_score`** (0–10, higher = worse debt) is computed as:
- Complexity: `min(complexity / 5, 4.0)`
- Maintainability: `max(0, (100 - MI) / 25)` (capped at 4.0)
- Severity bonus: HIGH = 2, MEDIUM = 1, LOW = 0

---

## Running Tests

```bash
python -m pytest tests/ -v
```

Tests use temporary Python files with intentional code smells — no external repo needed.

---

## Project Structure

```
├── analyzer/
│   ├── analyzer.py          # Orchestrator
│   ├── radon_analyzer.py    # Radon CC + MI
│   ├── ruff_analyzer.py     # Ruff linting
│   ├── pylint_analyzer.py   # Pylint linting
│   ├── vulture_analyzer.py  # Dead code detection
│   └── normalizer.py        # Merge + static_score
├── data/                    # Output directory
├── tests/                   # Pytest suite
├── config.py                # Default settings
├── run_analysis.py          # CLI entry point
└── requirements.txt
```

---

## Tools Used

| Tool | Purpose |
|------|---------|
| [Radon](https://radon.readthedocs.io/) | Cyclomatic complexity & maintainability index |
| [Ruff](https://docs.astral.sh/ruff/) | Fast Python linter |
| [Pylint](https://pylint.readthedocs.io/) | Comprehensive linter |
| [Vulture](https://github.com/jendrikseipp/vulture) | Dead code detection |
