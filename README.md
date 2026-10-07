# Intelligent Technical Debt & Refactoring

## Presentation and Docker quick start

Run `docker compose up --build -d` and open `http://127.0.0.1:8501`.
Without Docker, run `./start-demo.ps1`; the offline HTML is in `reports/requests_dashboard.html`.
See [deployment instructions](DEPLOYMENT.md) and [current evidence](reports/READINESS.md).
The saved LLM experiment currently reports exhausted API credits; it must be rerun with
a funded account. Independent human expert ratings must also be collected.

`python run_pipeline.py --reports-only` refreshes the report without paid calls.
`python run_pipeline.py --model gpt-4.1-mini --test-python targets/.venv-requests/Scripts/python.exe`
runs both matched LLM experiments and the refactoring gate when funded credentials are available.

> **Research question:** Can an LLM combine static-analysis results with repository context to
> prioritise technical debt and recommend refactoring more effectively than static analysis alone?

The pipeline analyses a Python repository with static-analysis tools, enriches every finding with
repository context (call graph, git history, coverage, documentation), retrieves similar debt via
RAG, asks a chain of LLM agents (OpenAI or Anthropic) to prioritise and recommend refactorings, evaluates the
ranking against measurable ground truth, and validates LLM-generated refactorings through a CI gate.

```
target repo @ cutoff ──► Phase 1 static analysis (Radon, Ruff, Pylint, Vulture, clone detector)
                    ──► Phase 2 context (callers, function/file git history, coverage)
                    ──► function-level debt items ──► Phase 3 RAG (ChromaDB + MiniLM)
                                                  ──► Phase 4 agents: Analysis → Priority → Refactor
history after cutoff ──► ground truth ─┐                       │
expert ratings (dashboard) ────────────┼──► Phase 5 evaluation ◄┘ (vs static baselines + ablation)
                                       └──► Phase 6 LLM patches on ai-refactoring/* branches
                                                → CI gate: tests vs baseline, Radon, Ruff, duplication
```

## Evaluation design (why the numbers can be trusted)

* **Temporal split.** The target is checked out at the last commit before a cutoff (default
  `2024-01-01`). Every input a ranker sees — code, metrics, git history — comes from *before* the
  cutoff. Commits *after* the cutoff form the ground truth and are stored in a separate file that
  no ranker reads. This keeps future labels out of the prompts. Public-code memorisation by the pretrained model remains a limitation.
* **Function-level history.** Each commit's changed lines are mapped to the functions they touch
  (`enricher/function_history.py`). Bug fixes are identified from commit and merge (PR) messages,
  excluding typo/docs/CI/typing commits. Sweeping commits that touch more than 25 functions
  (re-formatting, type-annotation passes) are ignored.
* **Three ground truths:** expert developer ratings (primary, as in the plan), future bug-fix
  commits, and future change-proneness.
* **Baselines and ablations:** composite static score, raw complexity, a non-LLM context
  heuristic, and the same LLM *without* repository context. Comparing these separates "the LLM
  helps" from "the context helps".
* **Metrics:** Spearman ρ, Kendall τ, Precision@K, NDCG@10, ROC-AUC, with bootstrap 95% CIs and
  paired bootstrap tests of Δρ.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then put your ANTHROPIC_API_KEY in .env (git-ignored)
