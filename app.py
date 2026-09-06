"""GLPK MathProg web IDE - minimal local backend.

Endpoints (model names are paths, docs/req_3.md — flat = depth 0):
    GET    /                        -> serves static/index.html
    GET    /api/models              -> folder + model tree
    GET    /api/models/{name:path}  -> read one model content
    POST   /api/models              -> save {name, content} to models/{name}.mod
    POST   /api/models/move         -> {from, to, type} rename/move model or folder
    POST   /api/models/folders      -> {path} create folder
    DELETE /api/models/{name:path}  -> delete model, or folder (?type=folder)
    GET    /api/runs                -> recent runs; ?model= and ?model=&raw= filters
    GET    /api/config              -> current configuration (file merged over defaults)
    PUT    /api/config              -> validate + persist config/config.json
    POST   /api/run                 -> run glpsol on a model, return solution + log
"""

import re
import json
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# All user-visible timestamps are Argentina time (GMT-3): stamps and creation
# dates are displayed as generated, so the container's default UTC (no TZ set
# in python:*-slim) must never leak into them.
TZ = ZoneInfo("America/Argentina/Buenos_Aires")

BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"
RESULTS_DIR = BASE_DIR / "results"
META_DIR = MODELS_DIR / ".meta"
CONFIG_DIR = BASE_DIR / "config"
CONFIG_PATH = CONFIG_DIR / "config.json"
GLPSOL = "glpsol"

# Safe file names only: letters, digits, underscore, dash. No paths, no dots.
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
RUN_FILE_RE = re.compile(r"^(?P<model>.+)-(?P<stamp>\d{8}-\d{6})\.(?P<ext>txt|log|sol|rng)$")
STAMP_RE = re.compile(r"^\d{8}-\d{6}$")
# glpsol reports parse errors as "path/to/model.mod:<line>: <message>"
ERROR_LINE_RE = re.compile(r"^\S*\.mod:(\d+):(.*)$", re.MULTILINE)

MODELS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)
META_DIR.mkdir(exist_ok=True)
CONFIG_DIR.mkdir(exist_ok=True)

app = FastAPI(title="mctd-glpsol")

# One glpsol at a time: uvicorn runs sync endpoints in a threadpool, so a
# retry from another tab (or after a page refresh, which orphans the first
# request server-side) would stack glpsol processes competing for CPU until
# the hard timeout kills them all. Valid for the single-process uvicorn
# deployment in the Dockerfile (no --workers).
_run_lock = threading.Lock()
_active_run = None  # {"model": str, "started": str} while a run is live


# Model/folder paths (docs/req_3.md): 1-5 NAME_RE segments joined by '/'.
# A flat name is a depth-0 path, so legacy models and results keep working
# with zero migration. NAME_RE already bans '.' and '..', and empty segments
# fail the per-segment match.
MAX_PATH_DEPTH = 5
MAX_PATH_LEN = 120


def validate_path(name: str) -> str:
    """Reject unsafe model/folder paths before they touch the filesystem."""
    if (
        not name
        or len(name) > MAX_PATH_LEN
        or len(name.split("/")) > MAX_PATH_DEPTH
        or any(not NAME_RE.match(s) for s in name.split("/"))
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Ruta inválida: segmentos con letras, números, - y _, "
                "separados por / (máx. 5 niveles)"
            ),
        )
    return name


def model_path(name: str) -> Path:
    return MODELS_DIR / f"{name}.mod"


def meta_path(name: str) -> Path:
    return META_DIR / f"{name}.json"


def models_under(folder: str) -> list:
    """Model paths inside a folder (recursive), for move/delete guards."""
    base = MODELS_DIR / folder
    if not base.is_dir():
        return []
    return [str(p.relative_to(MODELS_DIR).with_suffix("")) for p in base.rglob("*.mod")]


# --- configuration (docs/req_1.md) ---

DEFAULT_CONFIG = {
    "solver": {
        "method": "simplex",          # simplex | interior
        "simplex_variant": "primal",  # primal | dual (simplex only)
        "presolve": True,
        "exact_check": False,
        "seed": None,                 # MathProg RNG seed; None = don't pass --seed
        "tmlim": 25,                  # soft limit: glpsol returns best solution so far
        "memlim": None,               # MB; None = unlimited
    },
    "mip": {
        "relax": False,               # --nomip: solve MIP as relaxed LP
        "mipgap": None,               # relative optimality gap; None = GLPK default
        "cuts": False,                # Gomory + MIR + cover + clique cuts
    },
    "output": {
        "sensitivity": True,          # --ranges report (simplex only, see validate)
        "plain_solution": True,       # -w plain-text solution artifact
        "mode": "pretty",             # raw | pretty | both
    },
    "runtime": {
        "timeout_seconds": 30,        # hard subprocess kill; must beat tmlim by 5s
        "runs_per_model": 5,          # circular history; 0 = unlimited
    },
    "editor": {
        "template": "var x >= 0;\nvar y >= 0;\n\nmaximize z: x + 2*y;\n\ns.t. r: x + y <= 4;\n\nend;",
    },
}

