import asyncio
import csv
import os
import re
import shutil
from pathlib import Path
from typing import Optional

# Optional: load .env if available; harmless if not installed
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

from models import SubmissionData, SubmissionType

try:
    from huggingface_hub import snapshot_download
except Exception:
    snapshot_download = None


def _P(val: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(val))).resolve()


# ---------- Paths (env-first), score/eval remain fixed to BFCL repo ----------
# Root of THIS service (Bounty Hunter). Defaults to ~/BFCL to preserve your layout.
BFCL_HOME = _P(os.getenv("BOUNTY_HUNTER_DIR", str(Path.home() / "BFCL")))

# Virtualenv under BFCL_HOME unless overridden
VENV_DIR = _P(os.getenv("VENV_DIR", str(BFCL_HOME / ".v4env")))
VENV_BIN = VENV_DIR / "bin"
PYTHON_BIN = VENV_BIN / "python"
BFCL_BIN = VENV_BIN / "bfcl"

# Where BFCL was cloned (user sets this explicitly)
# Default keeps your prior opinion: ~/BFCL/gorilla/berkeley-function-call-leaderboard
GORILLA_DIR = _P(str(BFCL_HOME / "gorilla"))
BFCL_ROOT = _P(os.getenv("BFCL_ROOT", str(GORILLA_DIR / "berkeley-function-call-leaderboard")))

# Fixed, not configurable via env (to minimize user surface)
BFCL_EVAL_DIR = BFCL_ROOT / "bfcl_eval"
BITAGENT_HANDLER_DST = BFCL_EVAL_DIR / "model_handler" / "local_inference" / "bitagent.py"
SCORE_CSV = BFCL_ROOT / "score" / "data_overall.csv"

# ---------- BFCL CLI defaults (users only change the GPU knobs) ----------
DEFAULT_MODEL_ARG_NAME = "BitAgent/BitAgent-Bounty-8B"
DEFAULT_TEST_CATEGORY = "multiple"
DEFAULT_BACKEND = "sglang"

# GPU knobs from env
DEFAULT_NUM_GPUS = os.getenv("BFCL_NUM_GPUS", "1")
DEFAULT_GPU_UTIL = os.getenv("BFCL_GPU_UTIL", "0.9")
DEFAULT_VISIBLE_DEVICE = os.getenv("CUDA_VISIBLE_DEVICES", "0") 


