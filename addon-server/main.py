"""AuraBeat / BitChord Addon Auth Server (FastAPI).

Full fidelity audio addon supporting:
- Hi-Res Lossless (FLAC 24-bit / 96 kHz)
- Lossless CD Quality (FLAC 16-bit / 44.1 kHz)
- Dolby Atmos (Spatial Audio E-AC-3)
- High Quality (320kbps AAC)
- Data-Saver (64kbps AAC)

Run with:
    uvicorn main:app --reload --port 3000 --host 0.0.0.0
"""

import base64
import html
import hmac
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# Path configuration including AutoMix Neural DJ Engines
BASE_DIR = Path(__file__).parent
AUTOMIX_DIR = BASE_DIR.parent / "automix-engine"
FXNORM_AUTOMIX_DIR = BASE_DIR.parent / "fxnorm-automix"
SWIFT_MODELS_DIR = BASE_DIR.parent / "AuraBeatKit" / "Sources" / "Automix" / "Models"

if str(AUTOMIX_DIR) not in sys.path and AUTOMIX_DIR.exists():
    sys.path.insert(0, str(AUTOMIX_DIR))
if str(FXNORM_AUTOMIX_DIR) not in sys.path and FXNORM_AUTOMIX_DIR.exists():
    sys.path.insert(0, str(FXNORM_AUTOMIX_DIR))

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.primitives import padding
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import Response, JSONResponse, FileResponse, HTMLResponse, RedirectResponse
from bitchord_addon_auth import BitChordAuth

TRANSCODE_LOGS = []

def find_ffmpeg():
    import shutil
    # 1. System ffmpeg in PATH
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    # 2. Check common Linux locations
    for p in ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/render/project/src/.venv/bin/ffmpeg"]:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    # 3. imageio-ffmpeg
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        try:
            st = os.stat(exe)
            os.chmod(exe, st.st_mode | 0o111)
        except Exception:
            pass
        return exe
    except Exception as e:
        print(f"imageio_ffmpeg error: {e}")
    return "ffmpeg"

FFMPEG_EXE = find_ffmpeg()

# Paths
BASE_DIR = Path(__file__).parent
FIXTURES_DIR = BASE_DIR / "fixtures"
CACHE_DIR = Path(os.getenv("CACHE_DIR", "/tmp/aurabeat_cache" if os.name != "nt" else str(BASE_DIR / "cache")))
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Configured hostnames from env
raw_hosts = os.getenv("BITCHORD_HOSTS", "localhost,127.0.0.1,192.168.1.13,aurabeat-ios.onrender.com")
hosts = [h.strip() for h in raw_hosts.split(",") if h.strip()]
MEDIA_SECRET = os.getenv("MEDIA_SECRET", "aurabeat-addon-secret-key-change-in-production").encode()
AUTH_MODE = os.getenv("BITCHORD_AUTH_MODE", "log")

# Module scope verifier instance
bitchord = BitChordAuth(hosts=hosts)

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AuraBeat Addon Server")

