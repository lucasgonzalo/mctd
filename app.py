"""GLPK MathProg web IDE - minimal local backend.

Endpoints:
    GET  /                   -> serves static/index.html
    GET  /api/models         -> list saved .mod files
    GET  /api/models/{name}  -> read one model content
    POST /api/models         -> save {name, content} to models/{name}.mod
    POST /api/run            -> run glpsol on a model, return solution + log
"""

import re
import json
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"
RESULTS_DIR = BASE_DIR / "results"
META_DIR = MODELS_DIR / ".meta"
GLPSOL = "glpsol"
TIMEOUT_SECONDS = 30
RUNS_PER_MODEL = 5  # circular history: oldest runs beyond this are pruned

# Safe file names only: letters, digits, underscore, dash. No paths, no dots.
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
RUN_FILE_RE = re.compile(r"^(?P<model>.+)-(?P<stamp>\d{8}-\d{6})\.(?P<ext>txt|log)$")
STAMP_RE = re.compile(r"^\d{8}-\d{6}$")

MODELS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)
META_DIR.mkdir(exist_ok=True)

app = FastAPI(title="mctd-glpsol")


def validate_name(name: str) -> str:
    """Reject unsafe model names before they touch the filesystem."""
    if not name or not NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid model name: use letters, digits, - and _ only",
        )
    return name


def run_files_for(name: str) -> dict:
    """Group result files by run stamp: {stamp: {'txt': path, 'log': path}}.

    The strict timestamp suffix keeps 'tp' from matching 'tp-1' runs.
    """
    pattern = re.compile(rf"^{re.escape(name)}-(\d{{8}}-\d{{6}})\.(txt|log)$")
    runs: dict = {}
    if RESULTS_DIR.is_dir():
        for f in RESULTS_DIR.iterdir():
            m = pattern.match(f.name)
            if m:
                runs.setdefault(m.group(1), {})[m.group(2)] = f
    return runs


def fmt_stamp(s: str) -> str:
    """'20260825-022027' -> '2026-08-25 02:20:27'."""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[9:11]}:{s[11:13]}:{s[13:15]}"


def created_at(name: str) -> str:
    """Creation timestamp for a model, persisted in models/.meta/{name}.json.

    Linux filesystems don't expose a reliable birth time, and mtime changes on
    every save, so the app records 'created' once when the model first appears.
    Missing meta (files created outside the app) is backfilled from mtime.
    """
    meta = META_DIR / f"{name}.json"
    if meta.exists():
        try:
            return json.loads(meta.read_text(encoding="utf-8"))["created"]
        except (json.JSONDecodeError, KeyError):
            pass  # fall through and backfill
    path = MODELS_DIR / f"{name}.mod"
    created = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    meta.write_text(json.dumps({"created": created}), encoding="utf-8")
    return created


def all_runs(limit: int = 10) -> list:
    """Most recent executions across all models: [{model, stamp}, ...]."""
    seen = {}
    for f in RESULTS_DIR.iterdir():
        m = RUN_FILE_RE.match(f.name)
        if m:
            seen[(m.group("model"), m.group("stamp"))] = f.stat().st_mtime
    ranked = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"model": model, "raw": raw, "stamp": fmt_stamp(raw)} for (model, raw), _ in ranked]


def prune_runs(name: str, keep: int = RUNS_PER_MODEL) -> int:
    """Circular history per model: keep the newest `keep` runs, delete the rest.

    Fixed-width stamps sort chronologically as plain strings.
    """
    runs = run_files_for(name)
    stamps = sorted(runs)
    removed = 0
    for stamp in stamps[:-keep] if keep > 0 else stamps:
        for f in runs[stamp].values():
            if f.exists():
                f.unlink()
                removed += 1
    return removed


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/guide")
def guide():
    return FileResponse(BASE_DIR / "static" / "guide.html")


@app.get("/api/models")
def list_models():
    return [
        {"name": m.stem, "created": created_at(m.stem)}
        for m in MODELS_DIR.glob("*.mod")
    ]


@app.get("/api/runs")
def list_runs():
    return all_runs(10)


@app.get("/api/runs/{name}/{raw}")
def get_run(name: str, raw: str):
    validate_name(name)
    if not STAMP_RE.match(raw):
        raise HTTPException(status_code=400, detail="Invalid run stamp")
    txt = RESULTS_DIR / f"{name}-{raw}.txt"
    log = RESULTS_DIR / f"{name}-{raw}.log"
    if not txt.exists() and not log.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "model": name,
        "stamp": fmt_stamp(raw),
        "solution": txt.read_text(encoding="utf-8", errors="replace") if txt.exists() else "",
        "log": log.read_text(encoding="utf-8", errors="replace") if log.exists() else "",
    }


@app.get("/api/models/{name}")
def get_model(name: str):
    validate_name(name)
    path = MODELS_DIR / f"{name}.mod"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Model not found")

    last_run = None
    runs = run_files_for(name)
    if runs:
        stamp = max(runs)  # fixed-width stamps sort chronologically
        pair = runs[stamp]
        txt, log = pair.get("txt"), pair.get("log")
        last_run = {
            "exit_code": None,  # not persisted; status line renders as restored run
            "stamp": fmt_stamp(stamp),
            "raw": stamp,
            "model": name,
            "solution": txt.read_text(encoding="utf-8", errors="replace") if txt and txt.exists() else "",
            "log": log.read_text(encoding="utf-8", errors="replace") if log and log.exists() else "",
            "solution_file": txt.name if txt else None,
        }

    return {"name": name, "content": path.read_text(encoding="utf-8"), "last_run": last_run}


@app.delete("/api/models/{name}")
def delete_model(name: str):
    validate_name(name)
    path = MODELS_DIR / f"{name}.mod"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Model not found")
    path.unlink()
    removed = 0
    for pair in run_files_for(name).values():
        for f in pair.values():
            if f.exists():
                f.unlink()
                removed += 1
    return {"status": "deleted", "name": name, "result_files_removed": removed}


@app.post("/api/models")
def save_model(payload: dict):
    name = validate_name(payload.get("name", ""))
    content = payload.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Model content is empty")
    path = MODELS_DIR / f"{name}.mod"
    is_new = not path.exists()
    path.write_text(content, encoding="utf-8")
    if is_new:
        (META_DIR / f"{name}.json").write_text(
            json.dumps({"created": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8"
        )
    return {"status": "saved", "name": name}


@app.post("/api/run")
def run_model(payload: dict):
    name = validate_name(payload.get("name", ""))
    path = MODELS_DIR / f"{name}.mod"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Model not found - save it first")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_file = RESULTS_DIR / f"{name}-{stamp}.txt"
    log_file = RESULTS_DIR / f"{name}-{stamp}.log"

    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, shell disabled
            [GLPSOL, "--model", str(path), "--output", str(out_file), "--log", str(log_file)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail=f"glpsol timed out after {TIMEOUT_SECONDS}s")

    solution = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""
    log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
    pruned = prune_runs(name)

    return {
        "exit_code": proc.returncode,
        "stamp": fmt_stamp(stamp),
        "raw": stamp,
        "model": name,
        "solution": solution,
        "log": log,
        "solution_file": out_file.name,
        "log_file": log_file.name,
        "pruned_runs": pruned,
    }


# Static assets (only /static mount; index is served explicitly above)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
