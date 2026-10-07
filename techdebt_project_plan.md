# Intelligent Technical Debt & Refactoring — Project Reference

> **Research question:** Can an LLM combine static-analysis results with repository context to prioritise technical debt and recommend refactoring more effectively than static analysis alone?

---

## 0. Implementation status

| Phase | Status | Where |
|---|---|---|
| 1 Static analysis | Done | `analyzer/`, `run_analysis.py` |
| 2 Context enrichment | Done, plus function-level git history and `as_of` cutoff | `enricher/`, `run_enrichment.py` |
| 3 RAG | Done | `rag/`, `run_rag_pipeline.py` |
| Research dataset | Function-level items for `psf/requests` at 2024-01-01, ground truth from later commits | `dataset/`, `run_dataset.py` |
| 4 LLM prioritisation | Implemented (Analysis → Priority → Refactor agents, RAG, no-context ablation); **needs ANTHROPIC_API_KEY to run** | `llm/`, `agents/`, `run_prioritisation.py` |
| 5 Evaluation | Implemented; baseline numbers computed; **needs expert ratings + Phase 4 output** | `evaluation/`, `run_evaluation.py`, dashboard |
| 6 Refactoring + CI | Implemented (branches, CI gate with retry, metrics); **needs Phase 4 output** | `refactoring/`, `run_refactoring.py`, `.github/workflows/` |

Deviations from the original plan, and why:
- **Unit of ranking = function**, not raw tool message: the plan's dataset schema (Section 4) is per function, and a refactoring targets a function.
- **Temporal split**: context comes from history before a cutoff and the "known defect history" ground truth from after it. Without this the LLM would be scored on data it was shown.
- **Native agent chain by default**: same three roles as Section 6, run directly on the Claude API with structured JSON output and caching; CrewAI is available via `--backend crewai`.
- **jscpd** replaced by a built-in clone detector (`dataset/duplication.py`) to avoid a Node.js dependency.
- **Branches are local**: `run_refactoring.py` creates `ai-refactoring/*` branches in the target clone; pushing to a fork with `refactoring/ci/target-techdebt.yml` is a manual step.

---

## 1. Project overview

Static-analysis tools (Radon, Pylint, Ruff) detect code problems but rank them by raw code metrics — complexity score, line count — with no knowledge of how critical a module is, how often it breaks, or how many other components depend on it.

This project builds a pipeline that:
1. Runs static analysis on a Python repository.
2. Enriches every issue with repository context (call graph, git history, test coverage, defect records).
3. Stores that context in a vector database (ChromaDB) for RAG retrieval.
4. Feeds enriched context to an LLM via CrewAI agents, which produce a priority-ranked list and concrete refactoring recommendations.
5. Tests refactoring output automatically through a CI/CD pipeline.
6. Evaluates prioritisation quality against static-analysis ranking and expert rankings (Spearman ρ, Precision@K).

---

## 2. System architecture

```
Software repository (GitHub — Python, ~5–10k LOC)
         │
   ┌─────┼──────┐
   ↓     ↓      ↓
Static   Repo   Dev
analysis context history
(Radon,  (call  (git log,
 Ruff,   graph, defect
 Pylint, docs,  commits)
 Vulture) coverage)
   │     │      │
   └─────┼──────┘
         ↓
  Tech-debt dataset (JSON per issue)
         ↓
     RAG layer
  (ChromaDB + Sentence-Transformers + LangChain)
         ↓
  LLM + CrewAI agents
  ┌──────────────────────────┐
  │ Analysis → Priority → Refactor │
  └──────────────────────────┘
       ↓                ↓
  Ranked debt list   Refactoring patch
  (priority+reason)  (LLM diff, feature branch)
       └────────┬───────┘
                ↓
       CI/CD (GitHub Actions)
       Pytest · Radon · Ruff
                ↓
       Streamlit evaluation dashboard
```

---

## 3. Tool stack

