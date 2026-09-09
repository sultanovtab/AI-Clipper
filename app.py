"""Phase 1+2: local, read-only media inspection and local CPU transcription."""

import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
PORT = 8765
ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
selected_paths: set[str] = set()
selection_lock = threading.Lock()
picker_lock = threading.Lock()
app = FastAPI(title="AI Clipper", docs_url=None, redoc_url=None, openapi_url=None)

# --- Local transcription (Phase 2) -------------------------------------------
WHISPER_EXE = ROOT / "tools" / "whisper" / "whisper-cli.exe"
WHISPER_MODELS = ROOT / "models" / "whisper"
WHISPER_DEFAULT_MODEL = WHISPER_MODELS / "ggml-base.bin"
ANALYSIS_DIR = ROOT / "analysis"
TEMP_DIR = ROOT / "temp"
CHUNK_SECONDS = 20 * 60  # 20-minute chunks
THREADS = 8
BACKEND = "CPU"
LANG_CODES = {"auto": "auto", "ru": "ru", "en": "en", "de": "de"}

_transcribe_lock = threading.Lock()
_active_job: "TranscribeJob | None" = None
# Keep finished job (success/fail/cancel) so the UI can read its state.
_last_job: "TranscribeJob | None" = None


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    raw = f"{str(path)}\0{stat.st_size}\0{int(stat.st_mtime * 1000)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_name(value: str) -> str:
    """Filesystem-safe key fragment from a model or language name."""
    cleaned = "".join(c for c in value if c.isalnum() or c in "._-")
    return cleaned or "base"


def _analysis_file(path: Path, model: Path = WHISPER_DEFAULT_MODEL,
                 language: str = "auto") -> Path:
    """A transcript cache is specific to source + model + language.

    Changing the Whisper model or language selects a different cache file, so a
    resumed job never reuses segments produced by an incompatible transcription.
    """
    digest = _fingerprint(path)[:12]
    model_key = _safe_name(model.name if isinstance(model, Path) else str(model))
    lang_key = _safe_name(language or "auto")
    return ANALYSIS_DIR / f"transcript-{digest}-{model_key}-{lang_key}.json"


class TranscribeJob:
    def __init__(self, path: Path):
        self.path = path
        self.cancel = threading.Event()
        self.running = False
        self.failed = False
        self.error = None
        self.cancelled = False
        self.total_chunks = 0
        self.completed_chunks = 0
        self.current_chunk = 0       # 1-based chunk index being worked on
        self.processed_duration = 0.0
        self.language = "auto"
        self.model = WHISPER_DEFAULT_MODEL.name
        self.segments: list[dict] = []
        self.duration = 0.0
        self.started_at = None
        self._lock = threading.Lock()


