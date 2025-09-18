# BountyScreener — Quick Start

Run a minimal **Submission Scorer** for the Berkeley Function‑Calling Leaderboard (BFCL). This service:
- Watches for submissions
- Downloads models from Hugging Face
- Runs BFCL scoring inside a controlled venv
- Reports results to your watcher

The layout is **opinionated** and now driven by a small `.env`. Users only set:
- Where they cloned **this** repo (Bounty Hunter / BountyScreener)
- Where they cloned **BFCL** (inside Gorilla)
- **GPU** settings (device index, num GPUs, utilization)
- **Validator / wallet names** (cold & hot keys) + watcher/auth used by your API

All score/eval paths are derived from `BFCL_ROOT` automatically — you **do not** set them in env.

---

## Prerequisites
- Ubuntu (or similar) with NVIDIA drivers + CUDA (verify `nvidia-smi`)
- Python 3.9+
- Git
- Node.js (optional, for `pm2` process manager)

---

## 1) Clone repositories (expected layout)

```bash
mkdir -p ~/BFCL
cd ~/BFCL

# Clone the BountyScreener service
git clone https://github.com/VectorForger/BFCL-BountyScreener

# Clone Gorilla (contains BFCL)
git clone https://github.com/ShishirPatil/gorilla.git
```

**BFCL lives at:**
```
~/BFCL/gorilla/berkeley-function-call-leaderboard
```

> This path is referenced by the app via `BFCL_ROOT` in your `.env`.

---

## 2) Create and activate the virtual environment (fixed location relative to your service dir)

Create the venv **at** `$BOUNTY_HUNTER_DIR/.v4env`. If you keep the default `BOUNTY_HUNTER_DIR=~/BFCL`, it becomes `~/BFCL/.v4env`.

```bash
# from anywhere
python3 -m venv ~/BFCL/.v4env
source ~/BFCL/.v4env/bin/activate
```

> Replace `~/BFCL` with your actual `BOUNTY_HUNTER_DIR` if you changed it.

---

## 3) Install dependencies

**BountyScreener** deps:
```bash
cd ~/BFCL/BFCL-BountyScreener
pip install -r requirements.txt
```

**BFCL** deps (editable + extras for sglang evaluation):
```bash
cd ~/BFCL/gorilla/berkeley-function-call-leaderboard
pip install -e .
pip install -e .[oss_eval_sglang]
```

---

## 4) Configure environment

Copy the example and edit:
```bash
cd ~/BFCL/BFCL-BountyScreener
cp .env.example .env
```

Edit `.env` and set the following **required** values:
- `BOUNTY_HUNTER_DIR` → path to this service’s root (controls venv path at `$BOUNTY_HUNTER_DIR/.v4env`)
- `BFCL_ROOT` → path to the BFCL repo root (`.../gorilla/berkeley-function-call-leaderboard`)
- `CUDA_VISIBLE_DEVICES` → which GPU index(es) to use
- `BFCL_NUM_GPUS` → number of GPUs bfcl uses
- `BFCL_GPU_UTIL` → GPU memory utilization (0–1)
- `VALIDATOR_NAME`, `COLDKEY`, `HOTKEY` → your identity/wallet names used by the API
- Watcher/auth lines (`SCREENER_API_URL`, `WATCHER_HOST`, hotkey allowlists) as provided by your setup

> **Note:** Score/eval paths are **not configurable**; they are derived from `BFCL_ROOT` and the BFCL repo layout.

---

## 5) Run locally

```bash
# terminal 1
cd ~/BFCL/BFCL-BountyScreener
source ~/BFCL/.v4env/bin/activate
python3 main.py
```

Health check:
```bash
# terminal 2
curl http://127.0.0.1:8999/health
```

---

## 6) (Optional) Run under pm2

```bash
npm install -g pm2
cd ~/BFCL/BFCL-BountyScreener
pm2 start "bash -lc 'source ~/BFCL/.v4env/bin/activate && python3 main.py'" --name scorer
pm2 save
pm2 status
pm2 logs scorer
# restart after config changes
pm2 restart scorer
```

---

## 7) Submit a scoring job

```bash
curl -X POST http://127.0.0.1:8999/score   -H "Content-Type: application/json"   -d '{
    "job_id": "job-123",
    "submission_type": "LINK",
    "content": "https://huggingface.co/Org/Model"
  }'
```

- The response returns `status: "started"`.
- Progress/results are streamed to your watcher (HTTP/WebSocket).
- Final score is read from BFCL’s `score/data_overall.csv` (auto‑cleared before each run).

---

## 8) Troubleshooting

- **Private HF repos**: ensure your venv has access to `HF_TOKEN` (env var) or `~/.huggingface/token`.
- **Path assumptions**:
  - venv at `$BOUNTY_HUNTER_DIR/.v4env`
  - BFCL at `$BFCL_ROOT` (e.g., `~/BFCL/gorilla/berkeley-function-call-leaderboard`)
- **GPU selection**: confirm with `nvidia-smi`; set `CUDA_VISIBLE_DEVICES` accordingly.
- **Auth**: if `SCORER_AUTH_ENABLED=true`, requests must use allowed hotkeys.
- **Timeouts**: `SCORING_TIMEOUT` controls subprocess max runtime (seconds).

---

## 9) Quick checklist
- [ ] Cloned **BFCL-BountyScreener** and **gorilla**
- [ ] Created venv at `$BOUNTY_HUNTER_DIR/.v4env` and activated it
- [ ] Installed deps in both repos
- [ ] Filled `.env` with: directories, GPU, validator, wallets, watcher/auth
- [ ] `python3 main.py` runs and `/health` returns OK
- [ ] Can POST to `/score` with a HF model URL

---

## 10) Example `.env` (edit and use)

See `.env.example` in this repo. Core lines are:

```dotenv
# Where you cloned things
BOUNTY_HUNTER_DIR=~/BFCL
BFCL_ROOT=~/BFCL/gorilla/berkeley-function-call-leaderboard

# GPU / BFCL runtime knobs
CUDA_VISIBLE_DEVICES=2
CUDA_DEVICE_ORDER=PCI_BUS_ID
BFCL_NUM_GPUS=1
BFCL_GPU_UTIL=0.9

# Identity / wallets / watcher
VALIDATOR_NAME=Rizzo
COLDKEY=integration_testing
HOTKEY=tester

MAX_CONCURRENT_TASKS=1
WATCHER_HOST=192.168.69.194:8994
SCREENER_ID=${VALIDATOR_NAME}BFCL
SCREENER_NAME=${VALIDATOR_NAME}BFCL
SCREENER_API_URL=http://192.168.69.157:8999
SCREENER_SUPPORTED_BOUNTY_IDS=BFCLV4

AUTH_ENABLED=true
SCORER_AUTH_ENABLED=true
SCORER_ALLOWED_HOTKEYS=5CXRfP2ekFhe62r7q3vppRajJmGhTi7vwvb2yr79jveZ282w
ALLOWED_HOTKEYS=5CXRfP2ekFhe62r7q3vppRajJmGhTi7vwvb2yr79jveZ282w

AUTH_SIGNATURE_TIMEOUT=300
SCORING_TIMEOUT=7200
```