| Layer | Tools | Purpose |
|---|---|---|
| Static analysis | Radon, Ruff, Pylint, Vulture, jscpd | Detect complexity, smells, dead code, duplication |
| Repo context | GitPython, ast (stdlib), pycallgraph2 | Call graph, commit frequency, coverage |
| Vector store | ChromaDB | Store and retrieve enriched issue context |
| Embeddings | sentence-transformers (`all-MiniLM-L6-v2`) | Embed issues + context chunks |
| Orchestration | LangChain | RAG retrieval + prompt chaining |
| Agents | CrewAI | Multi-agent: Analysis → Priority → Refactor |
| LLM | claude-sonnet-4-6 (Anthropic API) | Reasoning, prioritisation, refactoring |
| CI/CD | GitHub Actions | Run tests + re-run static analysis post-refactor |
| Testing | Pytest | Regression check after LLM refactoring |
| Dashboard | Streamlit | Visualise before/after metrics, rankings |
| Database | PostgreSQL (optional) | Persist issue history across runs |

All static-analysis tools are free and pip-installable.

---

## 4. Tech-debt dataset schema

Each static-analysis issue is merged into a single JSON record:

```json
{
  "file": "payment.py",
  "function": "process_payment",
  "complexity": 24,
  "maintainability_index": 51.2,
  "loc": 180,
  "code_smells": 5,
  "duplicate_lines": 32,
  "dead_code": false,
  "callers": 17,
  "recent_commits": 15,
  "defect_commits": 3,
  "test_coverage": 42,
  "docstring": "Handles all payment validation and transaction processing.",
  "static_rank": 1
}
```

This record is what gets embedded and retrieved by the RAG layer.

---

## 5. RAG prompt template

For each issue, the LLM receives:

```
You are a senior software engineer reviewing technical debt.

Issue: process_payment() in payment.py
Static analysis:
  - Cyclomatic complexity: 24
  - LOC: 180
  - Code smells: 5
  - Duplicate lines: 32
  - Test coverage: 42%

Repository context:
  - Called by 17 modules
  - Modified 15 times in last 3 months
  - 3 recent defect-linked commits
  - Documentation: "Handles all payment validation and transaction processing."

Assign a priority (HIGH / MEDIUM / LOW), explain your reasoning in 2–3 sentences,
and recommend a concrete refactoring action.
Respond as JSON: {"priority": "...", "reason": "...", "action": "..."}
```

---

## 6. CrewAI agent definitions

```python
from crewai import Agent, Task, Crew

analysis_agent = Agent(
    role="Code Analysis Expert",
    goal="Parse static analysis results and repository context for each issue",
    backstory="Senior engineer with 10 years of codebase analysis experience",
    verbose=True
)

priority_agent = Agent(
    role="Technical Debt Prioritiser",
    goal="Rank issues by business impact using context retrieved from RAG",
    backstory="Engineering manager who has led multiple large-scale refactoring programmes",
    verbose=True
)

refactor_agent = Agent(
    role="Refactoring Specialist",
    goal="Generate concrete, testable refactoring recommendations",
    backstory="Staff engineer who specialises in code quality and maintainability",
    verbose=True
)
```

Tasks flow: Analysis → Priority → Refactor. Each agent receives the output of the previous one as context.

---

## 7. Phased roadmap

### Phase 1 — Static analysis foundation (weeks 1–2)
**Goal:** build the tech-debt dataset.

- [ ] Pick a real open-source Python repo (~5–10k LOC). Good candidates: `flask`, `requests`, `black`, `httpie`.
- [ ] Install tools: `pip install radon ruff pylint vulture`
- [ ] Write `analyzer.py` that runs each tool, parses JSON/text output, and merges into a unified list of issue records.
- [ ] Save output to `debt_dataset.json`.
- [ ] Verify: you have at least 30–50 distinct issues across files/functions.

**Key commands:**
```bash
radon cc . -s -j          # complexity per function, JSON
radon mi . -s             # maintainability index
ruff check . --output-format json
pylint src/ --output-format json
vulture .                 # dead code (text, parse manually)
```

**Deliverable:** `debt_dataset.json` with 30–50 issues, each having file, function, complexity, smells fields.

---

### Phase 2 — Repo context enrichment (weeks 3–4)
**Goal:** add git history, call graph, and coverage to each issue.

- [ ] Use `GitPython` to extract, per file: commit count in last 90 days, number of commits whose message contains "fix"/"bug"/"error".
- [ ] Use `ast` (stdlib) or `pycallgraph2` to count callers per function.
- [ ] Run `pytest --cov=src --cov-report json` to get per-file coverage.
- [ ] Merge all fields into each issue record in `debt_dataset.json`.