EDITOR_TEMPLATE_FALLBACK = "var x >= 0;\nvar y >= 0;\n\nmaximize z: x + 2*y;\n\ns.t. r: x + y <= 4;\n\nend;"


def deep_merge(base: dict, override: dict) -> dict:
    """Merge override onto base; unknown keys in override are dropped."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def load_config() -> dict:
    """Current config: file merged over defaults. Missing/corrupt file -> defaults."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    cfg = deep_merge(DEFAULT_CONFIG, data)
    if not cfg["editor"].get("template"):
        cfg["editor"]["template"] = EDITOR_TEMPLATE_FALLBACK
    return cfg


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _unknown_keys(ref: dict, got: dict, prefix: str = "") -> list:
    """Schema keys present in `got` but not in `ref`, as 'a.b' strings."""
    unknown = []
    for k, v in got.items():
        if k not in ref:
            unknown.append(f"{prefix}{k}")
        elif isinstance(ref.get(k), dict) and isinstance(v, dict):
            unknown += _unknown_keys(ref[k], v, f"{prefix}{k}.")
    return unknown


def validate_config(cfg: dict) -> dict:
    """Field errors keyed 'section.key' with user-facing Spanish messages."""
    errors = {}
    s, m, o, r = cfg["solver"], cfg["mip"], cfg["output"], cfg["runtime"]

    if s["method"] not in ("simplex", "interior"):
        errors["solver.method"] = "Debe ser 'simplex' o 'interior'."
    if s["simplex_variant"] not in ("primal", "dual"):
        errors["solver.simplex_variant"] = "Debe ser 'primal' o 'dual'."
    if not isinstance(s["presolve"], bool):
        errors["solver.presolve"] = "Debe ser verdadero o falso."
    if not isinstance(s["exact_check"], bool):
        errors["solver.exact_check"] = "Debe ser verdadero o falso."
    if s["seed"] is not None and (not _is_int(s["seed"]) or s["seed"] < 0):
        errors["solver.seed"] = "Debe ser un entero >= 0 o vacío."
    if not _is_int(s["tmlim"]) or s["tmlim"] < 1:
        errors["solver.tmlim"] = "Debe ser un entero >= 1 (segundos)."
    if s["memlim"] is not None and (not _is_int(s["memlim"]) or s["memlim"] < 1):
        errors["solver.memlim"] = "Debe ser un entero >= 1 (MB) o vacío."

    if not isinstance(m["relax"], bool):
        errors["mip.relax"] = "Debe ser verdadero o falso."
    if not isinstance(m["cuts"], bool):
        errors["mip.cuts"] = "Debe ser verdadero o falso."
    if m["mipgap"] is not None and (not _is_num(m["mipgap"]) or m["mipgap"] <= 0):
        errors["mip.mipgap"] = "Debe ser un número > 0 o vacío."

    if not isinstance(o["sensitivity"], bool):
        errors["output.sensitivity"] = "Debe ser verdadero o falso."
    if not isinstance(o["plain_solution"], bool):
        errors["output.plain_solution"] = "Debe ser verdadero o falso."
    if o["mode"] not in ("raw", "pretty", "both"):
        errors["output.mode"] = "Debe ser 'raw', 'pretty' o 'both'."

    if not _is_int(r["timeout_seconds"]) or not 5 <= r["timeout_seconds"] <= 600:
        errors["runtime.timeout_seconds"] = "Debe estar entre 5 y 600 segundos."
    if not _is_int(r["runs_per_model"]) or r["runs_per_model"] < 0:
        errors["runtime.runs_per_model"] = "Debe ser un entero >= 0 (0 = ilimitado)."

    # Cross rules
    if s["method"] == "interior" and o["sensitivity"] and "solver.method" not in errors:
        errors["output.sensitivity"] = (
            "El análisis de sensibilidad solo está disponible con el método simplex."
        )
    if (
        _is_int(s["tmlim"])
        and _is_int(r["timeout_seconds"])
        and s["tmlim"] + 5 > r["timeout_seconds"]
    ):
        errors["solver.tmlim"] = (
            "El límite blando debe ser al menos 5 segundos menor que el límite "
            "duro (timeout)."
        )
    return errors