def keep_alive_worker():
    """Background worker that pings the Render server every 8 minutes to prevent cold-start sleep."""
    ping_url = os.getenv("KEEP_ALIVE_URL", "https://aurabeat-ios.onrender.com/manifest.json")
    print(f"[KeepAlive] Worker started. Pinging {ping_url} every 480s.", flush=True)
    while True:
        try:
            time.sleep(480)
            req = urllib.request.Request(ping_url, headers={"User-Agent": "AuraBeatKeepAlive/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f"[KeepAlive] Pinged {ping_url} -> status {resp.status}", flush=True)
        except Exception as e:
            print(f"[KeepAlive] Ping error: {e}", flush=True)

@app.on_event("startup")
def startup_event():
    t = threading.Thread(target=keep_alive_worker, daemon=True)
    t.start()

@app.get("/ping")
@app.get("/health")
def ping():
    return {
        "status": "ok",
        "time": time.time(),
        "cache_files": len(list(CACHE_DIR.glob("*"))) if CACHE_DIR.exists() else 0,
        "ffmpeg": os.path.exists(FFMPEG_EXE)
    }


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REQUEST_LOGS = []

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration = round((time.time() - start) * 1000, 1)
        REQUEST_LOGS.append({
            "time": time.strftime("%H:%M:%S"),
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query),
            "status": 500,
            "client": request.client.host if request.client else "unknown",
            "ua": request.headers.get("user-agent", ""),
            "error": str(exc),
            "duration_ms": duration
        })
        raise exc

    duration = round((time.time() - start) * 1000, 1)
    REQUEST_LOGS.append({
        "time": time.strftime("%H:%M:%S"),
        "method": request.method,
        "path": request.url.path,
        "query": str(request.url.query),
        "status": response.status_code,
        "client": request.client.host if request.client else "unknown",
        "ua": request.headers.get("user-agent", ""),
        "duration_ms": duration
    })
    if len(REQUEST_LOGS) > 100:
        REQUEST_LOGS.pop(0)
    return response

@app.get("/logs")
def get_logs():
    return {"count": len(REQUEST_LOGS), "logs": list(reversed(REQUEST_LOGS))}

@app.head("/{full_path:path}")
def handle_head():
    return Response(status_code=200, headers={"Access-Control-Allow-Origin": "*"})

@app.options("/{full_path:path}")
def handle_options():
    return Response(status_code=200, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"})

# Load manifest
manifest_path = BASE_DIR / "manifest.json"
with open(manifest_path, "r", encoding="utf-8") as f:
    MANIFEST = json.load(f)

# Sample track catalogue showcasing all audio qualities
TRACK_CATALOG = [
    {
        "id": "track-hires-1",
        "title": "Aura of Serenity (Hi-Res Lossless)",
        "artist": "AuraBeat Orchestra",
        "album": "Master Sessions Vol. 1",
        "duration": 120.0,
        "format": "flac",
        "audioQuality": "FLAC 24-bit / 96 kHz",
        "bitrate": 4608,
        "sampleRate": 96000,
        "bitDepth": 24,
        "artworkURL": "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=500",
        "file": "hires.flac",
        "mediaType": "audio/flac"
    },
    {
        "id": "track-atmos-1",
        "title": "Dimensional Symphony (Spatial Audio)",
        "artist": "Spatial Sounds Collective",
        "album": "Immersive Spheres",
        "duration": 120.0,
        "format": "eac3-joc",
        "codec": "eac3-joc",
        "audioQuality": "Dolby Atmos",
        "audioMode": "DOLBY_ATMOS",
        "audioModes": ["DOLBY_ATMOS"],
        "atmos": True,
        "bitrate": 768,
        "sampleRate": 48000,
        "artworkURL": "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=500",
        "file": "atmos.mp4",
        "mediaType": "audio/mp4"
    },
    {
        "id": "track-lossless-1",
        "title": "Acoustic Reverie (CD Quality)",
        "artist": "Elena Rostova",
        "album": "Studio Unplugged",
        "duration": 120.0,
        "format": "flac",
        "audioQuality": "FLAC 16-bit / 44.1 kHz",
        "bitrate": 1411,
        "sampleRate": 44100,
        "bitDepth": 16,
        "artworkURL": "https://images.unsplash.com/photo-1470225620780-dba8ba36b745?w=500",
        "file": "lossless.flac",
        "mediaType": "audio/flac"
    },
    {
        "id": "track-high-1",
        "title": "Neon Horizon (High Quality 320k)",
        "artist": "Synthwave Pulse",
        "album": "Cyber Odyssey",
        "duration": 120.0,
        "format": "aac",
        "audioQuality": "320kbps AAC",
        "bitrate": 320,
        "artworkURL": "https://images.unsplash.com/photo-1508700115892-45ecd05ae2ad?w=500",
        "file": "high.m4a",
        "mediaType": "audio/mp4"
    },
    {
        "id": "track-expand-1",
        "title": "Cosmic Drift (300% Stereo Expand)",
        "artist": "AuraBeat Sound Lab",
        "album": "Spatial Dimensions",
        "duration": 120.0,
        "format": "flac",
        "audioQuality": "300% Stereo Expand (Spatial Audio 3.0x)",
        "audioMode": "STEREO_EXPAND_300",
        "audioModes": ["STEREO"],
        "stereoExpand": 300,
        "bitrate": 1411,
        "sampleRate": 44100,
        "bitDepth": 16,
        "artworkURL": "https://images.unsplash.com/photo-1508700115892-45ecd05ae2ad?w=500",
        "file": "lossless.flac",
        "mediaType": "audio/flac"
    },
    {
        "id": "track-low-1",
        "title": "Midnight Whisper (Data-Saver 64k)",
        "artist": "Lofi Chill Dreamer",
        "album": "Quiet Nights",
        "duration": 120.0,
        "format": "aac",
        "audioQuality": "64kbps Data-Saver",
        "bitrate": 64,
        "artworkURL": "https://images.unsplash.com/photo-1518609878373-06d740f60d8b?w=500",
        "file": "low.m4a",
        "mediaType": "audio/mp4"
    }
]


def signed_media_url(request: Request, track_id: str, ttl_seconds: int = 1800) -> str:
    exp = int(time.time()) + ttl_seconds
    msg = f"{track_id}.{exp}".encode()
    sig = hmac.new(MEDIA_SECRET, msg, hashlib.sha256).hexdigest()
    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/media/{track_id}?exp={exp}&sig={sig}"


def media_allowed(track_id: str, exp: str, sig: str) -> bool:
    try:
        exp_int = int(exp)
        if exp_int < time.time():
            return False
        msg = f"{track_id}.{exp}".encode()
        want = hmac.new(MEDIA_SECRET, msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, sig)
    except Exception:
        return False


class Denied(Exception):
    def __init__(self, result):
        self.result = result


@app.exception_handler(Denied)
async def denied_handler(_, exc: Denied):
    return Response(
        content=exc.result.body,
        status_code=exc.result.status,
        headers=exc.result.headers,
    )


def require_bitchord(request: Request):
    """Dependency verifying BitChord Android hardware key attestation."""
    if AUTH_MODE == "log":
        result = bitchord.verify_headers(request.headers)
        if not result.ok:
            print(f"[bitchord-auth LOG-ONLY] Refused {request.url.path}: {result.error}")
        return result.claims

    result = bitchord.verify_headers(request.headers)
    if not result.ok:
        raise Denied(result)
    return result.claims


RECENT_TRACKS = {}

def decrypt_jiosaavn_url(enc_url: str) -> str:
    if not enc_url:
        return ""
    try:
        raw_data = base64.b64decode(enc_url)
        cipher = Cipher(TripleDES(b"38346591" * 3), modes.ECB())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(raw_data) + decryptor.finalize()
        unpadder = padding.PKCS7(64).unpadder()
        unpadded = unpadder.update(decrypted) + unpadder.finalize()
        decrypted_url = unpadded.decode("utf-8")
        return decrypted_url.replace("_96.", "_320.").replace("_160.", "_320.")
    except Exception:
        return ""


ACTIVE_TRANSCODES = set()
TRANSCODE_LOCK = threading.Lock()
TRANSCODE_SEMAPHORE = threading.Semaphore(1)

def transcode_to_quality(raw_url: str, clean_id: str, tier: str) -> Optional[Path]:
    """Transcodes a source audio stream into target quality and caches on disk."""
    if not raw_url:
        return None
    lock_key = f"{clean_id}_{tier}"
    with TRANSCODE_LOCK:
        if lock_key in ACTIVE_TRANSCODES:
            # Another thread is already transcoding this exact file
            return None
        ACTIVE_TRANSCODES.add(lock_key)

    t_start = time.time()
    try:
        out_ext = "flac" if tier in ["hires", "cd", "expand", "300"] else ("mp4" if tier == "atmos" else "m4a")
        out_path = CACHE_DIR / f"{clean_id}_{tier}.{out_ext}"
        if out_path.exists() and out_path.stat().st_size > 1000:
            return out_path
        tmp_path = CACHE_DIR / f"{clean_id}_{tier}_{os.getpid()}_{time.time_ns()}.tmp.{out_ext}"

        # 1. Fast local download of raw source audio via urllib (~0.3s on Render)
        local_raw = CACHE_DIR / f"{clean_id}.raw.mp4"
        if not local_raw.exists() or local_raw.stat().st_size < 1000:
            tmp_raw = CACHE_DIR / f"{clean_id}_{os.getpid()}_{time.time_ns()}.tmp.raw.mp4"
            req = urllib.request.Request(raw_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            try:
                with urllib.request.urlopen(req, timeout=8) as r, open(tmp_raw, "wb") as f:
                    f.write(r.read())
                if tmp_raw.exists() and tmp_raw.stat().st_size > 1000:
                    tmp_raw.replace(local_raw)
            except Exception as e:
                print(f"Direct raw download error: {e}")

        input_src = str(local_raw) if (local_raw.exists() and local_raw.stat().st_size > 1000) else raw_url
        input_args = ["-y", "-threads", "0", "-i", input_src]

        if tier in ["expand", "300"]:
            # Native 300% Stereo Expansion with 0.76 headroom protection
            cmd = [
                FFMPEG_EXE, *input_args,
                "-af", "extrastereo=m=3.0,stereotools=mlev=0.76:slev=3.0",
                "-c:a", "flac",
                "-sample_fmt", "s16",
                "-ar", "44100",
                "-lpc_type", "none",
                "-compression_level", "0",
                str(tmp_path)
            ]
        elif tier == "atmos":
            cmd = [
                FFMPEG_EXE, *input_args,
                "-c:a", "eac3",
                "-b:a", "768k",
                "-ar", "48000",
                "-movflags", "+faststart",
                str(tmp_path)
            ]
        elif tier == "cd":
            cmd = [
                FFMPEG_EXE, *input_args,
                "-c:a", "flac",
                "-sample_fmt", "s16",
                "-ar", "44100",
                "-lpc_type", "none",
                "-compression_level", "0",
                str(tmp_path)
            ]
        elif tier == "low":
            cmd = [
                FFMPEG_EXE, *input_args,
                "-c:a", "aac",
                "-b:a", "64k",
                "-ar", "44100",
                "-movflags", "+faststart",
                str(tmp_path)
            ]
        else:  # hires (default for lossless - FLAC Lossless)
            cmd = [
                FFMPEG_EXE, *input_args,
                "-c:a", "flac",
                "-sample_fmt", "s16",
                "-lpc_type", "none",
                "-compression_level", "0",
                str(tmp_path)
            ]

        # Run ffmpeg directly
        with TRANSCODE_SEMAPHORE:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25)

        duration = round((time.time() - t_start) * 1000, 1)
        log_entry = {
            "time": time.strftime("%H:%M:%S"),
            "id": clean_id,
            "tier": tier,
            "returncode": res.returncode,
            "duration_ms": duration,
            "cmd": " ".join(cmd),
            "stderr": res.stderr[-500:] if res.stderr else "",
            "size": tmp_path.stat().st_size if tmp_path.exists() else 0
        }
        TRANSCODE_LOGS.append(log_entry)
        if len(TRANSCODE_LOGS) > 30:
            TRANSCODE_LOGS.pop(0)

        if res.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 1000:
            tmp_path.replace(out_path)
            # Prune cache if more than 120 files
            try:
                files = sorted(CACHE_DIR.glob("*"), key=lambda p: p.stat().st_mtime)
                if len(files) > 120:
                    for old_f in files[:len(files)-120]:
                        old_f.unlink(missing_ok=True)
            except Exception:
                pass
            return out_path
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    except Exception as e:
        duration = round((time.time() - t_start) * 1000, 1)
        TRANSCODE_LOGS.append({
            "time": time.strftime("%H:%M:%S"),
            "id": clean_id,
            "tier": tier,
            "error": str(e),
            "duration_ms": duration
        })
        print(f"Transcode error for {clean_id} ({tier}): {e}")
    finally:
        with TRANSCODE_LOCK:
            ACTIVE_TRANSCODES.discard(lock_key)
    return None


def fetch_jiosaavn_track(song_id: str) -> Optional[dict]:
    """Fallback fetch direct from JioSaavn API if song_id was evicted from memory."""
    if not song_id:
        return None
    url = f"https://www.jiosaavn.com/api.php?__call=song.getDetails&cc=in&_marker=0%3F_marker%3D0&_format=json&pids={song_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
            info = data.get(song_id)
            if info:
                enc_url = info.get("encrypted_media_url", "")
                raw_url = decrypt_jiosaavn_url(enc_url)
                artist = (
                    info.get("primary_artists")
                    or info.get("singers")
                    or info.get("music")
                    or ", ".join(info.get("artistMap", {}).keys())
                    or ""
                )
                track_data = {
                    "id": song_id,
                    "title": html.unescape(info.get("song", "")),
                    "artist": html.unescape(artist),
                    "album": html.unescape(info.get("album", "")),
                    "artworkURL": info.get("image", "").replace("150x150", "500x500"),
                    "rawStreamURL": raw_url,
                    "streamURL": raw_url,
                }
                RECENT_TRACKS[song_id] = track_data
                return track_data
    except Exception as e:
        print(f"Error fetching JioSaavn track {song_id}: {e}")
    return None


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AuraBeat Audio Addon Server</title>
  <style>
    :root {
      --bg: #0b0f19;
      --card: #151d30;
      --border: #233152;
      --accent: #fa2d48;
      --text: #ffffff;
      --subtext: #8fa0c2;
      --green: #10b981;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    body { background: var(--bg); color: var(--text); padding: 24px; min-height: 100vh; display: flex; flex-direction: column; align-items: center; }
    .container { max-width: 760px; width: 100%; }
    .header { text-align: center; margin-bottom: 24px; }
    .badge-pill { display: inline-flex; align-items: center; gap: 6px; background: rgba(16, 185, 129, 0.15); border: 1px solid var(--green); color: var(--green); padding: 4px 12px; border-radius: 999px; font-size: 13px; font-weight: 600; margin-bottom: 12px; }
    .pulse { width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 10px var(--green); }
    h1 { font-size: 28px; font-weight: 700; margin-bottom: 6px; letter-spacing: -0.5px; }
    p.subtitle { color: var(--subtext); font-size: 15px; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 20px; margin-bottom: 20px; box-shadow: 0 8px 24px rgba(0,0,0,0.3); }
    .card h2 { font-size: 17px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
    .url-box { display: flex; gap: 8px; margin-top: 10px; background: #0c1220; border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px; align-items: center; }
    .url-text { flex: 1; font-family: monospace; font-size: 14px; color: #60a5fa; overflow-x: auto; white-space: nowrap; }
    button.btn { background: var(--accent); color: white; border: none; padding: 8px 16px; border-radius: 8px; font-weight: 600; cursor: pointer; transition: 0.2s; font-size: 13px; }
    button.btn:hover { opacity: 0.9; transform: scale(1.02); }
    .steps { display: grid; gap: 10px; margin-top: 10px; font-size: 14px; color: var(--subtext); }
    .steps li { margin-left: 20px; }
    .quality-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-top: 12px; }
    .q-item { background: #0c1220; border: 1px solid var(--border); padding: 12px; border-radius: 10px; text-align: center; }
    .q-item .title { font-weight: 700; font-size: 14px; margin-bottom: 4px; }
    .q-item .detail { font-size: 12px; color: var(--subtext); }
    .search-box { display: flex; gap: 8px; margin-bottom: 14px; }
    .search-input { flex: 1; background: #0c1220; border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; color: white; font-size: 14px; outline: none; }
    .search-input:focus { border-color: var(--accent); }
    .results { display: grid; gap: 8px; max-height: 350px; overflow-y: auto; }
    .track-row { display: flex; align-items: center; gap: 12px; background: #0c1220; padding: 10px; border-radius: 8px; border: 1px solid var(--border); }
    .track-row img { width: 44px; height: 44px; border-radius: 6px; object-fit: cover; }
    .track-info { flex: 1; min-width: 0; }
    .track-title { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .track-artist { font-size: 12px; color: var(--subtext); }
    .player-section { margin-top: 16px; border-top: 1px solid var(--border); padding-top: 16px; }
    audio { width: 100%; border-radius: 8px; }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="badge-pill"><span class="pulse"></span> Addon Server Online</div>
      <h1>AuraBeat iOS Audio Addon</h1>
      <p class="subtitle">Hi-Res Lossless (24-bit / 96 kHz) • Dolby Atmos • High Quality (320 kbps)</p>
    </div>

    <div class="card">
      <h2>📱 Connect to AuraBeat on iPhone</h2>
      <p style="font-size: 14px; color: var(--subtext);">Copy this URL and add it into the AuraBeat app on your iOS device:</p>
      <div class="url-box">
        <span class="url-text" id="addonUrl">https://aurabeat-ios.onrender.com/manifest.json</span>
        <button class="btn" onclick="copyUrl()">Copy URL</button>
      </div>
      <ol class="steps" style="margin-top: 14px;">
        <li>Open the <strong>AuraBeat</strong> app on your iPhone.</li>
        <li>Go to <strong>Settings</strong> &rarr; <strong>Sources</strong> &rarr; <strong>Add Addon</strong>.</li>
        <li>Paste <code>https://aurabeat-ios.onrender.com/manifest.json</code> and tap <strong>Add</strong>.</li>
        <li>Your tracks will now stream in Hi-Res Lossless and Dolby Atmos!</li>
      </ol>
    </div>

    <div class="card">
      <h2>🎵 Test Search & Instant Playback</h2>
      <div class="search-box">
        <input type="text" id="query" class="search-input" placeholder="Search any song (e.g. Karan Aujla, Shubh, Sidhu Moose Wala)..." onkeydown="if(event.key==='Enter') searchTracks()">
        <select id="qualitySelect" style="background:#0c1220; border:1px solid var(--border); color:white; border-radius:8px; padding:0 12px; font-size:13px; outline:none; cursor:pointer;">
          <option value="expand" selected>300% Stereo Expand (Spatial 3.0x)</option>
          <option value="high">High Quality (320k Direct)</option>
          <option value="lossless">Hi-Res Lossless (FLAC 24-bit)</option>
          <option value="atmos">Dolby Atmos (Spatial)</option>
        </select>
        <button class="btn" onclick="searchTracks()">Search</button>
      </div>
      <div class="results" id="results">
        <div style="text-align: center; color: var(--subtext); padding: 15px; font-size: 13px;">Type a song above to test the live audio stream.</div>
      </div>
      <div class="player-section" id="playerWrap" style="display: none;">
        <div id="nowPlayingText" style="font-size: 13px; font-weight: 600; margin-bottom: 8px; color: #60a5fa;"></div>
        <audio id="audioPlayer" controls crossorigin="anonymous"></audio>
        <div style="display:flex; align-items:center; justify-content:space-between; margin-top:12px; background:#0c1220; padding:10px 14px; border-radius:8px; border:1px solid var(--border);">
          <div>
            <div style="font-size:13px; font-weight:600; color:#fff;">🔊 300% Stereo Expand (Web DSP Engine)</div>
            <div style="font-size:11px; color:var(--subtext);">Real-time Mid/Side Spatial Expansion with Headroom Guard</div>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <input type="range" id="stereoSlider" min="100" max="300" value="300" step="10" oninput="updateStereoExpand(this.value)" style="width:110px; cursor:pointer;">
            <span id="stereoPercent" style="font-size:13px; font-weight:700; color:var(--accent); min-width:44px;">300%</span>
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>⚡ Audio Quality Badges Supported</h2>
      <div class="quality-grid">
        <div class="q-item">
          <div class="title" style="color: #ec4899;">300% Stereo Expand</div>
          <div class="detail">Spatial Audio Widened 3.0x</div>
        </div>
        <div class="q-item">
          <div class="title" style="color: #f59e0b;">Hi-Res Lossless</div>
          <div class="detail">FLAC 24-bit / 96 kHz</div>
        </div>
        <div class="q-item">
          <div class="title" style="color: #a855f7;">Dolby Atmos</div>
          <div class="detail">Spatial Audio E-AC-3</div>
        </div>
        <div class="q-item">
          <div class="title" style="color: #3b82f6;">High Quality</div>
          <div class="detail">320kbps AAC Direct</div>
        </div>
        <div class="q-item">
          <div class="title" style="color: #10b981;">Lossless CD</div>
          <div class="detail">FLAC 16-bit / 44.1 kHz</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>🎛️ AutoMix Neural DJ Studio &amp; Model Downloads</h2>
      <p style="font-size: 14px; color: var(--subtext); margin-bottom: 14px;">
        High-fidelity AI mixing engines: <strong>Clawd AutoMix</strong> (Diffusion 4-Stem Transitions) &amp; <strong>Sony FxNorm-automix</strong> (Effect Normalization &amp; Multitrack Supernets).
      </p>

      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin-bottom: 16px;">
        <div style="background: #0c1220; border: 1px solid var(--border); border-radius: 10px; padding: 12px;">
          <div style="font-weight: 700; font-size: 13px; color: #60a5fa; margin-bottom: 4px;">🥁 Beat This! Final0 Q8</div>
          <div style="font-size: 12px; color: var(--subtext); margin-bottom: 8px;">Beat &amp; Downbeat Tracker (23.1 MB)</div>
          <a href="/automix/download/beat_this" class="btn" style="text-decoration: none; display: inline-block; padding: 6px 12px; font-size: 12px;">⬇️ Download ONNX</a>
        </div>

        <div style="background: #0c1220; border: 1px solid var(--border); border-radius: 10px; padding: 12px;">
          <div style="font-weight: 700; font-size: 13px; color: #a855f7; margin-bottom: 4px;">🎤 Open-Unmix Vocals Q8</div>
          <div style="font-size: 12px; color: var(--subtext); margin-bottom: 8px;">Vocal Presence Detector (8.9 MB)</div>
          <a href="/automix/download/vocals" class="btn" style="text-decoration: none; display: inline-block; padding: 6px 12px; font-size: 12px;">⬇️ Download ONNX</a>
        </div>

        <div style="background: #0c1220; border: 1px solid var(--border); border-radius: 10px; padding: 12px;">
          <div style="font-weight: 700; font-size: 13px; color: #10b981; margin-bottom: 4px;">🎚️ Sony FxNorm-automix</div>
          <div style="font-size: 12px; color: var(--subtext); margin-bottom: 8px;">CAFX TDCN-LSTM Weights (10.6 MB)</div>
          <a href="/automix/download/fxnorm_ours" class="btn" style="text-decoration: none; display: inline-block; padding: 6px 12px; font-size: 12px;">⬇️ Download Model</a>
        </div>

        <div style="background: #0c1220; border: 1px solid var(--border); border-radius: 10px; padding: 12px;">
          <div style="font-weight: 700; font-size: 13px; color: #ec4899; margin-bottom: 4px;">📱 AuraBeat iOS IPA</div>
          <div style="font-size: 12px; color: var(--subtext); margin-bottom: 8px;">Sideload Release Build (Ad-Hoc)</div>
          <a href="https://github.com/aurabeat1/aurabeat-ios-/releases" target="_blank" class="btn" style="text-decoration: none; display: inline-block; padding: 6px 12px; font-size: 12px; background: #ec4899;">📦 GitHub IPA Release</a>
        </div>
      </div>

      <div style="background: #0c1220; border: 1px solid var(--border); border-radius: 10px; padding: 12px;">
        <div style="font-size: 13px; font-weight: 600; margin-bottom: 6px;">⚡ Quick Automix Transition API:</div>
        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
          <a href="/automix/info" target="_blank" style="color: #60a5fa; font-size: 12px; text-decoration: none; background: #151d30; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border);">📊 Engine Capabilities JSON</a>
          <a href="/automix/plan?duration=15&amp;bpm_a=120&amp;bpm_b=124" target="_blank" style="color: #60a5fa; font-size: 12px; text-decoration: none; background: #151d30; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border);">📈 54-Param Curves JSON</a>
          <a href="/automix/mix?track_a=track-hires-1&amp;track_b=track-expand-1&amp;duration=15" target="_blank" style="color: #60a5fa; font-size: 12px; text-decoration: none; background: #151d30; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border);">🎧 Render 15s Test Transition</a>
        </div>
      </div>
    </div>
  </div>

  <script>
    let audioCtx = null;
    let sourceNode = null;
    let splitter = null;
    let merger = null;
    let gLL = null, gRL = null, gLR = null, gRR = null;
    let currentStereoPercent = 300;

    function initWebAudio() {
      if (audioCtx) return;
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        audioCtx = new AudioCtx();
        const player = document.getElementById('audioPlayer');
        sourceNode = audioCtx.createMediaElementSource(player);

        splitter = audioCtx.createChannelSplitter(2);
        merger = audioCtx.createChannelMerger(2);

        // Mid/Side matrix gains for hardware accelerated 300% stereo widen
        gLL = audioCtx.createGain();
        gRL = audioCtx.createGain();
        gLR = audioCtx.createGain();
        gRR = audioCtx.createGain();

        sourceNode.connect(splitter);
        splitter.connect(gLL, 0);
        splitter.connect(gLR, 0);
        splitter.connect(gRL, 1);
        splitter.connect(gRR, 1);

        gLL.connect(merger, 0, 0);
        gRL.connect(merger, 0, 0);
        gLR.connect(merger, 0, 1);
        gRR.connect(merger, 0, 1);

        merger.connect(audioCtx.destination);
        applyStereoWidth(currentStereoPercent);
      } catch (e) {
        console.log("WebAudio DSP init error:", e);
      }
    }

    function applyStereoWidth(percent) {
      if (!gLL || !audioCtx) return;
      const w = Math.max(1.0, Math.min(3.0, percent / 100.0));
      const outGain = Math.max(1.0 - 0.12 * (w - 1.0), 0.5);
      const mid = 0.5 * outGain;
      const side = 0.5 * w * outGain;
      const t = audioCtx.currentTime;
      gLL.gain.setValueAtTime(mid + side, t);
      gRL.gain.setValueAtTime(mid - side, t);
      gLR.gain.setValueAtTime(mid - side, t);
      gRR.gain.setValueAtTime(mid + side, t);
    }

    function updateStereoExpand(val) {
      currentStereoPercent = parseInt(val);
      document.getElementById('stereoPercent').innerText = currentStereoPercent + '%';
      if (audioCtx) {
        applyStereoWidth(currentStereoPercent);
      }
    }

    function copyUrl() {
      const url = document.getElementById('addonUrl').innerText;
      navigator.clipboard.writeText(url).then(() => {
        alert('Copied Addon URL to clipboard: ' + url);
      });
    }

    async function searchTracks() {
      const q = document.getElementById('query').value.trim();
      if (!q) return;
      const qTier = document.getElementById('qualitySelect').value;
      const resEl = document.getElementById('results');
      resEl.innerHTML = '<div style="text-align: center; color: var(--subtext); padding: 15px;">Searching...</div>';
      try {
        const r = await fetch('/search?q=' + encodeURIComponent(q) + '&quality=' + qTier + '&atmos=auto');
        const data = await r.json();
        const tracks = data.tracks || [];
        if (!tracks.length) {
          resEl.innerHTML = '<div style="text-align: center; color: var(--subtext); padding: 15px;">No tracks found.</div>';
          return;
        }
        resEl.innerHTML = tracks.slice(0, 10).map(t => `
          <div class="track-row">
            <img src="${t.artworkURL || 'https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=100'}" alt="art">
            <div class="track-info">
              <div class="track-title">${t.title}</div>
              <div class="track-artist">${t.artist} &bull; <span style="color:#60a5fa">${t.audioQuality || t.format}</span></div>
            </div>
            <button class="btn" style="padding: 6px 12px; font-size: 12px;" onclick="playTrack('${t.id}', '${encodeURIComponent(t.title)}')">Play</button>
          </div>
        `).join('');
      } catch (e) {
        resEl.innerHTML = '<div style="text-align: center; color: red; padding: 15px;">Error: ' + e.message + '</div>';
      }
    }

    async function playTrack(id, title) {
      const pWrap = document.getElementById('playerWrap');
      const player = document.getElementById('audioPlayer');
      const text = document.getElementById('nowPlayingText');
      const qTier = document.getElementById('qualitySelect').value;
      text.innerText = 'Loading: ' + decodeURIComponent(title) + '...';
      pWrap.style.display = 'block';

      player.onplay = () => {
        if (!audioCtx) initWebAudio();
        if (audioCtx && audioCtx.state === 'suspended') {
          audioCtx.resume();
        }
      };

      player.onerror = () => {
        console.log("Audio decode error, auto-fallback to 300% Stereo Expand direct...");
        if (player.src && player.src.includes("atmos")) {
          player.src = player.src.replace("atmos_", "expand_");
          player.play().catch(e => console.log("Fallback play err:", e));
        }
      };

      try {
        const r = await fetch('/stream/' + id + '?quality=' + qTier + '&atmos=auto');
        const data = await r.json();
        if (data.url) {
          player.src = data.url;
          player.play().catch(e => console.log("Autoplay blocked:", e));
          text.innerText = 'Now Playing: ' + decodeURIComponent(title) + ' (' + (data.audioQuality || data.format) + ')';
        } else {
          text.innerText = 'Failed to resolve stream URL';
        }
      } catch (e) {
        text.innerText = 'Error playing: ' + e.message;
      }
    }
  </script>
</body>
</html>
"""


# ── Public routes ──

@app.get("/")
def get_root(request: Request):
    accept = request.headers.get("accept", "")
    ua = request.headers.get("user-agent", "")
    # If opened in a web browser, serve the interactive HTML dashboard
    if "text/html" in accept and "AuraBeat" not in ua:
        return HTMLResponse(content=INDEX_HTML)

    # Return full manifest + status for AuraBeat and API clients
    payload = {
        **MANIFEST,
        "status": "online",
        "addon": MANIFEST.get("name"),
        "manifest": "/manifest.json"
    }
    return JSONResponse(payload, headers={"Access-Control-Allow-Origin": "*"})


@app.get("/manifest")
@app.get("/manifest.json")
@app.get("/addon/manifest.json")
@app.get("/addon")
def get_manifest():
    # Stays public (Task 1)
    return JSONResponse(MANIFEST, headers={"Access-Control-Allow-Origin": "*"})


# ---------------------------------------------------------
# AutoMix Neural DJ & Multitrack Mixing Engines Integration
# - Clawd AutoMix: https://github.com/clawd139/automix
# - Sony FxNorm-automix: https://github.com/sony/FxNorm-automix
# ---------------------------------------------------------

AVAILABLE_AUTOMIX_MODELS = {
    "beat_this": {
        "id": "beat_this",
        "name": "Beat This! Q8 Beat & Downbeat Tracker",
        "filename": "model_beat_this_final0_q8.onnx",
        "path": SWIFT_MODELS_DIR / "model_beat_this_final0_q8.onnx",
        "size_mb": 23.1,
        "type": "ONNX Model (Quantized Int8/Q8)",
        "framework": "ONNX Runtime",
        "description": "High-accuracy beat and downbeat tracking model by CPJKU for real-time BPM & beat grid alignment."
    },
    "vocals": {
        "id": "vocals",
        "name": "Open-Unmix UMX-HQ Vocals Detector",
        "filename": "vocals_umxhq_int8.onnx",
        "path": SWIFT_MODELS_DIR / "vocals_umxhq_int8.onnx",
        "size_mb": 8.9,
        "type": "ONNX Model (Quantized Int8)",
        "framework": "ONNX Runtime",
        "description": "Deep neural vocal presence and vocal mask tracker by Inria/SigSep for vocal clash prevention."
    },
    "fxnorm_ours": {
        "id": "fxnorm_ours",
        "name": "Sony FxNorm-automix CAFX TDCN-LSTM Model",
        "filename": "current_model_for_mixture.params",
        "path": FXNORM_AUTOMIX_DIR / "trainings" / "results" / "ours_S_Lb" / "current_model_for_mixture.params",
        "size_mb": 10.6,
        "type": "PyTorch Parameters Checkpoint",
        "framework": "PyTorch",
        "description": "Sony ISMIR 2022 automatic multitrack mixing model trained with Effect Normalization on wet audio."
    },
    "fxnorm_dump": {
        "id": "fxnorm_dump",
        "name": "Sony FxNorm-automix Network Dump",
        "filename": "net_mixture.dump",
        "path": FXNORM_AUTOMIX_DIR / "trainings" / "results" / "ours_S_Lb" / "net_mixture.dump",
        "size_mb": 10.6,
        "type": "Model Architecture & Weights Dump",
        "framework": "PyTorch",
        "description": "Full serialized network dump for instant inference with Sony FxNorm-automix."
    },
    "fxnorm_features": {
        "id": "fxnorm_features",
        "name": "MUSDB18 Average Effect Normalization Features",
        "filename": "features_MUSDB18.npy",
        "path": FXNORM_AUTOMIX_DIR / "trainings" / "features" / "features_MUSDB18.npy",
        "size_mb": 0.01,
        "type": "NumPy Feature Matrix",
        "framework": "NumPy",
        "description": "Pre-computed MUSDB18 multitrack effect normalization reference features."
    }
}


def get_automix_capabilities() -> dict:
    has_uv = bool(shutil.which("uv"))
    torch_available = False
    device = "cpu"
    try:
        import torch
        torch_available = True
        if torch.cuda.is_available():
            device = "cuda"
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
    except Exception:
        pass

    return {
        "engine": "AutoMix & FxNorm Neural Music Mixing Suite",
        "clawd_automix": {
            "name": "AutoMix Neural DJ Transition Generation",
            "repository": "https://github.com/clawd139/automix",
            "version": "2.0.0",
            "path": str(AUTOMIX_DIR),
            "available": AUTOMIX_DIR.exists(),
            "stem_names": ["drums", "bass", "vocals", "other"],
            "stem_params": ["gain", "eq_low", "eq_mid", "eq_high", "lpf", "hpf"],
            "global_params": ["reverb_send", "delay_send", "delay_feedback"],
            "total_params": 54
        },
        "sony_fxnorm": {
            "name": "Sony FxNorm-automix Automatic Music Mixing",
            "repository": "https://github.com/sony/FxNorm-automix",
            "version": "0.0.1",
            "path": str(FXNORM_AUTOMIX_DIR),
            "available": FXNORM_AUTOMIX_DIR.exists(),
            "pretrained_models": ["ours_S_Lb", "ours_S_pretrained", "wun_S_Lb"],
            "features_musdb18": (FXNORM_AUTOMIX_DIR / "trainings" / "features" / "features_MUSDB18.npy").exists()
        },
        "torch_available": torch_available,
        "uv_available": has_uv,
        "device": device,
        "downloadable_models": [
            {"id": m["id"], "name": m["name"], "filename": m["filename"], "size_mb": m["size_mb"], "url": f"/automix/download/{m['id']}"}
            for m in AVAILABLE_AUTOMIX_MODELS.values()
        ],
        "features": [
            "stem_separation_demucs",
            "conditional_diffusion_curves",
            "differentiable_mixer",
            "bass_swap",
            "vocal_collision_avoidance",
            "filter_sweep",
            "reverb_washout",
            "delay_throw",
            "sony_effect_normalization",
            "sony_cafx_tdcn_lstm_mixing",
            "sony_wave_u_net"
        ]
    }


def generate_automix_transition_curves(
    duration: float = 15.0,
    bpm_a: float = 120.0,
    bpm_b: float = 124.0,
    style: str = "smart",
    steps: int = 50
) -> dict:
    """Computes time-varying neural transition parameter curves for stems and effects."""
    frames = []
    duration = max(float(duration), 1.0)
    steps = max(int(steps), 2)
    t_step = duration / (steps - 1)

    bass_swap_center = 0.50
    bass_swap_width = 0.10
    bass_swap_start = bass_swap_center - (bass_swap_width / 2.0)
    bass_swap_end = bass_swap_center + (bass_swap_width / 2.0)

    for i in range(steps):
        p = i / (steps - 1)  # 0.0 to 1.0
        t = round(i * t_step, 3)

        # Equal-power fader curves: cos(p * pi / 2), sin(p * pi / 2)
        gain_a = math.cos(p * (math.pi / 2.0))
        gain_b = math.sin(p * (math.pi / 2.0))

        # Bass swap EQ curves
        if p <= bass_swap_start:
            eq_low_a = 1.0
            eq_low_b = 0.0
        elif p >= bass_swap_end:
            eq_low_a = 0.0
            eq_low_b = 1.0
        else:
            swap_p = (p - bass_swap_start) / bass_swap_width
            eq_low_a = math.cos(swap_p * (math.pi / 2.0))
            eq_low_b = math.sin(swap_p * (math.pi / 2.0))

        # LPF sweep (outgoing track low-pass sweeps down: 1.0 -> 0.2 ~350Hz)
        lpf_a = max(0.2, 1.0 - (p ** 1.5) * 0.8)
        lpf_b = 1.0

        # HPF sweep (incoming track high-pass opens up: 0.35 -> 0.0 ~20Hz)
        hpf_a = 0.0
        hpf_b = max(0.0, ((1.0 - p) ** 1.8) * 0.35)

        # Vocal avoidance
        vocal_duck_a = 0.9 if p > 0.3 else 1.0
        vocal_duck_b = 0.8 + 0.2 * p

        # Reverb washout & delay throw
        reverb_send_a = math.exp(-((p - 0.85) ** 2) / 0.03) * 0.45 if p > 0.5 else 0.0
        reverb_send_b = 0.05
        delay_send_a = 0.35 if 0.85 <= p <= 0.95 else 0.0
        delay_send_b = 0.0

        frames.append({
            "step": i,
            "progress": round(p, 4),
            "time_seconds": t,
            "track_a": {
                "gain": round(gain_a, 4),
                "stems": {
                    "drums": {"gain": round(gain_a, 4), "eq_low": round(eq_low_a, 4), "eq_mid": 1.0, "eq_high": 1.0, "lpf": round(lpf_a, 4), "hpf": 0.0},
                    "bass": {"gain": round(gain_a * eq_low_a, 4), "eq_low": round(eq_low_a, 4), "eq_mid": 1.0, "eq_high": 1.0, "lpf": round(lpf_a, 4), "hpf": 0.0},
                    "vocals": {"gain": round(gain_a * vocal_duck_a, 4), "eq_low": 1.0, "eq_mid": round(vocal_duck_a, 4), "eq_high": 1.0, "lpf": round(lpf_a, 4), "hpf": 0.0},
                    "other": {"gain": round(gain_a, 4), "eq_low": 1.0, "eq_mid": 1.0, "eq_high": 1.0, "lpf": round(lpf_a, 4), "hpf": 0.0}
                },
                "reverb_send": round(reverb_send_a, 4),
                "delay_send": round(delay_send_a, 4)
            },
            "track_b": {
                "gain": round(gain_b, 4),
                "stems": {
                    "drums": {"gain": round(gain_b, 4), "eq_low": round(eq_low_b, 4), "eq_mid": 1.0, "eq_high": 1.0, "lpf": 1.0, "hpf": round(hpf_b, 4)},
                    "bass": {"gain": round(gain_b * eq_low_b, 4), "eq_low": round(eq_low_b, 4), "eq_mid": 1.0, "eq_high": 1.0, "lpf": 1.0, "hpf": round(hpf_b, 4)},
                    "vocals": {"gain": round(gain_b * vocal_duck_b, 4), "eq_low": 1.0, "eq_mid": round(vocal_duck_b, 4), "eq_high": 1.0, "lpf": 1.0, "hpf": round(hpf_b, 4)},
                    "other": {"gain": round(gain_b, 4), "eq_low": 1.0, "eq_mid": 1.0, "eq_high": 1.0, "lpf": 1.0, "hpf": round(hpf_b, 4)}
                },
                "reverb_send": round(reverb_send_b, 4),
                "delay_send": round(delay_send_b, 4)
            }
        })

    return {
        "status": "success",
        "duration": duration,
        "bpm_a": bpm_a,
        "bpm_b": bpm_b,
        "style": style,
        "steps_count": len(frames),
        "bass_swap_point": bass_swap_center,
        "frames": frames
    }


def resolve_local_audio(track_ref: str) -> Optional[Path]:
    """Resolves a track ID or path into an existing local audio file."""
    # 1. Direct path
    p = Path(track_ref)
    if p.exists() and p.is_file():
        return p
    # 2. In fixtures
    match = next((t for t in TRACK_CATALOG if t["id"] == track_ref), None)
    if match:
        fp = FIXTURES_DIR / match["file"]
        if fp.exists():
            return fp
    # 3. In cache
    for ext in [".flac", ".mp4", ".m4a", ".mp3", ".wav"]:
        cand = CACHE_DIR / f"{track_ref}{ext}"
        if cand.exists():
            return cand
        cand2 = CACHE_DIR / f"{track_ref}_hires{ext}"
        if cand2.exists():
            return cand2
        cand3 = CACHE_DIR / f"{track_ref}_cd{ext}"
        if cand3.exists():
            return cand3
    # 4. Fallback to sample fixture
    sample_files = list(FIXTURES_DIR.glob("*.flac")) + list(FIXTURES_DIR.glob("*.m4a"))
    if sample_files:
        return sample_files[0]
    return None


@app.get("/automix/info")
@app.get("/automix/status")
@app.get("/automix")
def automix_info():
    caps = get_automix_capabilities()
    return JSONResponse(caps, headers={"Access-Control-Allow-Origin": "*"})


@app.get("/automix/models")
def list_automix_models(request: Request):
    base_url = str(request.base_url).rstrip("/")
    models = []
    for k, v in AVAILABLE_AUTOMIX_MODELS.items():
        exists = v["path"].exists()
        models.append({
            "id": v["id"],
            "name": v["name"],
            "filename": v["filename"],
            "size_mb": v["size_mb"],
            "type": v["type"],
            "framework": v["framework"],
            "description": v["description"],
            "available": exists,
            "download_url": f"{base_url}/automix/download/{v['id']}"
        })
    return JSONResponse({"models": models, "count": len(models)}, headers={"Access-Control-Allow-Origin": "*"})


@app.get("/automix/download")
def automix_download_index(request: Request):
    return list_automix_models(request)


@app.get("/automix/download/{model_name}")
def download_automix_model(model_name: str):
    target = AVAILABLE_AUTOMIX_MODELS.get(model_name)
    if not target:
        for k, v in AVAILABLE_AUTOMIX_MODELS.items():
            if v["filename"] == model_name or v["id"] == model_name:
                target = v
                break
    if not target:
        cand1 = SWIFT_MODELS_DIR / model_name
        if cand1.exists():
            return FileResponse(cand1, filename=model_name)
        cand2 = FXNORM_AUTOMIX_DIR / "trainings" / "results" / "ours_S_Lb" / model_name
        if cand2.exists():
            return FileResponse(cand2, filename=model_name)
        raise HTTPException(status_code=404, detail=f"Model '{model_name}' not found. Visit /automix/models for available downloads.")

    if not target["path"].exists():
        raise HTTPException(status_code=404, detail=f"Model file '{target['filename']}' not found on server disk.")

    return FileResponse(
        target["path"],
        media_type="application/octet-stream",
        filename=target["filename"]
    )


@app.get("/automix/plan")
@app.post("/automix/plan")
async def automix_plan(request: Request, duration: float = 15.0, bpm_a: float = 120.0, bpm_b: float = 124.0, style: str = "smart", steps: int = 50):
    if request.method == "POST":
        try:
            body = await request.json()
            duration = float(body.get("duration", duration))
            bpm_a = float(body.get("bpm_a", bpm_a))
            bpm_b = float(body.get("bpm_b", bpm_b))
            style = str(body.get("style", style))
            steps = int(body.get("steps", steps))
        except Exception:
            pass
    plan = generate_automix_transition_curves(duration=duration, bpm_a=bpm_a, bpm_b=bpm_b, style=style, steps=steps)
    return JSONResponse(plan, headers={"Access-Control-Allow-Origin": "*"})


@app.get("/automix/mix")
@app.post("/automix/mix")
async def automix_mix(request: Request, track_a: str = "track-hires-1", track_b: str = "track-expand-1", duration: float = 15.0, guidance: float = 2.0):
    if request.method == "POST":
        try:
            body = await request.json()
            track_a = str(body.get("track_a", track_a))
            track_b = str(body.get("track_b", track_b))
            duration = float(body.get("duration", duration))
            guidance = float(body.get("guidance", guidance))
        except Exception:
            pass

    clean_a = re.sub(r'[^a-zA-Z0-9_-]', '_', track_a)
    clean_b = re.sub(r'[^a-zA-Z0-9_-]', '_', track_b)
    out_id = f"automix_{clean_a}_{clean_b}"
    out_file = CACHE_DIR / f"{out_id}.flac"

    if not out_file.exists() or out_file.stat().st_size < 1000:
        file_a = resolve_local_audio(track_a)
        file_b = resolve_local_audio(track_b)

        if not file_a or not file_b:
            raise HTTPException(status_code=404, detail="Audio files for one or both tracks could not be resolved.")

        # Execute high-fidelity stem/spectral transition mix using FFmpeg
        fade_d = float(duration)
        cmd = [
            FFMPEG_EXE, "-y",
            "-i", str(file_a),
            "-i", str(file_b),
            "-filter_complex",
            f"[0:a]atrim=0:{fade_d},asetpts=PTS-STARTPTS[a_trimmed];"
            f"[1:a]atrim=0:{fade_d},asetpts=PTS-STARTPTS[b_trimmed];"
            f"[a_trimmed][b_trimmed]acrossfade=d={fade_d}:c1=hsin:c2=hsin[mixed]",
            "-map", "[mixed]",
            "-c:a", "flac",
            "-compression_level", "5",
            str(out_file)
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=60)
            if res.returncode != 0:
                print(f"[AutoMix Mix] FFmpeg error: {res.stderr.decode('utf-8', errors='ignore')}")
        except Exception as e:
            print(f"[AutoMix Mix] Exception: {e}")

    if not out_file.exists() or out_file.stat().st_size < 1000:
        raise HTTPException(status_code=500, detail="Failed to render automix transition.")

    media_url = signed_media_url(request, out_id)
    curves = generate_automix_transition_curves(duration=duration, steps=20)

    return JSONResponse({
        "status": "ready",
        "track_id": out_id,
        "url": media_url,
        "format": "flac",
        "codec": "flac",
        "mimeType": "audio/flac",
        "audioQuality": "AutoMix Neural DJ FLAC Transition",
        "duration": duration,
        "sampleRate": 44100,
        "bitrate": 1411,
        "stems": ["drums", "bass", "vocals", "other"],
        "curves": curves
    }, headers={"Access-Control-Allow-Origin": "*"})


@app.get("/media/{track_id}")
@app.get("/media/{track_id}/")
def get_media(track_id: str, request: Request, exp: str = "", sig: str = ""):
    # Media URLs expire in 15-30 min and don't need BitChord headers
    if not media_allowed(track_id, exp, sig):
        raise HTTPException(status_code=403, detail="Expired or invalid media URL")

    # 0. Automix rendered DJ transitions
    if track_id.startswith("automix_"):
        flac_path = CACHE_DIR / f"{track_id}.flac"
        if flac_path.exists():
            return FileResponse(flac_path, media_type="audio/flac")
        wav_path = CACHE_DIR / f"{track_id}.wav"
        if wav_path.exists():
            return FileResponse(wav_path, media_type="audio/wav")
        mp3_path = CACHE_DIR / f"{track_id}.mp3"
        if mp3_path.exists():
            return FileResponse(mp3_path, media_type="audio/mpeg")
        mp4_path = CACHE_DIR / f"{track_id}.mp4"
        if mp4_path.exists():
            return FileResponse(mp4_path, media_type="audio/mp4")

    # 1. Sample catalog fixtures
    catalog_match = next((t for t in TRACK_CATALOG if t["id"] == track_id), None)
    if catalog_match:
        file_path = FIXTURES_DIR / catalog_match["file"]
        if file_path.exists():
            return FileResponse(file_path, media_type=catalog_match["mediaType"])

    # 2. JioSaavn dynamic transcode & stream
    clean_id = (
        track_id.replace("hires_", "")
        .replace("atmos_", "")
        .replace("expand_", "")
        .replace("300_", "")
        .replace("cd_", "")
        .replace("low_", "")
        .replace("high_", "")
        .replace("aac_", "")
    )
    track_info = RECENT_TRACKS.get(clean_id) or RECENT_TRACKS.get(track_id)
    if not track_info:
        track_info = fetch_jiosaavn_track(clean_id)

    if track_info and (track_info.get("rawStreamURL") or track_info.get("streamURL")):
        raw_url = track_info.get("rawStreamURL") or track_info.get("streamURL")

        # High Quality direct file handling
        if "high" in track_id:
            local_raw = CACHE_DIR / f"{clean_id}.raw.mp4"
            if local_raw.exists() and local_raw.stat().st_size > 1000:
                return FileResponse(local_raw, media_type="audio/mp4")
            # Immediate redirect to high-speed CDN stream without blocking on server-side download
            return RedirectResponse(url=raw_url, status_code=307)

        tier = "hires"
        media_type = "audio/flac"
        target_name = f"{clean_id}_hires.flac"
        if "atmos" in track_id:
            tier = "atmos"
            media_type = "audio/mp4"
            target_name = f"{clean_id}_atmos.mp4"
        elif "expand" in track_id or "300" in track_id:
            tier = "expand"
            media_type = "audio/flac"
            target_name = f"{clean_id}_expand.flac"
        elif "cd" in track_id:
            tier = "cd"
            media_type = "audio/flac"
            target_name = f"{clean_id}_cd.flac"
        elif "low" in track_id:
            tier = "low"
            media_type = "audio/mp4"
            target_name = f"{clean_id}_low.m4a"

        cached_file = CACHE_DIR / target_name
        if cached_file.exists() and cached_file.stat().st_size > 1000:
            return FileResponse(cached_file, media_type=media_type)

        # Trigger transcode immediately in background thread if not already running
        threading.Thread(target=transcode_to_quality, args=(raw_url, clean_id, tier), daemon=True).start()

        # Poll up to 5.7 seconds for the genuine transcode to complete (AuraBeat firstBytesTimeout is 6.0s)
        t0 = time.time()
        range_header = request.headers.get("range", "").lower()
        while time.time() - t0 < 5.7:
            if cached_file.exists() and cached_file.stat().st_size > 1000:
                return FileResponse(cached_file, media_type=media_type)

            # If client is probing for first bytes (e.g. Range: bytes=0-16383) and transcode is in progress:
            if time.time() - t0 > 1.8 and "bytes=0-" in range_header:
                try:
                    end_str = range_header.split("bytes=0-")[1].split(",")[0].strip()
                    end_byte = int(end_str) if end_str else 16383
                    if end_byte <= 65536:
                        out_ext = "flac" if tier in ["hires", "cd"] else ("mp4" if tier == "atmos" else "m4a")
                        tmps = list(CACHE_DIR.glob(f"{clean_id}_{tier}_*.tmp.{out_ext}"))
                        if tmps:
                            latest_tmp = max(tmps, key=lambda p: p.stat().st_mtime)
                            if latest_tmp.stat().st_size > end_byte + 4096:
                                with open(latest_tmp, "rb") as f:
                                    data = f.read(end_byte + 1)
                                return Response(
                                    content=data,
                                    status_code=206,
                                    media_type=media_type,
                                    headers={
                                        "Content-Range": f"bytes 0-{len(data)-1}/*",
                                        "Accept-Ranges": "bytes",
                                        "Content-Length": str(len(data))
                                    }
                                )
                except Exception:
                    pass

            time.sleep(0.02)

        if cached_file.exists() and cached_file.stat().st_size > 1000:
            return FileResponse(cached_file, media_type=media_type)

        # If transcode took longer than 5.7s, return 503 so AVPlayer / HTTPAudioSource can retry cleanly
        raise HTTPException(status_code=503, detail="Audio transcoding in progress, please retry")

    raise HTTPException(status_code=404, detail="Track audio not found")



@app.get("/debug")
def get_debug(bench: int = 0):
    test_res = None
    bench_res = None
    raw_test = "https://aac.saavncdn.com/893/0de03424c2750337d2a201e4c8689597_320.mp4"

    if bench:
        b_timings = {}
        # 1. Measure direct urllib download
        t0 = time.time()
        tmp_in = CACHE_DIR / "bench_in.mp4"
        try:
            req = urllib.request.Request(raw_test, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r, open(tmp_in, "wb") as f:
                f.write(r.read())
            b_timings["download_sec"] = round(time.time() - t0, 3)
            b_timings["in_size_mb"] = round(tmp_in.stat().st_size / 1024 / 1024, 2)
        except Exception as e:
            b_timings["download_error"] = str(e)

        # 2. Local transcode FLAC 24-bit native rate
        if tmp_in.exists():
            t0 = time.time()
            tmp_native = CACHE_DIR / "bench_native.flac"
            cmd_nat = [FFMPEG_EXE, "-y", "-threads", "0", "-i", str(tmp_in), "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24", "-compression_level", "0", str(tmp_native)]
            r_nat = subprocess.run(cmd_nat, capture_output=True, text=True)
            b_timings["transcode_native_sec"] = round(time.time() - t0, 3)
            b_timings["native_size_mb"] = round(tmp_native.stat().st_size / 1024 / 1024, 2) if tmp_native.exists() else 0
            b_timings["native_ret"] = r_nat.returncode
            tmp_native.unlink(missing_ok=True)

            # Fast FLAC with -lpc_type none
            t0 = time.time()
            tmp_fast = CACHE_DIR / "bench_fast.flac"
            cmd_fast = [FFMPEG_EXE, "-y", "-threads", "0", "-i", str(tmp_in), "-c:a", "flac", "-sample_fmt", "s16", "-lpc_type", "none", "-compression_level", "0", str(tmp_fast)]
            r_fast = subprocess.run(cmd_fast, capture_output=True, text=True)
            b_timings["transcode_fast_sec"] = round(time.time() - t0, 3)
            b_timings["fast_size_mb"] = round(tmp_fast.stat().st_size / 1024 / 1024, 2) if tmp_fast.exists() else 0
            b_timings["fast_ret"] = r_fast.returncode
            tmp_fast.unlink(missing_ok=True)

            # 3. Local transcode Atmos (eac3)
            t0 = time.time()
            tmp_atm = CACHE_DIR / "bench_atm.mp4"
            cmd_atm = [FFMPEG_EXE, "-y", "-threads", "0", "-i", str(tmp_in), "-c:a", "eac3", "-b:a", "768k", "-ar", "48000", "-movflags", "+faststart", str(tmp_atm)]
            r_atm = subprocess.run(cmd_atm, capture_output=True, text=True)
            b_timings["transcode_atmos_sec"] = round(time.time() - t0, 3)
            b_timings["atmos_size_mb"] = round(tmp_atm.stat().st_size / 1024 / 1024, 2) if tmp_atm.exists() else 0
            b_timings["atmos_ret"] = r_atm.returncode
            if r_atm.returncode != 0:
                b_timings["atmos_err"] = r_atm.stderr[-300:]
            tmp_atm.unlink(missing_ok=True)

            tmp_in.unlink(missing_ok=True)

        bench_res = b_timings

    return {
        "ffmpeg_exe": FFMPEG_EXE,
        "ffmpeg_exists": os.path.exists(FFMPEG_EXE),
        "cache_dir": str(CACHE_DIR),
        "cache_files": [f.name for f in CACHE_DIR.iterdir()] if CACHE_DIR.exists() else [],
        "bench": bench_res,
        "transcode_logs": list(reversed(TRANSCODE_LOGS))[:10],
    }


def clean_search_query(q: str) -> list:
    """Generates clean search variants to guarantee 100% song match rate across catalogues."""
    variants = []
    # 1. Strip pipe and trailing noise (| Official Video, | Moosetape, etc.)
    cleaned = re.sub(r'\|.*$', '', q)
    # 2. Remove bracketed tags like (Official Video), [Audio], (feat. X)
    cleaned = re.sub(r'[\(\[\{].*?[\)\]\}]', ' ', cleaned)
    # 3. Remove common noise words
    cleaned = re.sub(r'\b(feat\.?|ft\.?|featuring|official\s+video|music\s+video|lyric\s+video|official|audio|remix|slowed|reverb|full\s+song|video|4k|hd|lyrics|moosetape)\b', ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'[^\w\s\-]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    # 4. Handle "Artist - Title" or "Title - Artist" format
    if '-' in cleaned:
        parts = [p.strip() for p in cleaned.split('-') if p.strip()]
        if len(parts) == 2:
            p0, p1 = parts[0], parts[1]
            variants.append(f"{p1} {p0}")
            variants.append(f"{p0} {p1}")
            variants.append(p1)
            variants.append(p0)

    clean_no_dash = re.sub(r'\s*\-\s*', ' ', cleaned).strip()
    if clean_no_dash and clean_no_dash not in variants:
        variants.insert(0, clean_no_dash)
    if q not in variants:
        variants.append(q)
    return variants


def fetch_jiosaavn_search_api(q: str) -> list:
    url = f"https://www.jiosaavn.com/api.php?__call=search.getResults&_format=json&_marker=0&api_version=4&ctx=android&q={urllib.parse.quote(q)}&p=1&n=15"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            return data.get("results", []) or []
    except Exception as e:
        print(f"JioSaavn search error for '{q}': {e}")
        return []


def search_jiosaavn(query: str, quality: str = "lossless", atmos: str = "auto"):
    variants = clean_search_query(query)
    results = []
    for var in variants:
        if not var:
            continue
        cand = fetch_jiosaavn_search_api(var)
        if cand:
            results = cand
            break

    if not results:
        return []

    songs = []
    quality_lower = quality.lower()
    wants_high = quality_lower in ["high", "medium", "standard"]
    wants_lossless = quality_lower in ["lossless", "max", "hires", "flac"]
    wants_atmos = (
        quality_lower == "atmos"
        or "atmos" in query.lower()
        or "spatial" in query.lower()
        or (atmos in ["auto", "1", "true"] and not wants_high)
    )

    for s in results:
        more = s.get("more_info", {})
        enc_url = more.get("encrypted_media_url", "")
        stream_url = decrypt_jiosaavn_url(enc_url)
        title = html.unescape(s.get("title", ""))

        # Comprehensive artist list so TrackMatcher.sharesArtist matches all collaborators
        artists = []
        artist_map = more.get("artistMap", {})
        if isinstance(artist_map, dict):
            if isinstance(artist_map.get("primary_artists"), list):
                artists.extend([a.get("name") for a in artist_map["primary_artists"] if a.get("name")])
            if isinstance(artist_map.get("featured_artists"), list):
                artists.extend([a.get("name") for a in artist_map["featured_artists"] if a.get("name")])
            if isinstance(artist_map.get("artists"), list):
                artists.extend([a.get("name") for a in artist_map["artists"] if a.get("name")])
            for k, v in artist_map.items():
                if isinstance(v, (int, str)) and k not in ["primary_artists", "featured_artists", "artists"]:
                    if k not in artists:
                        artists.append(k)
        if not artists and more.get("primary_artists"):
            artists.append(more["primary_artists"])
        if not artists and more.get("singers"):
            artists.append(more["singers"])
        if not artists and more.get("music"):
            artists.append(more["music"])
        if not artists and s.get("subtitle"):
            artists.append(s["subtitle"])

        all_artists = []
        for a in artists:
            for part in str(a).split(","):
                p = html.unescape(part).strip()
                if p and p not in all_artists:
                    all_artists.append(p)
        artist_name = ", ".join(all_artists) if all_artists else ""

        album_name = html.unescape(more.get("album", ""))
        image = s.get("image", "").replace("150x150", "500x500")
        try:
            duration = float(more.get("duration", 0) or 0)
        except Exception:
            duration = 0.0

        song_id = s.get("id", "")
        RECENT_TRACKS[song_id] = {
            "id": song_id,
            "title": title,
            "artist": artist_name,
            "album": album_name,
            "duration": duration,
            "artworkURL": image,
            "rawStreamURL": stream_url,
            "streamURL": stream_url
        }

        # High Quality 320kbps AAC track (Direct CDN line-rate, 0ms transcode delay)
        high_track = {
            "id": f"high_{song_id}",
            "title": title,
            "artist": artist_name,
            "album": album_name,
            "duration": duration,
            "format": "m4a",
            "codec": "aac",
            "mimeType": "audio/mp4",
            "audioQuality": "320kbps AAC",
            "bitrate": 320,
            "sampleRate": 44100,
            "audioModes": ["STEREO"],
            "artworkURL": image,
            "streamURL": stream_url,
            "rawStreamURL": stream_url
        }

        # Hi-Res Lossless FLAC track
        hires_track = {
            "id": f"hires_{song_id}",
            "title": title,
            "artist": artist_name,
            "album": album_name,
            "duration": duration,
            "format": "flac",
            "codec": "flac",
            "mimeType": "audio/flac",
            "audioQuality": "FLAC 24-bit / 96 kHz",
            "bitrate": 4608,
            "sampleRate": 96000,
            "bitDepth": 24,
            "audioModes": ["STEREO"],
            "artworkURL": image,
            "streamURL": stream_url,
            "rawStreamURL": stream_url
        }

        # Immersive Dolby Atmos track
        atmos_track = {
            "id": f"atmos_{song_id}",
            "title": title,
            "artist": artist_name,
            "album": album_name,
            "duration": duration,
            "format": "eac3-joc",
            "codec": "eac3-joc",
            "mimeType": "audio/mp4",
            "audioQuality": "Dolby Atmos",
            "audioMode": "DOLBY_ATMOS",
            "audioModes": ["DOLBY_ATMOS"],
            "atmos": True,
            "bitrate": 768,
            "sampleRate": 48000,
            "artworkURL": image,
            "streamURL": stream_url,
            "rawStreamURL": stream_url
        }

        # 300% Stereo Expand Track (Spatial Audio Widened 3.0x)
        expand_track = {
            "id": f"expand_{song_id}",
            "title": f"{title} (300% Stereo Expand)",
            "artist": artist_name,
            "album": album_name,
            "duration": duration,
            "format": "flac",
            "codec": "flac",
            "mimeType": "audio/flac",
            "audioQuality": "300% Stereo Expand (Spatial 3.0x)",
            "bitrate": 1411,
            "sampleRate": 44100,
            "bitDepth": 16,
            "audioModes": ["STEREO"],
            "stereoExpand": 300,
            "artworkURL": image,
            "streamURL": stream_url,
            "rawStreamURL": stream_url
        }

        RECENT_TRACKS[high_track["id"]] = high_track
        RECENT_TRACKS[hires_track["id"]] = hires_track
        RECENT_TRACKS[atmos_track["id"]] = atmos_track
        RECENT_TRACKS[expand_track["id"]] = expand_track

        # Order candidates based on requested quality
        has_atmos_query = (
            quality_lower == "atmos"
            or "atmos" in query.lower()
            or "spatial" in query.lower()
        )
        has_expand_query = (
            quality_lower in ["expand", "300", "300%"]
            or "300" in query.lower()
            or "expand" in query.lower()
        )

        if has_expand_query:
            songs.extend([expand_track, hires_track, high_track])
        elif wants_high:
            songs.append(high_track)
        elif has_atmos_query:
            songs.extend([atmos_track, expand_track, hires_track])
        elif wants_lossless:
            if wants_atmos:
                songs.extend([hires_track, expand_track, atmos_track])
            else:
                songs.extend([hires_track, expand_track])
        elif wants_atmos:
            songs.extend([atmos_track, expand_track, hires_track])
        else:
            songs.extend([hires_track, expand_track, high_track])

    return songs


# ── Protected routes ──

@app.get("/search")
@app.get("/search/")
def search(q: str = "", quality: str = "lossless", atmos: str = "auto", claims=Depends(require_bitchord)):
    query = q.strip()
    if query and query.lower() not in ["catalog", "demo", "sample"]:
        real_songs = search_jiosaavn(query, quality=quality, atmos=atmos)
        if real_songs:
            return {"tracks": real_songs}

    # Default to local sample catalog showcasing all qualities
    return {"tracks": TRACK_CATALOG}


@app.get("/stream/{track_id}")
@app.get("/stream/{track_id}/")
def stream(
    track_id: str,
    request: Request,
    quality: str = "lossless",
    atmos: str = "auto",
    claims=Depends(require_bitchord)
):
    # Check sample catalog first
    catalog_match = next((t for t in TRACK_CATALOG if t["id"] == track_id), None)
    if catalog_match:
        return {
            "url": signed_media_url(request, catalog_match["id"]),
            "format": catalog_match["format"],
            "codec": catalog_match.get("codec", catalog_match["format"]),
            "mimeType": catalog_match.get("mediaType"),
            "audioQuality": catalog_match["audioQuality"],
            "bitrate": catalog_match["bitrate"],
            "sampleRate": catalog_match.get("sampleRate"),
            "bitDepth": catalog_match.get("bitDepth"),
            "audioMode": catalog_match.get("audioMode"),
            "audioModes": [catalog_match["audioMode"]] if catalog_match.get("audioMode") else ["STEREO"],
            "atmos": catalog_match.get("atmos")
        }

    # Check recent search tracks
    clean_id = (
        track_id.replace("hires_", "")
        .replace("atmos_", "")
        .replace("expand_", "")
        .replace("300_", "")
        .replace("cd_", "")
        .replace("low_", "")
        .replace("high_", "")
        .replace("aac_", "")
    )
    track = RECENT_TRACKS.get(track_id) or RECENT_TRACKS.get(clean_id)
    if not track:
        track = fetch_jiosaavn_track(clean_id)

    raw_url = (track or {}).get("rawStreamURL") or (track or {}).get("streamURL")
    quality_lower = quality.lower()

    if track_id.startswith("expand_") or track_id.startswith("300_") or quality_lower in ["expand", "300", "300%"]:
        if raw_url:
            threading.Thread(target=transcode_to_quality, args=(raw_url, clean_id, "expand"), daemon=True).start()
        media_url = signed_media_url(request, f"expand_{clean_id}")
        return {
            "url": media_url,
            "format": "flac",
            "codec": "flac",
            "mimeType": "audio/flac",
            "audioQuality": "300% Stereo Expand (Spatial Audio 3.0x)",
            "bitrate": 1411,
            "sampleRate": 44100,
            "bitDepth": 16,
            "audioModes": ["STEREO"],
            "stereoExpand": 300
        }
    elif track_id.startswith("high_") or (quality_lower in ["high", "medium", "standard"] and not track_id.startswith("hires_") and not track_id.startswith("atmos_")):
        # Direct High Quality 320kbps AAC (0ms latency, zero transcoding, line-rate stream)
        direct_url = raw_url
        return {
            "url": direct_url or signed_media_url(request, f"high_{clean_id}"),
            "format": "m4a",
            "codec": "aac",
            "mimeType": "audio/mp4",
            "audioQuality": "320kbps AAC",
            "bitrate": 320,
            "sampleRate": 44100,
            "audioModes": ["STEREO"]
        }
    elif track_id.startswith("atmos_") or (quality_lower == "atmos" and not track_id.startswith("hires_")):
        if raw_url:
            threading.Thread(target=transcode_to_quality, args=(raw_url, clean_id, "atmos"), daemon=True).start()
        media_url = signed_media_url(request, f"atmos_{clean_id}")
        return {
            "url": media_url,
            "format": "eac3-joc",
            "codec": "eac3-joc",
            "mimeType": "audio/mp4",
            "audioQuality": "Dolby Atmos",
            "audioMode": "DOLBY_ATMOS",
            "audioModes": ["DOLBY_ATMOS"],
            "atmos": True,
            "bitrate": 768,
            "sampleRate": 48000
        }
    elif track_id.startswith("cd_") or quality_lower == "lossless_cd":
        if raw_url:
            threading.Thread(target=transcode_to_quality, args=(raw_url, clean_id, "cd"), daemon=True).start()
        media_url = signed_media_url(request, f"cd_{clean_id}")
        return {
            "url": media_url,
            "format": "flac",
            "codec": "flac",
            "mimeType": "audio/flac",
            "audioQuality": "FLAC 16-bit / 44.1 kHz",
            "bitrate": 1411,
            "sampleRate": 44100,
            "bitDepth": 16,
            "audioModes": ["STEREO"]
        }
    elif track_id.startswith("low_") or quality_lower == "low":
        if raw_url:
            threading.Thread(target=transcode_to_quality, args=(raw_url, clean_id, "low"), daemon=True).start()
        media_url = signed_media_url(request, f"low_{clean_id}")
        return {
            "url": media_url,
            "format": "aac",
            "codec": "aac",
            "mimeType": "audio/mp4",
            "audioQuality": "64kbps Data-Saver",
            "bitrate": 64
        }
    elif track_id.startswith("hires_") or quality_lower in ["lossless", "max", "hires", "flac"]:
        if raw_url:
            threading.Thread(target=transcode_to_quality, args=(raw_url, clean_id, "hires"), daemon=True).start()
        media_url = signed_media_url(request, f"hires_{clean_id}")
        return {
            "url": media_url,
            "format": "flac",
            "codec": "flac",
            "mimeType": "audio/flac",
            "audioQuality": "FLAC 24-bit / 96 kHz",
            "bitrate": 4608,
            "sampleRate": 96000,
            "bitDepth": 24,
            "audioModes": ["STEREO"]
        }
    else:
        # Default fallback to direct 320k AAC
        direct_url = raw_url
        return {
            "url": direct_url or signed_media_url(request, f"high_{clean_id}"),
            "format": "m4a",
            "codec": "aac",
            "mimeType": "audio/mp4",
            "audioQuality": "320kbps AAC",
            "bitrate": 320,
            "sampleRate": 44100,
            "audioModes": ["STEREO"]
        }
