# Run the project

## Groq with CrewAI

The local `.env` supplies `GROQ_API_KEY` and
`TECHDEBT_CREWAI_MODEL=groq/openai/gpt-oss-20b`. The key is excluded from Git
and Docker build contexts. Provider routing follows the
[Groq CrewAI integration](https://console.groq.com/docs/crewai).
An isolated environment at `%USERPROFILE%/.venvs/techdebt-crewai` keeps CrewAI
dependencies separate from the tested app and avoids Windows long-path installation errors.

```powershell
./run-crewai.ps1 --limit 1
# Full matched experiments after the pilot succeeds:
./run-crewai.ps1
./run-crewai.ps1 --no-context
python run_pipeline.py --reports-only
```

The native OpenAI/Anthropic backend and refactoring client use their own provider
configuration. Setting the CrewAI Groq model does not switch those clients.

## Docker presentation app

Start Docker Desktop, then run these commands from this folder:

```powershell
docker compose up --build -d
docker compose ps
```

Open http://127.0.0.1:8501. The image includes the dataset, saved evidence,
snapshot source, and dashboard. Viewing it requires no API key or API credits.
Reviewers can use http://127.0.0.1:8501/?review=1 for the dedicated blind form.
Reviewer files persist in `evaluation/expert_rankings` on the host.

```powershell
docker compose logs --tail=100 dashboard
docker compose down
```

The service runs as a non-root user with a read-only root filesystem, a health
check, dropped Linux capabilities, and a localhost-only port. It is intended
for one trusted local presentation/research team. Public hosting needs an
authenticated TLS reverse proxy, access policy, reviewer identity checks,
backup policy, load testing and an operational security review.

## Local fallback

```powershell
python -m pip install -r requirements-dashboard.txt
.\start-demo.ps1
```

Or open `reports/requests_dashboard.html` directly for the offline report.
The HTML is self-contained apart from optional web fonts; it works without them.

## Complete the real experiments

Install `requirements.txt`. Set a funded `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`
in a git-ignored `.env` file. Do not commit or bake keys into images.

```powershell
python run_pipeline.py --model gpt-4.1-mini --test-python targets/.venv-requests/Scripts/python.exe
```

This runs the same three-agent chain with and without context, then generates
refactoring proposals in separate target Git worktrees, evaluates results and
rebuilds the dashboard. API use is billed by the provider. Validated responses
are cached. Failed/quota-blocked responses never count as successful rankings.
Pilot runs (`--limit`) write separate files and cannot replace the full run.

The pipeline Docker target is optional:

```powershell
docker build --target pipeline -t techdebt-pipeline .
```

It contains the code and snapshot, but new dataset/refactoring experiments need
a separately mounted full target Git clone and its test dependencies. Windows
virtual environments and absolute paths cannot be reused inside Linux; generate
the dataset inside that environment for new refactoring experiments.

## Human evaluation and refresh

Have two or three developers independently rate the sample in the blind form.
Do not show them algorithm rankings first. No generated or fabricated ratings
should be treated as human evidence. After collecting actual ratings:

```powershell
python run_pipeline.py --reports-only
docker compose up --build -d
```

Back up `data`, `reports`, `evaluation/expert_rankings`, and `refactoring/branches`
along with the target snapshot SHA and cached LLM responses. Keep `.env` separate.
See `reports/READINESS.md` for current missing evidence. A Docker build does not
establish scientific validity or prove production readiness.