def _load_saved(file: Path):
    """Return a dict of saved transcript data, or None if absent/corrupt/or wrong shape."""
    try:
        with open(file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        return None
    return data


def _run_raw(args, timeout=900):
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _short_error(text: str, limit: int = 200) -> str:
    """Return a short, single-line tool error message for the UI."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return "no error output"
    return lines[-1][:limit]


def _transcribe_chunk(wav: Path, out_base: Path, language: str, model: Path):
    args = [str(WHISPER_EXE), "-m", str(model), "-f", str(wav),
            "-l", language, "-t", str(THREADS), "-oj", "-of", str(out_base), "-np"]
    result = _run_raw(args)
    if result.returncode:
        raise RuntimeError(
            f"whisper-cli failed (exit {result.returncode}): "
            f"{_short_error(result.stderr)}"
        )
    json_path = Path(f"{out_base}.json")
    try:
        with open(json_path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RuntimeError("whisper-cli returned no readable JSON.") from exc
    return data


def _run_worker(job: TranscribeJob, language: str, model: Path, retranscribe: bool):
    try:
        file = _analysis_file(job.path, model, language)
        job.duration = 0.0

        # Total duration from the source itself.
        probe = _run_raw([
            shutil.which("ffprobe") or "ffprobe", "-v", "error",
            "-protocol_whitelist", "file", "-show_entries", "format=duration",
            "-of", "json", str(job.path),
        ])
        if probe.returncode:
            raise RuntimeError("Could not read the video duration.")
        probe_data = json.loads(probe.stdout)
        duration = number((probe_data.get("format") or {}).get("duration"))
        if duration is None or duration <= 0:
            raise RuntimeError("This video reports no playable duration.")
        job.duration = duration

        total = int(math.ceil(duration / CHUNK_SECONDS))
        if total < 1:
            total = 1
        job.total_chunks = total

        fingerprint = _fingerprint(job.path)

        if retranscribe:
            done = 0
            segments: list[dict] = []
        else:
            saved = _load_saved(file)
            if saved and saved.get("fingerprint") == fingerprint:
                done = int(saved.get("completed_chunks") or 0)
                segments = list(saved.get("segments") or [])
            else:
                done = 0
                segments = []
            done = min(done, total)
            if done >= total:
                job.segments = segments
                job.completed_chunks = total
                job.processed_duration = duration
                return

        job.segments = segments
        job.completed_chunks = done
        job.processed_duration = min(done * CHUNK_SECONDS, duration)
        job.language = language
        job.model = model.name

        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

        for idx in range(done, total):
            if job.cancel.is_set():
                job.cancelled = True
                return
            job.current_chunk = idx + 1
            start = idx * CHUNK_SECONDS
            end = min(start + CHUNK_SECONDS, duration)
            safe_id = fingerprint[:12]
            wav = TEMP_DIR / f"transcribe-{safe_id}-chunk-{idx:04d}.wav"
            out_base = TEMP_DIR / f"transcribe-{safe_id}-chunk-{idx:04d}"
            json_path = Path(f"{out_base}.json")
            try:
                extract = _run_raw([
                    shutil.which("ffmpeg") or "ffmpeg", "-nostdin", "-y", "-v", "error",
                    "-ss", str(start), "-to", str(end), "-i", str(job.path),
                    "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(wav),
                ], timeout=900)
                if extract.returncode or not wav.is_file() or wav.stat().st_size == 0:
                    raise RuntimeError(
                        f"FFmpeg failed (exit {extract.returncode}): "
                        f"{_short_error(extract.stderr)}"
                    )

                data = _transcribe_chunk(wav, out_base, language, model)

                transcription = data.get("transcription")
                if not isinstance(transcription, list):
                    raise RuntimeError("whisper-cli produced no segments.")
                for seg in transcription:
                    offsets = seg.get("offsets") or {}
                    text = (seg.get("text") or "").strip()
                    if not text:
                        continue
                    from_ms = number(offsets.get("from")) or 0
                    to_ms = number(offsets.get("to")) or from_ms
                    segments.append({
                        "start": round(start + from_ms / 1000.0, 3),
                        "end": round(start + to_ms / 1000.0, 3),
                        "text": text,
                    })
                job.segments = segments
                job.completed_chunks = idx + 1
                job.processed_duration = min((idx + 1) * CHUNK_SECONDS, duration)

                # Save progress after every completed chunk (UTF-8).
                payload = {
                    "source": str(job.path),
                    "fingerprint": fingerprint,
                    "duration": duration,
                    "language": language,
                    "model": model.name,
                    "backend": BACKEND,
                    "threads": THREADS,
                    "completed_chunks": idx + 1,
                    "total_chunks": total,
                    "created_at": _now_iso(),
                    "segments": segments,
                }
                with open(file, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
            finally:
                for tmp in (wav, json_path):
                    try:
                        if tmp.is_file():
                            tmp.unlink()
                    except OSError:
                        pass
                if job.cancel.is_set():
                    job.cancelled = True
                    return
    except Exception as exc:  # noqa: BLE001 - surface any tool failure to the UI
        job.failed = True
        job.error = str(exc) or exc.__class__.__name__
    finally:
        job.running = False


def _analysis_files_for(path: Path) -> list:
    """Return transcript cache files that belong to this exact source."""
    digest = _fingerprint(path)[:12]
    prefix = f"transcript-{digest}-"
    if not ANALYSIS_DIR.is_dir():
        return []
    try:
        return sorted(
            (p for p in ANALYSIS_DIR.iterdir()
             if p.is_file() and p.name.startswith(prefix) and p.suffix == ".json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []


def _get_job(path: Path) -> TranscribeJob:
    """Return the live job for a path, else the most recent finished job, else a report."""
    global _active_job, _last_job
    with _transcribe_lock:
        if _active_job is not None and _active_job.path == path:
            return _active_job
        if _last_job is not None and _last_job.path == path:
            if _last_job.failed or _last_job.cancelled or _last_job.completed_chunks >= _last_job.total_chunks:
                return _last_job
    fingerprint = _fingerprint(path)
    job = TranscribeJob(path)
    for file in _analysis_files_for(path):
        saved = _load_saved(file)
        if saved and saved.get("fingerprint") == fingerprint:
            completed = int(saved.get("completed_chunks") or 0)
            total = int(math.ceil(float(saved.get("duration") or 0) / CHUNK_SECONDS))
            job.duration = float(saved.get("duration") or 0)
            job.completed_chunks = completed
            job.total_chunks = max(total, 0)
            job.language = saved.get("language") or "auto"
            job.model = saved.get("model") or WHISPER_DEFAULT_MODEL.name
            job.segments = list(saved.get("segments") or [])
            job.processed_duration = min(completed * CHUNK_SECONDS, job.duration)
            break
    return job


def _job_state(job: TranscribeJob, path: Path):
    with job._lock:
        live = job.running
        cancelled = job.cancelled
        failed = job.failed
        error = job.error
    elapsed = None
    if job.started_at:
        elapsed = round(time.monotonic() - job.started_at, 1)
    complete = (not live) and not cancelled and not failed and job.completed_chunks >= job.total_chunks and job.total_chunks > 0
    current = min(job.completed_chunks + 1, job.total_chunks) if (live and job.total_chunks) else job.completed_chunks
    percent = (job.processed_duration / job.duration * 100.0) if job.duration else (100.0 if complete else 0.0)
    return {
        "source_path": str(path),
        "running": live,
        "cancelled": cancelled,
        "failed": failed,
        "error": error,
        "duration": job.duration,
        "total_chunks": job.total_chunks,
        "completed_chunks": job.completed_chunks,
        "current_chunk": current,
        "processed_duration": job.processed_duration,
        "percent": round(min(max(percent, 0.0), 100.0), 1),
        "elapsed": elapsed,
        "language": job.language,
        "model": job.model,
        "backend": BACKEND,
        "threads": THREADS,
        "has_transcript": complete,
        "can_resume": (not live) and not failed and job.completed_chunks > 0 and job.total_chunks > 0 and job.completed_chunks < job.total_chunks,
        "segments": list(job.segments),
    }


@app.get("/api/local/models")
def local_models():
    names = []
    if WHISPER_MODELS.is_dir():
        names = sorted(p.name for p in WHISPER_MODELS.iterdir()
                     if p.suffix.lower() == ".bin" and p.is_file())
    if not names:
        names = [WHISPER_DEFAULT_MODEL.name]
    return {"models": names, "backend": BACKEND, "threads": THREADS,
            "default": WHISPER_DEFAULT_MODEL.name}


@app.get("/api/local/transcribe/status")
def transcribe_status(path: str):
    try:
        p = Path(path).resolve()
    except (OSError, ValueError):
        raise HTTPException(422, "Invalid path.")
    return _job_state(_get_job(p), p)


class TranscribeInput(BaseModel):
    path: str = Field(min_length=1, max_length=32767)
    language: str = "auto"
    model: str = ""
    retranscribe: bool = False


@app.post("/api/local/transcribe")
def start_transcribe(body: TranscribeInput):
    global _active_job
    try:
        path = Path(body.path).resolve()
        with selection_lock:
            allowed = str(path) in selected_paths
        if not allowed:
            raise HTTPException(403, "Select this file using Select Local Video first.")
        if path.suffix.lower() not in EXTENSIONS or not path.is_file():
            raise HTTPException(422, "Select an existing MP4, MKV, MOV, WebM, or AVI video.")
    except OSError:
        raise HTTPException(422, "The selected path cannot be read.")

    language = LANG_CODES.get(body.language, "auto")
    model = WHISPER_DEFAULT_MODEL
    if body.model:
        candidate = (WHISPER_MODELS / body.model).resolve()
        if not candidate.is_file() or candidate.parent != WHISPER_MODELS:
            raise HTTPException(422, "That Whisper model is not available locally.")
        model = candidate

    with _transcribe_lock:
        if _active_job is not None and _active_job.running:
            raise HTTPException(409, "A transcription is already running.")

    job = TranscribeJob(path)

    def _run():
        global _active_job, _last_job
        try:
            job.started_at = time.monotonic()
            job.running = True
            _run_worker(job, language, model, body.retranscribe)
        finally:
            with _transcribe_lock:
                _active_job = None
                # Keep the finished job available so the UI can read its final
                # state (success, failure, or cancel) after the worker ends.
                _last_job = job

    with _transcribe_lock:
        _active_job = job
    threading.Thread(target=_run, daemon=True).start()
    return {"started": True, "path": str(path)}


@app.post("/api/local/transcribe/cancel")
def cancel_transcribe(body: TranscribeInput):
    try:
        path = Path(body.path).resolve()
    except (OSError, ValueError):
        raise HTTPException(422, "Invalid path.")
    with _transcribe_lock:
        job = _active_job if _active_job is not None and _active_job.path == path and _active_job.running else None
    if job:
        job.cancel.set()
        return {"cancelling": True}
    return {"cancelling": False}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Host + Origin + a same-origin token protect the desktop picker from websites.
    host = request.headers.get("host", "")
    if host not in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}:
        return JSONResponse({"detail": "Only the localhost UI is allowed."}, status_code=403)
    if request.client and request.client.host not in {"127.0.0.1", "::1"}:
        return JSONResponse({"detail": "Only local connections are allowed."}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin not in ORIGINS:
        return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
    if request.method == "POST" and not secrets.compare_digest(
        request.headers.get("x-ai-clipper-token", ""), TOKEN
    ):
        return JSONResponse({"detail": "Reload the local page and try again."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' https:; connect-src 'self'; object-src 'none'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


def run_tool(args: list[str], timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError as exc:
        raise HTTPException(503, f"{Path(args[0]).stem} is not installed or is not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "Inspection timed out. The file or service may be unavailable.") from exc
    except OSError as exc:
        raise HTTPException(503, "Could not start the required system tool.") from exc
    if result.returncode:
        # Do not return raw tool output: it may contain signed URLs or internal details.
        tool = Path(args[0]).stem
        message = (
            "Cannot read this video. It may be damaged, unsupported, or no longer accessible."
            if tool == "ffprobe" else
            "Cannot inspect this URL. It may be private, removed, region-restricted, or require "
            "sign-in. Check the link and connection, or update yt-dlp. No video was downloaded."
        )
        raise HTTPException(422, message)
    return result.stdout


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def frame_rate(value):
    try:
        numerator, denominator = str(value).split("/")
        return number(float(numerator) / float(denominator))
    except (ValueError, ZeroDivisionError):
        return number(value)


def parse_json(output: str):
    try:
        result = json.loads(output)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, TypeError) as exc:
        raise HTTPException(502, "The metadata tool returned an invalid response.") from exc


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/session")
def session():
    return {"token": TOKEN}


@app.get("/api/health")
def health():
    tools = {"python": {"available": True, "version": sys.version.split()[0]}}
    for name, flag in (("ffmpeg", "-version"), ("ffprobe", "-version"), ("yt-dlp", "--version")):
        try:
            output = run_tool([shutil.which(name) or name, flag], timeout=10)
            version = output.splitlines()[0]
            tools[name.replace("-", "_")] = {"available": True, "version": version}
        except (HTTPException, IndexError):
            tools[name.replace("-", "_")] = {"available": False, "version": None}
    return {"status": "ok", "tools": tools}


@app.post("/api/local/select")
def select_local():
    if os.name != "nt":
        raise HTTPException(503, "Native file selection requires Windows.")
    if not picker_lock.acquire(blocking=False):
        raise HTTPException(409, "The file picker is already open. Check your Windows taskbar.")
    try:
        # Isolate the Windows modal dialog from API threads; no file content crosses HTTP.
        output = run_tool([sys.executable, str(ROOT / "picker.py")], timeout=300)
        result = parse_json(output)
        if result.get("error"):
            raise HTTPException(503, "Windows could not open the file picker. Run start.bat on your desktop.")
        path = result.get("path")
        if not path:
            return {"cancelled": True, "path": None}
        with selection_lock:
            selected_paths.add(str(Path(path).resolve()))
        return {"cancelled": False, "path": path}
    finally:
        picker_lock.release()


class LocalInput(BaseModel):
    path: str = Field(min_length=1, max_length=32767)


@app.post("/api/local/inspect")
def inspect_local(body: LocalInput):
    try:
        path = Path(body.path).resolve()
        with selection_lock:
            allowed = str(path) in selected_paths
        if not allowed:
            raise HTTPException(403, "Select this file using Select Local Video first.")
        if path.suffix.lower() not in EXTENSIONS or not path.is_file():
            raise HTTPException(422, "Select an existing MP4, MKV, MOV, WebM, or AVI video.")
        stat = path.stat()
        if not stat.st_size:
            raise HTTPException(422, "The selected file is empty.")
        data = parse_json(run_tool([
            shutil.which("ffprobe") or "ffprobe", "-v", "error",
            "-protocol_whitelist", "file", "-probesize", "20000000", "-analyzeduration", "10000000",
            "-show_format", "-show_streams", "-of", "json", str(path),
        ]))
        streams = data.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"
                      and not s.get("disposition", {}).get("attached_pic")), None)
        if not video:
            raise HTTPException(422, "This file does not contain a video stream.")
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        container = data.get("format") or {}
        duration = number(container.get("duration"))
        return {
            "kind": "local", "title": path.name, "filename": path.name,
            "path": str(path), "source": "Local file", "size_bytes": stat.st_size,
            "duration": duration if duration is not None else number(video.get("duration")),
            "width": video.get("width"), "height": video.get("height"),
            "fps": frame_rate(video.get("avg_frame_rate")) or frame_rate(video.get("r_frame_rate")),
            "video_codec": video.get("codec_name"),
            "audio_codec": (audio.get("codec_name") or "Unknown") if audio else None,
            "container": container.get("format_name"),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "thumbnail_url": None, "status": "Ready",
        }
    except (OSError, ValueError) as exc:
        raise HTTPException(422, "The selected path is invalid or cannot be read.") from exc


class URLInput(BaseModel):
    url: str = Field(min_length=1, max_length=4096)


def validate_url(raw: str):
    url = raw.strip()
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"https", "http"} or parts.username or parts.password or parts.port not in {None, 80, 443}:
            raise ValueError
        hosts = {
            "YouTube": {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"},
            "Twitch": {"twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv"},
            "TikTok": {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"},
        }
        platform = next((name for name, domains in hosts.items() if parts.hostname in domains), None)
        if not platform:
            raise HTTPException(422, "Unsupported URL. Use a YouTube, Twitch, or TikTok video link.")
        if not parts.path.strip("/") or parts.path.startswith(("/playlist", "/channel/", "/user/", "/feed/")):
            raise HTTPException(422, "Paste a single video, clip, or live-stream URL, not a playlist or channel page.")
        return url, platform
    except ValueError as exc:
        raise HTTPException(422, "Enter a valid http:// or https:// video URL.") from exc


@app.post("/api/url/inspect")
def inspect_url(body: URLInput):
    url, platform = validate_url(body.url)
    data = parse_json(run_tool([
        shutil.which("yt-dlp") or "yt-dlp", "--ignore-config", "--skip-download",
        "--dump-single-json", "--no-playlist", "--playlist-end", "1", "--no-progress",
        "--no-warnings", "--no-cache-dir", "--no-check-formats",
        "--socket-timeout", "15", "--retries", "0", "--extractor-retries", "0",
        "--use-extractors", "youtube.*,twitch.*,tiktok.*", "--", url,
    ], timeout=90))
    if data.get("_type") in {"playlist", "multi_video"} or "entries" in data:
        raise HTTPException(422, "Paste a single video or clip URL, not a playlist or channel page.")
    if not data.get("title"):
        raise HTTPException(422, "No video metadata was available for this URL.")
    formats = [f for f in data.get("formats", []) if f.get("vcodec") not in {None, "none"}]
    best = max(formats, key=lambda f: (number(f.get("height")) or 0, number(f.get("width")) or 0), default={})
    return {
        "kind": "url", "title": data["title"], "source": platform, "platform": platform,
        "source_url": url, "duration": number(data.get("duration")),
        "uploader": data.get("uploader") or data.get("channel") or data.get("creator"),
        "thumbnail_url": data.get("thumbnail"),
        "width": data.get("width") or best.get("width"),
        "height": data.get("height") or best.get("height"),
        "fps": number(data.get("fps") or best.get("fps")),
        "format_info": data.get("format") or best.get("format") or data.get("ext"),
        "size_bytes": None, "status": "Ready",
    }
