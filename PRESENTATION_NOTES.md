# Presentation guide

## Explain the research question

Can repository context and retrieval help an LLM prioritise technical debt more
effectively than static analysis alone? Static complexity measures code structure;
repository context adds usage, changes, historical fixes and coverage.

## Demonstrate what is implemented

1. Open the app at http://127.0.0.1:8501 or the offline HTML in `reports`.
2. Overview: Requests snapshot `72eccc8d`, cutoff 2024-01-01, 235 indexed
   functions, 500 tool findings, 70 candidate debt functions.
3. Rankings: inspect a function, its source, complexity, callers and coverage.
4. Evaluation: compare static composite, raw complexity and context heuristic.
   Precision@10 is 0.40, 0.40 and 0.30 respectively. Change-history Spearman
   correlation is 0.386, 0.484 and 0.392. Seven candidates have post-cutoff fixes.
5. Explain the implemented LLM chain: analysis → priority → refactoring advice.
   MiniLM embeddings and Chroma retrieval provide five neighbouring debt items.
6. Show the blind expert form at `/?review=1`. Two or three developers should
   rate the same 30 functions independently before seeing model rankings.
7. Explain the refactoring gate: isolated Git worktree, preserved signatures,
   tests compared with baseline, lower target complexity, no increase in Ruff
   findings or duplicated lines. Rejected attempts retain diagnostic evidence.

## Be precise about results

The baseline evaluation is measured. The RAG index ran. The LLM API returned
`credit_balance_exhausted`; both experiment files contain failures, not model
rankings. No actual LLM refactoring experiment has completed. Do not present
target numbers from the roadmap as observations or claim LLM superiority.

The unmodified target suite produced 591 passes, 2 failures, 3 skips and 1
expected failure in this Windows environment. These are target-library results,
separate from the project's own automated tests. See `reports/project_tests.xml`
and `reports/VERIFICATION.md` for the application verification.

## Limitations to discuss

- A single mature Python repository and only seven future-defect positives.
- Commit-message heuristics are imperfect labels, not independent expert truth.
- Call counts are static approximations; dynamic Python calls may be missed.
- Future commits are withheld from prompts, but pretrained-model familiarity
  with this public repository cannot be ruled out.
- Expert evaluation, real LLM comparison, and measured refactoring success
  remain pending. Lower function complexity alone does not prove better design;
  report complexity of extracted helpers too.

## Next run

Configure a funded provider key in `.env`, then run the command in `DEPLOYMENT.md`.
Both experimental arms use the same model and chain, so the context toggle is
the intended difference. Results, prompt hashes and retrieval evidence are saved.
Collect actual human ratings, refresh the evaluation, and report failures as
well as successes. The Docker package has a local-only deployment scope.
