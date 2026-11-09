# Deploying to Render (public link for the submission)

**What Render is:** a hosting service. You connect a GitHub repository; it builds the app from the `Dockerfile` and gives you a public URL like `https://vendor-onboarding.onrender.com`. That URL is "Deliverable 1: a link to your process, live and runnable".

**Free tier, two things to know:**
1. **It sleeps after about 15 minutes with no visitors.** The first visit after that takes about 50 seconds. Open the link a minute before any demo.
2. **There is no permanent disk on the free tier**, so a restart wipes the run history. The app re-seeds itself on startup, so it always works. If you want history to survive, switch to the Starter plan (about $7/month) and add a disk (commented out in `render.yaml`).

## Steps (about 10 minutes)
1. **Put the code on GitHub.** In this folder:
   ```bash
   git remote add origin https://github.com/<you>/vendor-onboarding.git
   git push -u origin master
   ```
   (Create the empty repository on github.com first. `.gitignore` keeps `var/` and `.env` out.)
2. **Render:** sign in at render.com (GitHub login is fine) → **New** → **Blueprint** → pick the repository. Render reads `render.yaml`.
3. **Fill the settings it asks for:**
   - `BASE_URL`: leave it empty for now, then set it to your URL after the first deploy (it is used in vendor email links).
   - `GEMINI_API_KEY` (optional): a free key from aistudio.google.com. It switches document reading from "offline" to a real model.
   - `ANTHROPIC_API_KEY` (optional): used instead of Gemini if set. Put a spend limit on it, because the URL is public.
   - `ADMIN_KEY` is generated for you. Find it under the service's **Environment** tab; the app asks for it when someone clicks "Reset demo data" on the public link.
4. **Deploy.** The first build takes about 3–5 minutes. Then open the URL:
   - The bottom-left chip says **Offline AI (simulated)** or **Live AI · Gemini/Claude**.
   - **Model quality** shows the validation results; **New submission** shows the demo pack.

## Before you send the link
1. Open it once to wake it.
2. Overview → **Reset demo data** (enter the admin key), so reviewers start clean.
3. Run one happy-path case and one edge case to confirm.
4. If you added a model key: run `python scripts/live_check.py --n 30` locally with the same key, and read any failure.

## Built-in protection for a public URL
- Upload type and size limits.
- A per-IP hourly limit (`RATE_PER_HOUR`).
- A hard daily run cap (`DAILY_RUN_CAP`), so a shared link can't drain an API budget.
- Demo emails are drafted, never sent to real vendor addresses.

## Alternatives
Railway and Fly.io work the same way (one container, `PORT` env var respected). Locally, with Docker:
```bash
docker build -t vendor .
docker run -p 8000:8000 vendor
```
