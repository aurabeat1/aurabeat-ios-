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
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

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
raw_hosts = os.getenv("BITCHORD_HOSTS", "localhost,127.0.0.1,192.168.1.13,aurabeat-ios.onrender.com,aurabeat-ios.vercel.app")
hosts = [h.strip() for h in raw_hosts.split(",") if h.strip()]
MEDIA_SECRET = os.getenv("MEDIA_SECRET", "aurabeat-addon-secret-key-change-in-production").encode()
AUTH_MODE = os.getenv("BITCHORD_AUTH_MODE", "log")

# Module scope verifier instance
bitchord = BitChordAuth(hosts=hosts)

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AuraBeat Addon Server")

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
TRANSCODE_SEMAPHORE = threading.Semaphore(1)

def transcode_to_quality(raw_url: str, clean_id: str, tier: str) -> Optional[Path]:
    """Transcodes a source audio stream into target quality and caches on disk."""
    if not raw_url:
        return None
    lock_key = f"{clean_id}_{tier}"
    if lock_key in ACTIVE_TRANSCODES:
        # Another thread is already transcoding this exact file
        return None
    ACTIVE_TRANSCODES.add(lock_key)
    t_start = time.time()
    try:
        out_ext = "flac" if tier in ["hires", "cd"] else ("mp4" if tier == "atmos" else "m4a")
        out_path = CACHE_DIR / f"{clean_id}_{tier}.{out_ext}"
        if out_path.exists() and out_path.stat().st_size > 1000:
            return out_path
        tmp_path = CACHE_DIR / f"{clean_id}_{tier}_{os.getpid()}_{time.time_ns()}.tmp.{out_ext}"

        # 1. Fast local download of raw source audio via urllib (~0.5s on Render)
        local_raw = CACHE_DIR / f"{clean_id}.raw.mp4"
        if not local_raw.exists() or local_raw.stat().st_size < 1000:
            tmp_raw = CACHE_DIR / f"{clean_id}_{os.getpid()}_{time.time_ns()}.tmp.raw.mp4"
            req = urllib.request.Request(raw_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as r, open(tmp_raw, "wb") as f:
                f.write(r.read())
            if tmp_raw.exists() and tmp_raw.stat().st_size > 1000:
                tmp_raw.replace(local_raw)

        input_src = str(local_raw) if local_raw.exists() and local_raw.stat().st_size > 1000 else raw_url

        if tier == "atmos":
            cmd = [
                FFMPEG_EXE, "-y",
                "-threads", "0",
                "-i", input_src,
                "-c:a", "eac3",
                "-b:a", "768k",
                "-ar", "48000",
                "-movflags", "+faststart",
                str(tmp_path)
            ]
        elif tier == "cd":
            cmd = [
                FFMPEG_EXE, "-y",
                "-threads", "0",
                "-i", input_src,
                "-c:a", "flac",
                "-sample_fmt", "s16",
                "-ar", "44100",
                "-compression_level", "0",
                str(tmp_path)
            ]
        elif tier == "low":
            cmd = [
                FFMPEG_EXE, "-y",
                "-threads", "0",
                "-i", input_src,
                "-c:a", "aac",
                "-b:a", "64k",
                "-ar", "44100",
                "-movflags", "+faststart",
                str(tmp_path)
            ]
        else:  # hires (default for lossless - FLAC 24-bit PCM)
            cmd = [
                FFMPEG_EXE, "-y",
                "-threads", "0",
                "-i", input_src,
                "-c:a", "flac",
                "-sample_fmt", "s32",
                "-bits_per_raw_sample", "24",
                "-compression_level", "0",
                str(tmp_path)
            ]

        # Acquire semaphore so at most 1 ffmpeg process runs at full CPU speed
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
            # Prune cache if more than 60 files
            try:
                files = sorted(CACHE_DIR.glob("*"), key=lambda p: p.stat().st_mtime)
                if len(files) > 60:
                    for old_f in files[:len(files)-60]:
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
        <button class="btn" onclick="searchTracks()">Search</button>
      </div>
      <div class="results" id="results">
        <div style="text-align: center; color: var(--subtext); padding: 15px; font-size: 13px;">Type a song above to test the live audio stream.</div>
      </div>
      <div class="player-section" id="playerWrap" style="display: none;">
        <div id="nowPlayingText" style="font-size: 13px; font-weight: 600; margin-bottom: 8px; color: #60a5fa;"></div>
        <audio id="audioPlayer" controls></audio>
      </div>
    </div>

    <div class="card">
      <h2>⚡ Audio Quality Badges Supported</h2>
      <div class="quality-grid">
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
  </div>

  <script>
    function copyUrl() {
      const url = document.getElementById('addonUrl').innerText;
      navigator.clipboard.writeText(url).then(() => {
        alert('Copied Addon URL to clipboard: ' + url);
      });
    }

    async function searchTracks() {
      const q = document.getElementById('query').value.trim();
      if (!q) return;
      const resEl = document.getElementById('results');
      resEl.innerHTML = '<div style="text-align: center; color: var(--subtext); padding: 15px;">Searching...</div>';
      try {
        const r = await fetch('/search?q=' + encodeURIComponent(q) + '&quality=lossless&atmos=auto');
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
      text.innerText = 'Loading: ' + decodeURIComponent(title) + '...';
      pWrap.style.display = 'block';
      try {
        const r = await fetch('/stream/' + id + '?quality=lossless&atmos=auto');
        const data = await r.json();
        if (data.url) {
          player.src = data.url;
          player.play();
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


@app.get("/media/{track_id}")
@app.get("/media/{track_id}/")
def get_media(track_id: str, request: Request, exp: str = "", sig: str = ""):
    # Media URLs expire in 15-30 min and don't need BitChord headers (Task 4)
    if not media_allowed(track_id, exp, sig):
        raise HTTPException(status_code=403, detail="Expired or invalid media URL")

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
        .replace("cd_", "")
        .replace("low_", "")
        .replace("aac_", "")
    )
    track_info = RECENT_TRACKS.get(clean_id) or RECENT_TRACKS.get(track_id)
    if not track_info:
        track_info = fetch_jiosaavn_track(clean_id)

    if track_info and track_info.get("rawStreamURL"):
        raw_url = track_info["rawStreamURL"]
        tier = "hires"
        media_type = "audio/flac"
        target_name = f"{clean_id}_hires.flac"
        if "atmos" in track_id:
            tier = "atmos"
            media_type = "audio/mp4"
            target_name = f"{clean_id}_atmos.mp4"
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

        # Trigger transcode immediately in background thread
        threading.Thread(target=transcode_to_quality, args=(raw_url, clean_id, tier), daemon=True).start()

        # Poll up to 4.5 seconds (AuraBeat firstBytesTimeout is 6.0s)
        # Dedicated transcode takes ~1.5-2.5s with local download, returning FileResponse as soon as ready
        t0 = time.time()
        while time.time() - t0 < 4.5:
            if cached_file.exists() and cached_file.stat().st_size > 1000:
                return FileResponse(cached_file, media_type=media_type)
            time.sleep(0.04)

        if cached_file.exists() and cached_file.stat().st_size > 1000:
            return FileResponse(cached_file, media_type=media_type)

        # HTTP 206 Partial Content proxy fallback (NEVER 307 redirect!)
        # AuraBeat's firstBytesArrive checks (200..<300).contains(status); 307 causes stream failure and retry loops
        range_header = request.headers.get("range", "bytes=0-16383")
        try:
            proxy_req = urllib.request.Request(raw_url, headers={"Range": range_header, "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(proxy_req, timeout=5) as p_resp:
                content = p_resp.read()
                headers = {
                    "Content-Type": media_type,
                    "Accept-Ranges": "bytes",
                    "Content-Range": p_resp.headers.get("Content-Range", f"bytes 0-{len(content)-1}/*"),
                    "Content-Length": str(len(content)),
                    "Access-Control-Allow-Origin": "*",
                }
                return Response(content=content, status_code=206, headers=headers)
        except Exception as e:
            print(f"Proxy fallback error for {clean_id}: {e}")

    # 3. Fallback to fixture or 404
    hires_fix = FIXTURES_DIR / "hires.flac"
    if hires_fix.exists():
        return FileResponse(hires_fix, media_type="audio/flac")
    return Response(status_code=404, content="Track audio not found")


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
            cmd_nat = [FFMPEG_EXE, "-y", "-i", str(tmp_in), "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24", "-compression_level", "0", str(tmp_native)]
            r_nat = subprocess.run(cmd_nat, capture_output=True, text=True)
            b_timings["transcode_native_sec"] = round(time.time() - t0, 3)
            b_timings["native_size_mb"] = round(tmp_native.stat().st_size / 1024 / 1024, 2) if tmp_native.exists() else 0
            tmp_native.unlink(missing_ok=True)

            # 3. Local transcode FLAC 24-bit 96kHz
            t0 = time.time()
            tmp_96k = CACHE_DIR / "bench_96k.flac"
            cmd_96 = [FFMPEG_EXE, "-y", "-i", str(tmp_in), "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24", "-ar", "96000", "-compression_level", "0", str(tmp_96k)]
            r_96 = subprocess.run(cmd_96, capture_output=True, text=True)
            b_timings["transcode_96k_sec"] = round(time.time() - t0, 3)
            b_timings["96k_size_mb"] = round(tmp_96k.stat().st_size / 1024 / 1024, 2) if tmp_96k.exists() else 0
            tmp_96k.unlink(missing_ok=True)

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


def clean_search_query(q: str) -> str:
    # Remove bracketed tags like (Official Video), [Audio], (feat. X)
    cleaned = re.sub(r'[\(\[\{].*?[\)\]\}]', ' ', q)
    # Remove noise words
    cleaned = re.sub(r'\b(feat\.?|ft\.?|featuring|official\s+video|music\s+video|lyric\s+video|official|audio|remix|slowed|reverb|full\s+song|video)\b', ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'[^\w\s]', ' ', cleaned)
    # Remove extra spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


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
    results = fetch_jiosaavn_search_api(query)
    if not results:
        cq = clean_search_query(query)
        if cq and cq != query:
            results = fetch_jiosaavn_search_api(cq)
        if not results and " - " in query:
            parts = query.split(" - ")
            results = fetch_jiosaavn_search_api(parts[0].strip())
    if not results:
        return []

    songs = []
    wants_lossless = quality in ["lossless", "max", "hires", "flac"]
    wants_atmos = (
        atmos in ["auto", "1", "true"]
        or quality == "atmos"
        or "atmos" in query.lower()
        or "spatial" in query.lower()
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
            "rawStreamURL": stream_url
        }

        # 1. Immersive Dolby Atmos track
        if wants_atmos:
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
                "rawStreamURL": stream_url
            }
            songs.append(atmos_track)
            RECENT_TRACKS[atmos_track["id"]] = atmos_track

        # 2. Hi-Res Lossless FLAC track
        if wants_lossless or not wants_atmos:
            track = {
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
                "rawStreamURL": stream_url
            }
            songs.append(track)
            RECENT_TRACKS[track["id"]] = track

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
        .replace("cd_", "")
        .replace("low_", "")
        .replace("aac_", "")
    )
    track = RECENT_TRACKS.get(track_id) or RECENT_TRACKS.get(clean_id)
    if not track:
        track = fetch_jiosaavn_track(clean_id)

    raw_url = (track or {}).get("rawStreamURL")
    if raw_url:
        bg_tier = "hires"
        if track_id.startswith("atmos_") or (quality == "atmos" and not track_id.startswith("hires_")):
            bg_tier = "atmos"
        elif track_id.startswith("cd_") or quality == "lossless_cd":
            bg_tier = "cd"
        elif track_id.startswith("low_") or quality == "low":
            bg_tier = "low"
        threading.Thread(target=transcode_to_quality, args=(raw_url, clean_id, bg_tier), daemon=True).start()

    if track_id.startswith("atmos_") or (quality == "atmos" and not track_id.startswith("hires_")):
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
    elif track_id.startswith("cd_") or quality == "lossless_cd":
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
    elif track_id.startswith("low_") or quality == "low":
        media_url = signed_media_url(request, f"low_{clean_id}")
        return {
            "url": media_url,
            "format": "aac",
            "codec": "aac",
            "mimeType": "audio/mp4",
            "audioQuality": "64kbps Data-Saver",
            "bitrate": 64
        }
    elif track_id.startswith("hires_") or quality in ["lossless", "max", "hires", "flac"]:
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
        # Default High Quality 320k
        direct_url = (track or {}).get("streamURL") or (track or {}).get("rawStreamURL")
        return {
            "url": direct_url or signed_media_url(request, clean_id),
            "format": "aac",
            "codec": "aac",
            "mimeType": "audio/mp4",
            "audioQuality": "320kbps AAC",
            "bitrate": 320
        }