def build_argv(cfg: dict, model_path: Path, out_file: Path, sol_file: Path,
               rng_file: Path, log_file: Path) -> list:
    """Translate config into the glpsol argv. Validation already ran, so the
    interior+sensitivity combination cannot reach here."""
    s, mip, out = cfg["solver"], cfg["mip"], cfg["output"]
    argv = [GLPSOL, "--model", str(model_path)]
    if s["method"] == "interior":
        argv.append("--interior")
    elif s["simplex_variant"] == "dual":
        argv.append("--dual")
    if not s["presolve"]:
        argv.append("--nopresol")
    if s["exact_check"]:
        argv.append("--xcheck")
    if s["seed"] is not None:
        argv += ["--seed", str(s["seed"])]
    if s["memlim"] is not None:
        argv += ["--memlim", str(s["memlim"])]
    argv += ["--tmlim", str(s["tmlim"])]
    if mip["relax"]:
        argv.append("--nomip")
    if mip["mipgap"] is not None:
        argv += ["--mipgap", str(mip["mipgap"])]
    if mip["cuts"]:
        argv.append("--cuts")
    argv += ["--output", str(out_file)]
    if out["plain_solution"]:
        argv += ["-w", str(sol_file)]
    if out["sensitivity"]:
        argv += ["--ranges", str(rng_file)]
    argv += ["--log", str(log_file)]
    return argv


def run_files_for(name: str) -> dict:
    """Group result files by run stamp: {stamp: {'txt': path, 'log': path}}.

    results/ mirrors the model tree (docs/req_3.md); a root-level model is
    depth 0, so flat legacy files keep working unchanged. The strict
    timestamp suffix keeps 'tp' from matching 'tp-1' runs.
    """
    parent = RESULTS_DIR / Path(name).parent
    last = Path(name).name
    pattern = re.compile(rf"^{re.escape(last)}-(\d{{8}}-\d{{6}})\.(txt|log|sol|rng)$")
    runs: dict = {}
    if parent.is_dir():
        for f in parent.iterdir():
            m = pattern.match(f.name)
            if m:
                runs.setdefault(m.group(1), {})[m.group(2)] = f
    return runs


def fmt_stamp(s: str) -> str:
    """'20260825-022027' -> '2026-08-25 02:20:27'."""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[9:11]}:{s[11:13]}:{s[13:15]}"


def parse_glpk_error(log: str) -> tuple:
    """(line, message) of the first syntax error in a glpsol log, or (None, None).

    glpsol emits warnings in the same 'file.mod:LINE: msg' shape (e.g. a
    missing 'end;' is auto-inserted with a warning) — those are skipped so
    successful runs don't get flagged.
    """
    if not log:
        return None, None
    for m in ERROR_LINE_RE.finditer(log):
        msg = m.group(2).strip()
        if not msg.lower().startswith("warning"):
            return int(m.group(1)), msg
    m = re.search(r"\berror\b[^\n]*\bline\s+(\d+)", log, re.IGNORECASE)
    if m:
        return int(m.group(1)), "error reported by glpsol"
    return None, None


def created_at(name: str) -> str:
    """Creation timestamp for a model, persisted in models/.meta/{name}.json.

    Linux filesystems don't expose a reliable birth time, and mtime changes on
    every save, so the app records 'created' once when the model first appears.
    Missing meta (files created outside the app) is backfilled from mtime.
    """
    meta = meta_path(name)
    if meta.exists():
        try:
            return json.loads(meta.read_text(encoding="utf-8"))["created"]
        except (json.JSONDecodeError, KeyError):
            pass  # fall through and backfill
    path = model_path(name)
    created = datetime.fromtimestamp(path.stat().st_mtime, TZ).isoformat(timespec="seconds")
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"created": created}), encoding="utf-8")
    return created