```

Target repository with full history plus an isolated venv for its test suite (needed for coverage
and for the Phase 6 CI gate):

```bash
git clone https://github.com/psf/requests.git targets/requests
python -m venv targets/.venv-requests
targets/.venv-requests/Scripts/python -m pip install "pytest>=8" pytest-cov "pytest-httpbin==2.1.0" "httpbin~=0.10.0" trustme pysocks charset_normalizer idna urllib3 certifi
```

(On Linux/macOS use `targets/.venv-requests/bin/python`.)

## Running the pipeline

| Step | Command | Output |
|---|---|---|
| Dataset (Phases 1–2 at the cutoff + ground truth) | `python run_dataset.py --repo targets/requests --as-of 2024-01-01 --source-subdir src/requests --test-python targets/.venv-requests/Scripts/python` | `data/requests_items.json`, `_ground_truth.json`, `_dataset_meta.json` |
| Phase 4 preview (no API calls) | `python run_prioritisation.py --dry-run` | prompt + cost estimate |
| Phase 4 LLM ranking (RAG + 3 agents) | `python run_prioritisation.py` | `data/requests_llm_rankings.json`, `_static_rankings.json` |
| Phase 4 ablation (no context) | `python run_prioritisation.py --no-context --mode chain` | `data/requests_llm_rankings_nocontext.json` |
| Expert sample | `python run_evaluation.py --make-expert-sample` | `data/requests_expert_sample.json` |
| Expert ratings | HTML dashboard → *Expert rating* tab → copy JSON (each rater, independently) | `evaluation/expert_rankings/<rater>.json` |
| Phase 5 evaluation | `python run_evaluation.py` | `data/requests_evaluation_results.json`, `reports/requests_evaluation_report.md` |
| Phase 6 refactoring + CI | `python run_refactoring.py --test-python targets/.venv-requests/Scripts/python` | `ai-refactoring/*` branches in `targets/requests`, `refactoring/branches/*/patch.diff`, `data/requests_refactoring_results.json` |
| Final report | `python run_evaluation.py` (again) | report now includes Phase 6 and the filled-in research statement |
| Live analysis (any repo URL) | `python run_live.py` → http://127.0.0.1:8600 | the full pipeline, stage by stage, on any public repository |
| Dashboard (HTML, no server) | `python run_dashboard.py` → open `reports/requests_dashboard.html` | overview, priorities, evaluation, expert rating form, refactoring diffs |
| Dashboard (Streamlit, optional) | `streamlit run dashboard/app.py` | same data, live |

Successful LLM responses are validated and cached in `data/llm_cache/`. Identical requests
reuse that cache; changed or failed requests require new API calls. The default is
`gpt-4.1-mini` when an OpenAI key is configured, otherwise `claude-sonnet-4-6`.
Override it with `--model` or `TECHDEBT_LLM_MODEL`. Both experimental arms use the
same model and three-agent chain. Refusals remain failed items; they are not bypassed.

The original Phase 1–3 entry points still work: `run_analysis.py`, `run_enrichment.py`,
`run_rag_pipeline.py`.

## Live analysis — paste any repository

```bash
python run_live.py          # opens http://127.0.0.1:8600   (or double-click start-live.cmd)
```

Paste a public GitHub/GitLab/Bitbucket URL (or `owner/repo`) and the whole pipeline runs
in front of you, stage by stage, with results appearing as each stage finishes:

| Stage | What you see |
|---|---|
| 1 Clone | shallow clone of the history window; commits, authors, files, lines, detected package |
| 2 Static analysis | Radon, Ruff, Pylint, Vulture findings by tool, type and severity |
| 3 Function index | complexity-rank histogram, most complex functions, duplication, dead code |
| 4 Repository context | most-changed files, functions with past bug fixes, most-called functions |
| 5 Debt candidates | complexity vs. static-score chart and a debt-fingerprint heatmap |
| 6 RAG retrieval | ChromaDB + MiniLM neighbours for each candidate, with a worked example |
| 7 LLM agents | an explained priority and refactoring action per shortlisted function, streamed live |
| 8 Fix-first report | final ranking; click any row for metrics, LLM reasoning, RAG neighbours and code |

* The LLM review uses whichever provider has a key in `.env` (Groq, OpenAI or Anthropic);
  pick it in the form, or turn it off. Responses are cached, so re-running a repository is fast.
* **Safe by design:** the pasted repository is only read. Its tests, setup scripts and code are
  never executed, so coverage is shown as "not measured" (and the LLM is told so).
* The server binds to `127.0.0.1` only; one analysis runs at a time. Clones live in `targets/live/`.
* The research dashboard for the evaluated dataset is served at `/research`.

## Debt item schema (`data/<repo>_items.json`)

One record per candidate function (a function with complexity ≥ 6, a lint/refactor finding,
dead code, ≥ 6 duplicated lines or ≥ 60 LOC):

| Field | Meaning |
|---|---|
| `id`, `file`, `function`, `line`, `start_line`, `end_line` | location (`file::Class.method`) |
| `complexity`, `complexity_rank`, `loc`, `params`, `maintainability_index` | Radon metrics |
| `code_smells`, `smell_types`, `tool_messages`, `style_issues`, `dead_code`, `duplicate_lines` | Ruff/Pylint/Vulture findings attributed to the function, clone detector |
| `static_score`, `static_rank` | Phase 1 composite score (0–10) and baseline rank |
| `callers`, `test_references`, `is_private` | call graph in the library / in tests |
| `func_commits`, `func_fix_commits` | function-level history before the cutoff |
| `recent_commits`, `defect_commits`, `commit_authors`, `last_modified_days` | file-level history (2 years before the cutoff) |
| `function_coverage`, `test_coverage` | pytest-cov line coverage of the function / file |
| `docstring` | documentation |

## Project structure

```
├── analyzer/            Phase 1: Radon, Ruff, Pylint, Vulture runners + normaliser
├── enricher/            Phase 2: git (file + function level), call graph, coverage
├── dataset/             function index, clone detector, item builder (temporal split)
├── rag/                 Phase 3: embedder, ChromaDB store, retriever
├── llm/                 Claude client: structured JSON output, disk cache, usage/cost
├── agents/              Phase 4: agent definitions, prompts, prioritisation, CrewAI backend
├── evaluation/          Phase 5: metrics, expert sampling/aggregation, method comparison
├── refactoring/         Phase 6: patching, CI gate, branch orchestration, target CI template
├── dashboard/           HTML dashboard template (run_dashboard.py) + optional Streamlit app
├── live/                live analysis server, pipeline and page (run_live.py)
├── .github/workflows/   CI for this project
├── run_*.py             CLI entry points (one per phase)
├── tests/               pytest suite (no network / API key needed)
└── config.py            defaults (model, cutoff, paths)
```

## Tests and CI

```bash
python -m pytest tests -q
```

The research-pipeline tests (`tests/test_research_pipeline.py`) use temporary git repositories
and a mock LLM, including a full Phase 6 run where the first patch breaks a test, the failure is
fed back, and the second patch passes the gate.

`.github/workflows/techdebt.yml` runs the tests, a Radon complexity report and Ruff on every push.
`refactoring/ci/target-techdebt.yml` is a workflow to drop into a **fork** of the target
repository so that pushed `ai-refactoring/*` branches go through the same gate on GitHub.
Branches are created locally only; pushing them is a manual step.

## Tools

| Tool | Purpose |
|---|---|
| Radon / Ruff / Pylint / Vulture | complexity & maintainability, lint, dead code |
| GitPython + git | history mining |
| pytest-cov | coverage |
| ChromaDB + sentence-transformers | RAG retrieval |
| Anthropic SDK (Claude) | agents; CrewAI optional |
| SciPy | rank statistics |
| Streamlit + Plotly | dashboard |