**Key code snippet (git history):**
```python
import git
repo = git.Repo(".")
for commit in repo.iter_commits(paths="payment.py", since="90 days ago"):
    if any(kw in commit.message.lower() for kw in ["fix", "bug", "error"]):
        defect_count += 1
```

**Deliverable:** Enriched `debt_dataset.json` with `callers`, `recent_commits`, `defect_commits`, `test_coverage` per issue.

---

### Phase 3 — RAG pipeline (weeks 5–6)
**Goal:** retrieve relevant context for any issue on demand.

- [ ] Install: `pip install chromadb sentence-transformers langchain`
- [ ] Write `ingest.py`: load `debt_dataset.json`, format each record as a text chunk, embed with `all-MiniLM-L6-v2`, store in ChromaDB collection.
- [ ] Write `retriever.py`: given an issue ID, query ChromaDB for top-5 similar issues (useful for few-shot examples and pattern matching).
- [ ] Test retrieval: for `process_payment`, confirm returned chunks include its own record and similar high-complexity functions.

**Key code snippet:**
```python
import chromadb
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("all-MiniLM-L6-v2")
client = chromadb.Client()
collection = client.create_collection("techdebt")

for issue in issues:
    text = f"{issue['function']} complexity={issue['complexity']} callers={issue['callers']} defects={issue['defect_commits']}"
    embedding = model.encode(text).tolist()
    collection.add(documents=[text], embeddings=[embedding], ids=[issue["function"]])
```

**Deliverable:** ChromaDB collection populated; retrieval returning correct top-K issues.

---

### Phase 4 — LLM prioritisation (weeks 7–9)
**Goal:** produce LLM-ranked list of all issues with reasons.

- [ ] Install: `pip install crewai anthropic`
- [ ] Set `ANTHROPIC_API_KEY` in `.env`.
- [ ] Write `agents.py` defining the three CrewAI agents (see Section 6).
- [ ] Write `prioritise.py`: for each issue, retrieve context from ChromaDB, build the prompt (see Section 5), run through Analysis → Priority agent chain, collect JSON output.
- [ ] Save results to `llm_rankings.json`: `[{"function": "...", "priority": "HIGH", "reason": "...", "action": "...", "llm_rank": 1}, ...]`
- [ ] Also save `static_rankings.json`: issues sorted purely by `complexity` descending.

**Deliverable:** Two ranking files ready for evaluation.

---

### Phase 5 — Evaluation (weeks 10–11)
**Goal:** produce the research result — measurable comparison.

- [ ] Recruit 2–3 developers (classmates, supervisor, or yourself across sessions) to manually rank a sample of 20–30 issues.
- [ ] Record expert rankings in `expert_rankings.json`.
- [ ] Compute Spearman ρ between: (a) static vs expert, (b) LLM vs expert.
- [ ] Compute Precision@10: of the top 10 issues each method selects, how many match the expert's top 10?
- [ ] Plot results in Streamlit dashboard.

**Key evaluation code:**
```python
from scipy.stats import spearmanr

static_ranks  = [issue["static_rank"]  for issue in merged]
llm_ranks     = [issue["llm_rank"]     for issue in merged]
expert_ranks  = [issue["expert_rank"]  for issue in merged]

rho_static, _ = spearmanr(static_ranks, expert_ranks)
rho_llm,    _ = spearmanr(llm_ranks,    expert_ranks)

print(f"Static ρ = {rho_static:.3f}")
print(f"LLM    ρ = {rho_llm:.3f}")
```

**Target numbers (publishable threshold):**

| Metric | Static-only target | LLM+RAG target |
|---|---|---|
| Spearman ρ vs experts | ~0.45–0.55 | > 0.70 |
| Precision@10 | ~0.40 | > 0.65 |

**Deliverable:** Evaluation table with Spearman ρ and Precision@10 for both methods.

---

### Phase 6 — Refactoring + CI/CD loop (weeks 12–14)
**Goal:** LLM generates refactored code; CI/CD validates it.

- [ ] Write `refactor.py`: for the top-5 HIGH-priority issues, pass the function code + LLM recommendation to the Refactor agent. Save the suggested rewrite.
- [ ] Write the changed code to a feature branch (`ai-refactoring`).
- [ ] Set up `.github/workflows/techdebt.yml` (see below).
- [ ] Track: did Pytest pass? Did complexity drop? Did smells reduce?
- [ ] Record `refactoring_success_rate` = (branches where tests pass + complexity drops) / total branches.