def all_runs(limit: int = 10) -> list:
    """Most recent executions across all models: [{model, raw, stamp}, ...].

    rglob walks the mirrored results tree; the model path is dirname +
    filename minus the -{stamp}.{ext} suffix (greedy match is deterministic).
    """
    seen = {}
    if RESULTS_DIR.is_dir():
        for f in RESULTS_DIR.rglob("*"):
            if not f.is_file():
                continue
            m = RUN_FILE_RE.match(f.name)
            if not m:
                continue
            rel_parent = f.relative_to(RESULTS_DIR).parent
            model = f"{rel_parent}/{m.group('model')}" if str(rel_parent) != "." else m.group("model")
            seen[(model, m.group("stamp"))] = f.stat().st_mtime
    ranked = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"model": model, "raw": raw, "stamp": fmt_stamp(raw)} for (model, raw), _ in ranked]


def prune_runs(name: str, keep: int) -> int:
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


def read_run_artifacts(out_file: Path, sol_file: Path, rng_file: Path,
                       log_file: Path) -> dict:
    """Best-effort read of the run artifacts that exist on disk.

    glpsol streams its log while it runs, so even a process killed by the
    hard timeout leaves a partial .log behind; .sol/.txt exist only if it
    got that far before dying.
    """
    return {
        "solution": out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else "",
        "sol": sol_file.read_text(encoding="utf-8", errors="replace") if sol_file.exists() else "",
        "ranges": rng_file.read_text(encoding="utf-8", errors="replace") if rng_file.exists() else "",
        "log": log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else "",
    }


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/guide")
def guide():
    return FileResponse(BASE_DIR / "static" / "guide.html")


def walk_tree() -> tuple:
    """(folders, model names) under models/, excluding .meta.

    Folders are real directories (empty ones included); models are paths
    without the .mod suffix.
    """
    folders, models = [], []
    for p in MODELS_DIR.rglob("*"):
        rel = p.relative_to(MODELS_DIR)
        if rel.parts[0] == ".meta":
            continue
        if p.is_dir():
            folders.append(str(rel))
        elif p.suffix == ".mod":
            models.append(str(rel.with_suffix("")))
    return sorted(folders), models


@app.get("/api/models")
def list_models():
    folders, names = walk_tree()
    return {
        "folders": folders,
        "models": [{"name": n, "created": created_at(n)} for n in names],
    }


@app.get("/api/runs")
def list_runs(model: str = None, raw: str = None):
    """Recent runs across all models; ?model= filters one model, and
    ?model=&raw= returns a single run detail. (docs/req_3.md: the old
    two-segment /api/runs/{name}/{raw} route cannot carry paths with '/')"""
    if model is not None and raw is not None:
        validate_path(model)
        if not STAMP_RE.match(raw):
            raise HTTPException(status_code=400, detail="Invalid run stamp")
        parent = RESULTS_DIR / Path(model).parent
        last = Path(model).name
        txt = parent / f"{last}-{raw}.txt"
        log = parent / f"{last}-{raw}.log"
        sol = parent / f"{last}-{raw}.sol"
        rng = parent / f"{last}-{raw}.rng"
        if not txt.exists() and not log.exists():
            raise HTTPException(status_code=404, detail="Run not found")
        return {
            "model": model,
            "raw": raw,
            "stamp": fmt_stamp(raw),
            "exit_code": None,
            "solution": txt.read_text(encoding="utf-8", errors="replace") if txt.exists() else "",
            "sol": sol.read_text(encoding="utf-8", errors="replace") if sol.exists() else "",
            "ranges": rng.read_text(encoding="utf-8", errors="replace") if rng.exists() else "",
            "log": log.read_text(encoding="utf-8", errors="replace") if log.exists() else "",
        }
    if model is not None:
        validate_path(model)
        if not model_path(model).exists():
            raise HTTPException(status_code=404, detail="Model not found")
        runs = run_files_for(model)
        return [
            {"model": model, "raw": r, "stamp": fmt_stamp(r)}
            for r in sorted(runs, reverse=True)[:20]
        ]
    return all_runs(10)


@app.get("/api/models/{name:path}")
def get_model(name: str):
    validate_path(name)
    path = model_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Model not found")

    last_run = None
    runs = run_files_for(name)
    if runs:
        stamp = max(runs)  # fixed-width stamps sort chronologically
        pair = runs[stamp]
        txt, log = pair.get("txt"), pair.get("log")
        sol, rng = pair.get("sol"), pair.get("rng")
        last_run = {
            "exit_code": None,  # not persisted; status line renders as restored run
            "stamp": fmt_stamp(stamp),
            "raw": stamp,
            "model": name,
            "solution": txt.read_text(encoding="utf-8", errors="replace") if txt and txt.exists() else "",
            "sol": sol.read_text(encoding="utf-8", errors="replace") if sol and sol.exists() else "",
            "ranges": rng.read_text(encoding="utf-8", errors="replace") if rng and rng.exists() else "",
            "log": log.read_text(encoding="utf-8", errors="replace") if log and log.exists() else "",
            "solution_file": txt.name if txt else None,
        }
        last_run["error_line"], last_run["error_msg"] = parse_glpk_error(last_run["log"])

    return {"name": name, "content": path.read_text(encoding="utf-8"), "last_run": last_run}


