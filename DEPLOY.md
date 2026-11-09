# Deploying

The task needs a public URL that the reviewers can open. This app is one container, one process, SQLite on a disk. Nothing else to run.

## Fastest: Render (or Railway / Fly)

1. Put this folder in a GitHub repo (the `.gitignore` already keeps `var/` and `.env` out).
2. Render > New > Blueprint > pick the repo. It reads `render.yaml`.
3. Fill in the three secrets it asks for:
   - `GEMINI_API_KEY`: free, from aistudio.google.com (no card). Free-tier rate limits are fine for a demo.
   - `ANTHROPIC_API_KEY` (optional): if you have one, it is used instead of Gemini. Put it on a workspace with a spend limit, because the URL is public.
   - `BASE_URL`: the URL Render gives you (links inside emails use it).
   - `DEMO_INBOX` + `RESEND_API_KEY`: optional, only for the "Send to my test inbox" button.
4. Open the URL. The top-left chip must say **Live AI: Gemini** (or Claude). If it says *Offline mock AI*, the key is missing.

## Settings that matter

| Variable | Why |
|---|---|
| `VAR_DIR=/data` | Database and uploads. Must be a persistent disk, or history vanishes on every restart. |
| `ADMIN_KEY` | If set, "Reset demo data" asks for it (`X-Admin-Key` header). Leave it unset for local use. |
| `DAILY_RUN_CAP` | Hard daily ceiling on runs, so a shared link cannot drain your API credit. |
| `RATE_PER_HOUR` | Per-IP submissions per hour. |
| `LLM_PROVIDER` | `auto` (default): Claude if its key exists, else Gemini. Or force `gemini` / `anthropic`. |
| `MOCK_LLM` | `auto` (default) uses the real model when a key exists. `1` forces the offline mock. |

## Before you submit the link (do this in order)

1. `python scripts/check_model.py` with the key set. It confirms the key works and every configured model exists. If a Gemini model name is reported missing, set `GEMINI_MODEL` / `GEMINI_FALLBACK` to one from your list.
2. Open the live site, Evaluation > Run all cases. It asks for confirmation because it spends credit. **Expect 26/26.** If a case fails in live mode, that is a real model-versus-mock difference: tell me which case and its output and I will fix the prompt or the rule.
3. Click "Reset demo data" so reviewers start from a clean list (5 existing vendors, nothing else).
4. Free instances sleep. Open the URL a minute before any demo so the first click is not a cold start.

## Run locally

```
pip install -r requirements.txt
python scripts/make_samples.py     # regenerate before a demo: document dates are relative to today
uvicorn app.main:app --port 8000   # no key needed: it runs in offline mock mode
```
Docker: `docker build -t vendor . && docker run -p 8000:8000 -e GEMINI_API_KEY=... -v vendor-data:/data vendor`
