# Deploy NeuroLens v2.1 to Google Cloud Run (from a Mac)

You get a public `https://neurolens-….run.app` link. Total time: about 20 minutes the first time and 8 minutes for later updates.

**What it costs.** Cloud Run's free tier gives 180,000 vCPU-seconds and 2 million requests every month. A class
demo uses a tiny part of that. The only small charges are image storage (~1–2 GB, a few rupees a month) and the
build minutes. Your $30 credit easily covers months of this. Step 3 sets an alert anyway.

**Demo mode.** The database lives inside the container, so accounts, reports and chats **reset whenever the
service restarts**. The service also restarts after it has been idle for a while. The admin account is created
again on every start.

---

## 1. Install and log in to gcloud (once)

```bash
brew install --cask google-cloud-sdk        # no Homebrew? see https://cloud.google.com/sdk/docs/install
gcloud init                                  # opens the browser: log in, pick or create a project
gcloud auth login                            # only if init did not log you in
```

Write down your **project ID** (not the project name):

```bash
gcloud projects list
```

## 2. Make sure billing (your credits) is linked to the project

Open https://console.cloud.google.com/billing/linkedaccount and check that the project shows your billing account
with the credits. Without this, step 5 stops with "billing account not found".

## 3. Set a budget alert (recommended, 1 minute)

Go to https://console.cloud.google.com/billing/budgets, then **Create budget**:
- Name: `neurolens`
- Amount: `$5`
- Alerts: 50%, 90% and 100%

Google e-mails you if spending gets near $5. The alert does not stop the service; step 8 does.

## 4. Prepare the folder

Open a terminal in the NeuroLens folder, the one that contains `Dockerfile`:

```bash
cd ~/Downloads/NeuroLens      # wherever you unzipped v2.1
cp ../"NeuroLens 2"/.env .     # reuse your keys from the v2 folder (or: cp .env.example .env and fill it in)
cp ../"NeuroLens 2"/models/mri_classifier.pt models/   # your trained MRI model
ls models                      # should show mri_classifier.pt
```

Your `.env` needs your 3 Gemini keys, 3 Grok/Groq keys and the Serper key, exactly as when you run it locally.
The deploy script handles these settings itself, so you don't need to change them:
- production mode;
- the database location;
- router mode for the explainer;
- a strong JWT secret;
- an admin password, generated if yours is still `Admin12345`.

## 5. Deploy

```bash
chmod +x deploy/*.sh
./deploy/cloudrun.sh YOUR_PROJECT_ID
```

The first run asks once to enable APIs and create the image repository. It ends with something like:

```
✅ NeuroLens is live: https://neurolens-abc123-el.a.run.app
   Admin login:    admin@neurolens.local
   Admin password: NL-3f9a1c2b7e44   ← new, write it down
```

The default region is Mumbai (`asia-south1`). To use another one: `./deploy/cloudrun.sh YOUR_PROJECT_ID us-central1`.

## 6. Check it

1. Open `<link>/api/health`. It should show `"status": "ok"` and `"llm_keys": 6`.
2. Open the link, register a user and upload `sample_data/reports/cbc_report.txt`.
3. Sign in as admin and open **System Review**. "LLM key pool" should pass after the first chat.
   Also open **API keys** and press **Test** on each key.

**The first visit after idle takes about 10–20 s.** The container starts from zero; later requests are fast.
Open the link a minute before you present.

## 7. Update after changing code or keys

Run the same command again:

```bash
./deploy/cloudrun.sh YOUR_PROJECT_ID
```

New keys in `.env` replace the old ones in Secret Manager. Only the two newest images are kept.

## 8. Stop everything (after the evaluation)

```bash
./deploy/teardown.sh YOUR_PROJECT_ID
```

This deletes the service, the images and the secret, so nothing keeps using credits.

---

## If something goes wrong

| Message | Fix |
|---|---|
| `gcloud: command not found` | Close and reopen the terminal after `brew install`, or run `source "$(brew --prefix)/share/google-cloud-sdk/path.zsh.inc"` |
| `billing account ... not found` / `FAILED_PRECONDITION` | Do step 2 |
| `PERMISSION_DENIED ... storage.objects.get` during the build | Wait 2 minutes (new permissions take time to apply) and run the script again |
| Build fails at `pip install` | Run it again (usually a temporary download error); if it repeats, send me the last 30 lines |
| The page loads, but answers say "template (offline)" | Admin → API keys → **Test** shows each key's error; fix `.env` and redeploy |
| MRI page: "model not installed" | `models/mri_classifier.pt` was missing; do step 4 and redeploy |
| `Memory limit exceeded` in the logs | Edit `deploy/cloudrun.sh`: change `--memory 2Gi` to `--memory 4Gi --cpu 2` |
| See the server logs | `gcloud run services logs read neurolens --region asia-south1 --limit 50` |

## What is deployed

```
Your Mac ──(gcloud run deploy --source .)──▶ Cloud Build ──▶ Artifact Registry (image)
                                                                     │
Browser ──HTTPS──▶ Cloud Run "neurolens" (1 instance max, 2 GB, scales to 0)
                     ├─ FastAPI + frontend + MRI model (CPU) + Tesseract
                     ├─ /secrets/neurolens.env  ◀── Secret Manager "neurolens-env" (your keys)
                     └─ SQLite in /tmp (demo data, resets on restart)
```

- Keys are never in the image. The `.env` is uploaded to Secret Manager as one secret and mounted as a file.
- `--max-instances 1`: the rate limiter and SQLite stay consistent, and cost can never run away.
- The image contains CPU-only PyTorch for the MRI model. The fine-tuned Qwen is **not** deployed: the patient view
  uses router mode (your API keys with the training prompt). Local mode would need about 4 GB RAM and take 20–60 s per answer.