@app.delete("/api/models/{name:path}")
def delete_model(name: str, type: str = "model"):  # noqa: A002 - query param name
    """Delete a model, or a folder with everything inside (?type=folder)."""
    validate_path(name)
    if type not in ("model", "folder"):
        raise HTTPException(status_code=400, detail="type must be 'model' or 'folder'")
    affected = [name] if type == "model" else models_under(name)
    if _active_run and _active_run["model"] in affected:
        raise HTTPException(
            status_code=409,
            detail="Hay una corrida en curso de este modelo; esperá a que termine.",
        )

    if type == "model":
        path = model_path(name)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Model not found")
        path.unlink()
        meta = meta_path(name)
        if meta.exists():
            meta.unlink()
        removed = 0
        for pair in run_files_for(name).values():
            for f in pair.values():
                if f.exists():
                    f.unlink()
                    removed += 1
        return {"status": "deleted", "name": name, "result_files_removed": removed}

    # folder: recursive delete of models + meta + results subtrees
    folder = MODELS_DIR / name
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")
    removed = 0
    for m in affected:
        for pair in run_files_for(m).values():
            for f in pair.values():
                if f.exists():
                    f.unlink()
                    removed += 1
    shutil.rmtree(folder)
    shutil.rmtree(META_DIR / name, ignore_errors=True)
    shutil.rmtree(RESULTS_DIR / name, ignore_errors=True)
    return {
        "status": "deleted",
        "name": name,
        "models_removed": len(affected),
        "result_files_removed": removed,
    }


@app.post("/api/models/move")
def move_model(payload: dict):
    """Rename/move a model or a whole folder (docs/req_3.md).

    Model: moves .mod + .meta + run artifacts (history follows the model).
    Folder: moves the three mirrored subtrees at once. Rejected while a run
    of any affected model is live.
    """
    fr = validate_path(payload.get("from", ""))
    to = validate_path(payload.get("to", ""))
    kind = payload.get("type", "model")
    if kind not in ("model", "folder"):
        raise HTTPException(status_code=400, detail="type must be 'model' or 'folder'")
    if to == fr or to.startswith(fr + "/"):
        raise HTTPException(status_code=400, detail="No se puede mover dentro de sí mismo")
    affected = [fr] if kind == "model" else models_under(fr)
    if _active_run and _active_run["model"] in affected:
        raise HTTPException(
            status_code=409,
            detail="Hay una corrida en curso de este modelo; esperá a que termine.",
        )

    if kind == "model":
        src = model_path(fr)
        if not src.exists():
            raise HTTPException(status_code=404, detail="Model not found")
        if model_path(to).exists() or (MODELS_DIR / to).exists():
            raise HTTPException(status_code=409, detail="Ya existe un modelo o carpeta con ese nombre")
        model_path(to).parent.mkdir(parents=True, exist_ok=True)
        src.rename(model_path(to))
        if meta_path(fr).exists():
            meta_path(to).parent.mkdir(parents=True, exist_ok=True)
            meta_path(fr).rename(meta_path(to))  # keeps the original creation date
        moved = 0
        dest_dir = RESULTS_DIR / Path(to).parent
        for pair in run_files_for(fr).values():
            dest_dir.mkdir(parents=True, exist_ok=True)
            for f in pair.values():
                if f.exists():
                    # {from}-{stamp}.{ext} -> {to}-{stamp}.{ext}
                    f.rename(dest_dir / f"{Path(to).name}{f.name[len(Path(fr).name):]}")
                    moved += 1
        return {"status": "moved", "from": fr, "to": to, "result_files_moved": moved}

    # folder move: the three subtrees keep mirroring each other
    src = MODELS_DIR / fr
    if not src.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")
    dst = MODELS_DIR / to
    if dst.exists() or model_path(to).exists():
        raise HTTPException(status_code=409, detail="Ya existe un modelo o carpeta con ese nombre")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    if (META_DIR / fr).is_dir():
        (META_DIR / to).parent.mkdir(parents=True, exist_ok=True)
        (META_DIR / fr).rename(META_DIR / to)
    if (RESULTS_DIR / fr).is_dir():
        (RESULTS_DIR / to).parent.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / fr).rename(RESULTS_DIR / to)
    return {"status": "moved", "from": fr, "to": to, "models_moved": len(affected)}