class BountyTask:
    def __init__(self, job_id: str, logger_func=None):
        self.job_id = job_id
        self.logger_func = logger_func
        self._task = None
        self._cancelled = False

    async def log(self, level: str, message: str, **kwargs):
        if self.logger_func:
            self.logger_func(level, message, self.job_id, **kwargs)

    # -------------------------- Helpers --------------------------

    def _extract_repo_id_from_hf_url(self, url: str) -> Optional[str]:
        """
        Accepts:
          https://huggingface.co/Org/Model
          https://huggingface.co/Org/Model/tree/main
        Returns "Org/Model".
        """
        if not url:
            return None
        m = re.match(r"^https?://huggingface\.co/([^/\s]+)/([^/\s#?]+)", url.strip())
        if not m:
            return None
        return f"{m.group(1)}/{m.group(2)}"

    def _find_handler_in_dir(self, base_dir: Path) -> Optional[Path]:
        # Prefer top-level handler.py, else first match
        top = base_dir / "handler.py"
        if top.exists():
            return top
        matches = list(base_dir.rglob("handler.py"))
        return matches[0] if matches else None

    def _venv_env(self) -> dict:
        """Create env that *forces* the .v4env venv + CUDA device mapping."""
        env = os.environ.copy()
        # Respect caller’s override if set; else default to configured GPU
        env.setdefault("CUDA_DEVICE_ORDER", os.getenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID"))
        env.setdefault("CUDA_VISIBLE_DEVICES", DEFAULT_VISIBLE_DEVICE)

        # Activate venv for subprocesses
        env["VIRTUAL_ENV"] = str(VENV_DIR)
        env["PATH"] = f"{VENV_BIN}:{env.get('PATH','')}"
        # Typical Python venv isolation vars (optional but harmless)
        env.setdefault("PYTHONNOUSERSITE", "1")
        return env

    async def _run_cmd(self, cmd: list[str], cwd: Optional[Path] = None, name: str = "") -> None:
        """Run a subprocess inside the venv; raise with captured logs on failure."""
        env = self._venv_env()
        await self.log("info", f"Running: {' '.join(cmd)}", step=name or "run_cmd")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, err_b = await proc.communicate()
        out, err = out_b.decode(errors="replace"), err_b.decode(errors="replace")

        if out.strip():
            await self.log("debug", f"[stdout] {out.strip()[:4000]}", step=name)
        if err.strip():
            await self.log("debug", f"[stderr] {err.strip()[:4000]}", step=name)

        if proc.returncode != 0:
            raise RuntimeError(f"{name or 'command'} failed (exit {proc.returncode}).")

    def _bfcl_cmd(self) -> list[str]:
        """Prefer venv bfcl binary; else use python -m bfcl."""
        if BFCL_BIN.exists():
            return [str(BFCL_BIN)]
        return [str(PYTHON_BIN), "-m", "bfcl"]

    def _read_overall_from_csv(self, csv_path: Path) -> float:
        if not csv_path.exists():
            raise FileNotFoundError(f"Score CSV not found at {csv_path}")
        import csv as _csv
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                val = row.get("Overall Acc", "") or row.get("Overall Accuracy", "")
                if not val:
                    continue
                val = val.strip()
                if val.endswith("%"):
                    val = val[:-1]
                return round(float(val), 2)
        raise ValueError("Could not find 'Overall Acc' in score CSV.")

    # -------------------------- Public API --------------------------

    async def score(self, submission: SubmissionData) -> float:
        try:
            await self.log("info", "BountyTask scoring process started")
            self._task = asyncio.create_task(self._scoring_process(submission))
            return await self._task
        except asyncio.CancelledError:
            await self.log("warning", "Scoring task was cancelled")
            self._cancelled = True
            raise
        except Exception as e:
            await self.log("error", f"Error in scoring process: {e}", error=str(e))
            raise

    async def _scoring_process(self, submission: SubmissionData) -> float:
        if self._cancelled:
            raise asyncio.CancelledError("Task was cancelled before start")

        # Validate paths early
        if not VENV_BIN.exists() or not PYTHON_BIN.exists():
            raise RuntimeError(f"Expected venv at {VENV_DIR} with python in {PYTHON_BIN}")
        if not BFCL_ROOT.exists():
            raise RuntimeError(f"BFCL leaderboard repo not found at {BFCL_ROOT}")

        # 1) Get HF model URL
        if submission.submission_type not in (SubmissionType.LINK, SubmissionType.TEXT):
            raise ValueError("Submission must be a LINK or TEXT containing a HuggingFace model URL.")
        url = (submission.content or "").strip()
        if not url:
            raise ValueError("No URL provided in submission content.")
        repo_id = self._extract_repo_id_from_hf_url(url)
        if not repo_id:
            raise ValueError(f"Not a valid HuggingFace model URL: {url}")
        if snapshot_download is None:
            raise RuntimeError("huggingface_hub is required but not installed in the venv.")

        await self.log("info", f"Resolved HuggingFace repo_id: {repo_id}", repo_id=repo_id)

        # 2) Download into BFCL-rooted cache (keeps things tidy)
        download_dir = BFCL_ROOT / "hf_models" / repo_id.replace("/", "__")
        download_dir.parent.mkdir(parents=True, exist_ok=True)

        await self.log("info", f"Downloading model to {download_dir}")
        local_path = snapshot_download(
            repo_id=repo_id,
            local_dir=str(download_dir),
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        model_dir = Path(local_path)

        # 3) Swap handler.py
        handler_src = self._find_handler_in_dir(model_dir)
        if not handler_src:
            raise FileNotFoundError(f"Could not find handler.py in {model_dir}")
        await self.log("info", f"Using handler.py at {handler_src}", handler=str(handler_src))

        backup_dst = BITAGENT_HANDLER_DST.with_suffix(".py.bak")
        if BITAGENT_HANDLER_DST.exists() and not backup_dst.exists():
            shutil.copy2(BITAGENT_HANDLER_DST, backup_dst)
            await self.log("info", f"Backed up original bitagent.py to {backup_dst}")

        shutil.copy2(handler_src, BITAGENT_HANDLER_DST)
        await self.log("info", f"Overwrote BFCL bitagent handler with {handler_src}", target=str(BITAGENT_HANDLER_DST))

        # 4) Clear stale score CSV (avoid reading old results)
        try:
            SCORE_CSV.unlink(missing_ok=True)  # py3.11+
        except TypeError:
            if SCORE_CSV.exists():
                SCORE_CSV.unlink()

        # 5) Run bfcl generate inside venv
        gen_cmd = self._bfcl_cmd() + [
            "generate",
            "--model", DEFAULT_MODEL_ARG_NAME,
            "--test-category", DEFAULT_TEST_CATEGORY,
            "--backend", DEFAULT_BACKEND,
            "--num-gpus", DEFAULT_NUM_GPUS,
            "--gpu-memory-utilization", DEFAULT_GPU_UTIL,
            "--local-model-path", str(model_dir),
        ]
        await self._run_cmd(gen_cmd, cwd=BFCL_ROOT, name="bfcl_generate")

        # 6) Run bfcl evaluate inside venv
        eval_cmd = self._bfcl_cmd() + [
            "evaluate",
            "--model", DEFAULT_MODEL_ARG_NAME,
            "--test-category", DEFAULT_TEST_CATEGORY,
        ]
        await self._run_cmd(eval_cmd, cwd=BFCL_ROOT, name="bfcl_evaluate")

        # 7) Parse score and return Overall Acc
        overall = self._read_overall_from_csv(SCORE_CSV)
        await self.log("info", f"Parsed Overall Acc: {overall}%", overall_accuracy=overall)

        if self._cancelled:
            raise asyncio.CancelledError("Task was cancelled during processing")
        return overall

    def cleanup(self):
        try:
            self._cancelled = True
            if self._task and not self._task.done():
                self._task.cancel()
                if self.logger_func:
                    print(f"[{self.job_id}] BountyTask cleanup: cancelled scoring task")
            else:
                if self.logger_func:
                    print(f"[{self.job_id}] BountyTask cleanup: no active task to cancel")
        except Exception as e:
            print(f"[{self.job_id}] Error during BountyTask cleanup: {e}")