**GitHub Actions workflow:**
```yaml
name: Tech Debt CI
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Install deps
        run: pip install -r requirements.txt
      - name: Run tests
        run: pytest --tb=short
      - name: Complexity check
        run: radon cc . -s -n B   # fail if anything > grade B
      - name: Lint check
        run: ruff check .
```

**Deliverable:** At least 5 refactored branches, success rate recorded, complexity delta measured.

---

## 8. Folder structure

```
techdebt-project/
├── src/                        # target repo code being analysed
├── analyzer/
│   ├── analyzer.py             # Phase 1: static analysis runner
│   ├── enricher.py             # Phase 2: git + coverage enrichment
│   └── ingest.py               # Phase 3: embed + store in ChromaDB
├── agents/
│   ├── agents.py               # CrewAI agent definitions
│   └── prioritise.py           # Phase 4: run LLM ranking
├── evaluation/
│   ├── evaluate.py             # Phase 5: Spearman, Precision@K
│   └── expert_rankings.json    # manual expert rankings
├── refactoring/
│   ├── refactor.py             # Phase 6: generate patches
│   └── branches/               # one folder per feature branch diff
├── data/
│   ├── debt_dataset.json       # unified issue dataset
│   ├── llm_rankings.json       # LLM priority output
│   └── static_rankings.json    # baseline static-only ranking
├── dashboard/
│   └── app.py                  # Streamlit evaluation dashboard
├── .github/workflows/
│   └── techdebt.yml            # CI/CD pipeline
├── requirements.txt
└── .env                        # ANTHROPIC_API_KEY (git-ignored)
```

---

## 9. Key metrics to track

| Metric | How to measure | Tool |
|---|---|---|
| Spearman ρ (LLM vs expert) | `scipy.stats.spearmanr` | Python |
| Precision@10 | Set intersection of top-10 lists | Python |
| Complexity before/after | `radon cc` on original vs refactored | Radon |
| Duplication reduction | `jscpd` before/after | jscpd |
| Test pass rate | Pytest exit code on CI | GitHub Actions |
| Refactoring success rate | (passed CI) / (total attempts) | Manual log |

---

## 10. Research contribution statement

> "We show that LLM-based prioritisation of technical debt, augmented with repository context retrieved via RAG, achieves a Spearman correlation of X.XX with expert developer rankings — compared to X.XX for static analysis alone — and a Precision@10 of X.XX vs X.XX. We further demonstrate that LLM-generated refactoring patches pass CI/CD validation at a rate of XX%, with an average cyclomatic complexity reduction of X.X points per refactored function."

Fill in the blanks after Phase 5 evaluation.

---

## 11. Quick-start checklist

```
Week 1  ☐ Clone target OSS repo
        ☐ Run Radon + Ruff + Pylint + Vulture
        ☐ Write analyzer.py → debt_dataset.json

Week 2  ☐ Add GitPython enrichment
        ☐ Add call-graph analysis
        ☐ Add coverage data

Week 3  ☐ Set up ChromaDB
        ☐ Embed all issues
        ☐ Test retrieval

Week 4  ☐ Write CrewAI agents
        ☐ Run LLM on all issues
        ☐ Save llm_rankings.json

Week 5  ☐ Collect expert rankings
        ☐ Compute Spearman ρ + Precision@10

Week 6  ☐ Generate top-5 refactoring patches
        ☐ Push to feature branches
        ☐ Set up GitHub Actions
        ☐ Record success rate

Week 7  ☐ Build Streamlit dashboard
        ☐ Write evaluation section of report
```

---

## 12. References / recommended reading

- McCabe, T.J. (1976). *A complexity measure.* IEEE TSE.
- Fowler, M. (2018). *Refactoring: Improving the Design of Existing Code.* 2nd ed.
- Lewis et al. (2020). *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.* NeurIPS.
- Pornprasit & Tantithamthavorn (2021). *JITLine: A Simpler, Better, Faster, Finer-grained JIT Defect Prediction.* MSR.
- SonarSource. *Technical Debt and Code Smells.* https://docs.sonarqube.org
- CrewAI docs: https://docs.crewai.com
- ChromaDB docs: https://docs.trychroma.com