@app.post("/api/models/folders")
def create_folder(payload: dict):
    """Create a (possibly nested) folder under models/."""
    path = validate_path(payload.get("path", ""))
    folder = MODELS_DIR / path
    if folder.exists() or model_path(path).exists():
        raise HTTPException(status_code=409, detail="Ya existe una carpeta o modelo con ese nombre")
    folder.mkdir(parents=True, exist_ok=True)
    return {"status": "created", "path": path}


@app.post("/api/models")
def save_model(payload: dict):
    name = validate_path(payload.get("name", ""))
    content = payload.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Model content is empty")
    path = model_path(name)
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if is_new:
        meta_path(name).parent.mkdir(parents=True, exist_ok=True)
        meta_path(name).write_text(
            json.dumps({"created": datetime.now(TZ).isoformat(timespec="seconds")}), encoding="utf-8"
        )
    return {"status": "saved", "name": name}


@app.get("/api/config")
def get_config():
    return load_config()


@app.put("/api/config")
def put_config(payload: dict):
    unknown = _unknown_keys(DEFAULT_CONFIG, payload or {})
    if unknown:
        raise HTTPException(
            status_code=400,
            detail={"errors": {"_": f"Claves desconocidas: {', '.join(sorted(unknown))}"}},
        )
    cfg = deep_merge(DEFAULT_CONFIG, payload or {})
    if not cfg["editor"].get("template"):
        cfg["editor"]["template"] = EDITOR_TEMPLATE_FALLBACK
    errors = validate_config(cfg)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    CONFIG_DIR.mkdir(exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_PATH)  # atomic: readers never see a half-written file
    return cfg


@app.post("/api/run")
def run_model(payload: dict):
    global _active_run
    name = validate_path(payload.get("name", ""))
    path = model_path(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Model not found - save it first")

    # Reject instead of queue: a second concurrent glpsol would only split
    # CPU with the live one and end in mutual hard-kills (retries made the
    # original 300s cutoff worse). String detail: the frontend alert()s it.
    if not _run_lock.acquire(blocking=False):
        active = _active_run or {}
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ya hay una corrida en curso (modelo {active.get('model', '?')}, "
                f"iniciada {active.get('started', '?')}). Esperá a que termine."
            ),
        )

    try:
        cfg = load_config()  # read fresh: applies config changes without a restart
        timeout = cfg["runtime"]["timeout_seconds"]

        stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
        # results/ mirrors the model tree: ensure the subdirectory exists
        (RESULTS_DIR / Path(name).parent).mkdir(parents=True, exist_ok=True)
        out_file = RESULTS_DIR / f"{name}-{stamp}.txt"
        sol_file = RESULTS_DIR / f"{name}-{stamp}.sol"
        rng_file = RESULTS_DIR / f"{name}-{stamp}.rng"
        log_file = RESULTS_DIR / f"{name}-{stamp}.log"
        argv = build_argv(cfg, path, out_file, sol_file, rng_file, log_file)
        _active_run = {"model": name, "started": fmt_stamp(stamp)}

        killed = False
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, shell disabled
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # glpsol was SIGKILLed at the hard limit. Salvage whatever it
            # left on disk instead of returning a bare 504: the partial log
            # (and the solution, if tmlim fired and files were written) is
            # still valuable. Note: a SIGTERM first is pointless, GLPK has
            # no handler for it.
            killed = True
            proc = None

        artifacts = read_run_artifacts(out_file, sol_file, rng_file, log_file)
        pruned = prune_runs(name, keep=cfg["runtime"]["runs_per_model"])
        err_line, err_msg = parse_glpk_error(artifacts["log"])

        return {
            "exit_code": proc.returncode if proc is not None else None,
            "killed_by_timeout": killed,
            "timeout_seconds": timeout,
            "stamp": fmt_stamp(stamp),
            "raw": stamp,
            "model": name,
            "solution": artifacts["solution"],
            "sol": artifacts["sol"],
            "ranges": artifacts["ranges"],
            "log": artifacts["log"],
            "solution_file": out_file.name,
            "log_file": log_file.name,
            "command": argv,
            "pruned_runs": pruned,
            "error_line": err_line,
            "error_msg": err_msg,
        }
    finally:
        _active_run = None
        _run_lock.release()


# Static assets (only /static mount; index is served explicitly above)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
