# XBot

XBot reads a product from its name and URL, finds related public posts with Grok inside X, and posts one reply under each saved post.

The app is two processes:

- `frontend` — Next.js dashboard at http://127.0.0.1:3000
- `backend` — FastAPI and Playwright at http://127.0.0.1:8787

Google Chrome must be installed. The bot uses that Chrome profile, not a separate browser.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -m playwright install chromium
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

Leave the password fields in those example files blank. Save the X username and password from Settings after the app is running. Those values stay in `backend/data/store.json` on this computer.

## Run

From `backend`:

```bash
../.venv/bin/python -m app
```

From `frontend`, in another terminal:

```bash
npm install
npm run dev
```

Open http://127.0.0.1:3000.

## Deploy on GCP (Cloud Run)

The dashboard is a Next.js Cloud Run service. The API is a second Cloud Run service with Playwright Chromium (2 CPU, 4 GiB, min instances 1 so in-memory jobs and the browser stay alive).

1. Create a GCP project and enable billing.
2. Install [gcloud](https://cloud.google.com/sdk/docs/install) and Docker Desktop.
3. Sign in and pick the project:

```bash
gcloud init
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

4. Apply `supabase/schema.sql` if you want data to survive deploys, then:

```bash
export PROJECT_ID=YOUR_PROJECT_ID
export REGION=us-central1
export SUPABASE_URL=https://your-project.supabase.co
export SUPABASE_PUBLISHABLE_KEY=your-key
chmod +x deploy/gcp.sh
./deploy/gcp.sh
```

To keep the X Chrome profile across restarts, add a bucket:

```bash
BUCKET=YOUR_PROJECT_ID-xbot-data ./deploy/gcp.sh
```

The script prints the dashboard URL. Save the X username and password in Settings after it is up. X may still challenge logins from Google IPs; complete any extra check if the session stays open, or log in once locally and copy `backend/data/chrome-profile` into the bucket.

`gcloud` is not installed in this workspace, so the first deploy has to run on a machine that has Docker and the Cloud SDK.

## Use

1. In Settings, enter the X username or email and password, then save.
2. On Analysis, enter the product name and URL, click Analyze, then Verify.
3. On Finder, verify the same product, choose how many posts to ask for, and click Find. Grok runs inside X.
4. On Coming, click Start commenting. Each post gets one reply, then a like, then a 30 second wait.

If X asks for a code or other extra check, finish it in the Chrome window while it stays open.

## What stays off GitHub

These files are local and are not part of the repository:

- `backend/.env` and `frontend/.env.local`
- `backend/data/store.json` (saved settings, including the X password)
- `backend/data/activity.json` and `backend/data/daily_post.json`
- `backend/data/chrome-profile/`

`DATABASE_MODE=auto` uses Supabase after `supabase/schema.sql` is applied. Until then the app uses the local JSON store.
