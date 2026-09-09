"""Phase 1: local, read-only media inspection. No media serving or downloading."""

import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
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


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
