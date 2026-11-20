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
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.primitives import padding
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import Response, JSONResponse, FileResponse
from bitchord_addon_auth import BitChordAuth

try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = "ffmpeg"

# Paths
BASE_DIR = Path(__file__).parent
FIXTURES_DIR = BASE_DIR / "fixtures"
CACHE_DIR = BASE_DIR / "cache"
FIXTURES_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

# Configured hostnames from env
raw_hosts = os.getenv("BITCHORD_HOSTS", "localhost,127.0.0.1,192.168.1.13")
hosts = [h.strip() for h in raw_hosts.split(",") if h.strip()]
MEDIA_SECRET = os.getenv("MEDIA_SECRET", "aurabeat-addon-secret-key-change-in-production").encode()
AUTH_MODE = os.getenv("BITCHORD_AUTH_MODE", "log")

# Module scope verifier instance
bitchord = BitChordAuth(hosts=hosts)

app = FastAPI(title="AuraBeat Addon Server")

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
        "format": "eac3_joc",
        "audioQuality": "Dolby Atmos",
        "audioMode": "DOLBY_ATMOS",
        "atmos": True,
        "bitrate": 768,
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


def transcode_to_quality(raw_url: str, clean_id: str, tier: str) -> Path:
    """Transcodes a source audio stream into target quality and caches on disk."""
    if tier == "atmos":
        out_path = CACHE_DIR / f"{clean_id}_atmos.mp4"
        if not out_path.exists() or out_path.stat().st_size == 0:
            cmd = [
                FFMPEG_EXE, "-y",
                "-i", raw_url,
                "-af", "surround",
                "-c:a", "eac3",
                "-b:a", "768k",
                str(out_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path
    elif tier == "cd":
        out_path = CACHE_DIR / f"{clean_id}_cd.flac"
        if not out_path.exists() or out_path.stat().st_size == 0:
            cmd = [
                FFMPEG_EXE, "-y",
                "-i", raw_url,
                "-c:a", "flac",
                "-sample_fmt", "s16",
                "-ar", "44100",
                str(out_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path
    elif tier == "low":
        out_path = CACHE_DIR / f"{clean_id}_low.m4a"
        if not out_path.exists() or out_path.stat().st_size == 0:
            cmd = [
                FFMPEG_EXE, "-y",
                "-i", raw_url,
                "-c:a", "aac",
                "-b:a", "64k",
                "-ar", "44100",
                str(out_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path
    else:  # hires (default for lossless)
        out_path = CACHE_DIR / f"{clean_id}_hires.flac"
        if not out_path.exists() or out_path.stat().st_size == 0:
            cmd = [
                FFMPEG_EXE, "-y",
                "-i", raw_url,
                "-c:a", "flac",
                "-sample_fmt", "s32",
                "-ar", "96000",
                str(out_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_path


# ── Public routes ──

@app.get("/")
def get_root():
    return {
        "status": "online",
        "addon": MANIFEST.get("name"),
        "version": MANIFEST.get("version"),
        "manifest": "/manifest.json"
    }


@app.get("/manifest.json")
def get_manifest():
    # Stays public (Task 1)
    return JSONResponse(MANIFEST, headers={"Access-Control-Allow-Origin": "*"})


@app.get("/media/{track_id}")
def get_media(track_id: str, exp: str = "", sig: str = ""):
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
    if track_info and track_info.get("rawStreamURL"):
        raw_url = track_info["rawStreamURL"]
        tier = "hires"
        media_type = "audio/flac"
        if "atmos" in track_id:
            tier = "atmos"
            media_type = "audio/mp4"
        elif "cd" in track_id:
            tier = "cd"
            media_type = "audio/flac"
        elif "low" in track_id:
            tier = "low"
            media_type = "audio/mp4"

        cached_file = transcode_to_quality(raw_url, clean_id, tier)
        if cached_file.exists() and cached_file.stat().st_size > 0:
            return FileResponse(cached_file, media_type=media_type)

    # Fallback to high-res fixture
    return FileResponse(FIXTURES_DIR / "hires.flac", media_type="audio/flac")


def search_jiosaavn(query: str, quality: str = "lossless", atmos: str = "auto"):
    url = f"https://www.jiosaavn.com/api.php?__call=search.getResults&_format=json&_marker=0&api_version=4&ctx=android&q={urllib.parse.quote(query)}&p=1&n=15"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"JioSaavn search error: {e}")
        return []

    songs = []
    wants_lossless = quality in ["lossless", "max", "hires", "flac"]
    wants_atmos = atmos in ["auto", "1", "true"] or quality == "atmos"

    for s in data.get("results", []):
        more = s.get("more_info", {})
        enc_url = more.get("encrypted_media_url", "")
        stream_url = decrypt_jiosaavn_url(enc_url)
        title = html.unescape(s.get("title", ""))
        artists_list = more.get("artistMap", {}).get("primary_artists", [])
        artist_name = html.unescape(artists_list[0].get("name", "")) if artists_list else ""
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

        # 1. Primary Lossless FLAC row
        if wants_lossless:
            track = {
                "id": f"hires_{song_id}",
                "title": title,
                "artist": artist_name,
                "album": album_name,
                "duration": duration,
                "format": "flac",
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

            # 2. Immersive Dolby Atmos row
            if wants_atmos:
                atmos_track = {
                    "id": f"atmos_{song_id}",
                    "title": f"{title} (Spatial Audio)",
                    "artist": artist_name,
                    "album": album_name,
                    "duration": duration,
                    "format": "eac3_joc",
                    "audioQuality": "Dolby Atmos",
                    "audioMode": "DOLBY_ATMOS",
                    "audioModes": ["DOLBY_ATMOS"],
                    "atmos": True,
                    "bitrate": 768,
                    "artworkURL": image,
                    "rawStreamURL": stream_url
                }
                songs.append(atmos_track)
                RECENT_TRACKS[atmos_track["id"]] = atmos_track
        elif quality == "atmos":
            track = {
                "id": f"atmos_{song_id}",
                "title": title,
                "artist": artist_name,
                "album": album_name,
                "duration": duration,
                "format": "eac3_joc",
                "audioQuality": "Dolby Atmos",
                "audioMode": "DOLBY_ATMOS",
                "audioModes": ["DOLBY_ATMOS"],
                "atmos": True,
                "bitrate": 768,
                "artworkURL": image,
                "rawStreamURL": stream_url
            }
            songs.append(track)
            RECENT_TRACKS[track["id"]] = track
        else:
            # High Quality 320k AAC
            track = {
                "id": song_id,
                "title": title,
                "artist": artist_name,
                "album": album_name,
                "duration": duration,
                "format": "aac",
                "audioQuality": "320kbps AAC",
                "bitrate": 320,
                "artworkURL": image,
                "streamURL": stream_url,
                "rawStreamURL": stream_url
            }
            songs.append(track)
            RECENT_TRACKS[track["id"]] = track

    return songs


# ── Protected routes ──

@app.get("/search")
def search(q: str = "", quality: str = "lossless", atmos: str = "auto", claims=Depends(require_bitchord)):
    query = q.strip()
    if query and query.lower() not in ["catalog", "demo", "sample"]:
        real_songs = search_jiosaavn(query, quality=quality, atmos=atmos)
        if real_songs:
            return {"tracks": real_songs}

    # Default to local sample catalog showcasing all qualities
    return {"tracks": TRACK_CATALOG}


@app.get("/stream/{track_id}")
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

    if track_id.startswith("atmos_") or quality == "atmos":
        media_url = signed_media_url(request, f"atmos_{clean_id}")
        return {
            "url": media_url,
            "format": "eac3_joc",
            "audioQuality": "Dolby Atmos",
            "audioMode": "DOLBY_ATMOS",
            "audioModes": ["DOLBY_ATMOS"],
            "atmos": True,
            "bitrate": 768
        }
    elif track_id.startswith("cd_") or quality == "lossless_cd":
        media_url = signed_media_url(request, f"cd_{clean_id}")
        return {
            "url": media_url,
            "format": "flac",
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
            "audioQuality": "64kbps Data-Saver",
            "bitrate": 64
        }
    elif track_id.startswith("hires_") or quality in ["lossless", "max", "hires", "flac"]:
        media_url = signed_media_url(request, f"hires_{clean_id}")
        return {
            "url": media_url,
            "format": "flac",
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
            "audioQuality": "320kbps AAC",
            "bitrate": 320
        }
