"""AuraBeat / BitChord Addon Auth Server (FastAPI).

Run with:
    pip install fastapi uvicorn cryptography
    uvicorn main:app --reload --port 3000
"""

import base64
import html
import hmac
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.primitives import padding
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import Response, JSONResponse
from bitchord_addon_auth import BitChordAuth

# Configured hostnames from env
raw_hosts = os.getenv("BITCHORD_HOSTS", "localhost,127.0.0.1,192.168.1.13")
hosts = [h.strip() for h in raw_hosts.split(",") if h.strip()]
MEDIA_SECRET = os.getenv("MEDIA_SECRET", "aurabeat-addon-secret-key-change-in-production").encode()
AUTH_MODE = os.getenv("BITCHORD_AUTH_MODE", "log")

# Module scope verifier instance
bitchord = BitChordAuth(hosts=hosts)

app = FastAPI(title="AuraBeat Addon Server")

# Load manifest
manifest_path = Path(__file__).parent / "manifest.json"
with open(manifest_path, "r", encoding="utf-8") as f:
    MANIFEST = json.load(f)

# Sample track catalogue showcasing all audio qualities
TRACK_CATALOG = [
    {
        "id": "track-hires-1",
        "title": "Aura of Serenity (Hi-Res Lossless)",
        "artist": "AuraBeat Orchestra",
        "album": "Master Sessions Vol. 1",
        "duration": 245.0,
        "format": "flac",
        "audioQuality": "FLAC 24-bit / 96 kHz",
        "bitrate": 4608,
        "sampleRate": 96000,
        "bitDepth": 24,
        "artworkURL": "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=500",
    },
    {
        "id": "track-atmos-1",
        "title": "Dimensional Symphony (Spatial Audio)",
        "artist": "Spatial Sounds Collective",
        "album": "Immersive Spheres",
        "duration": 312.0,
        "format": "eac3_joc",
        "audioQuality": "Dolby Atmos",
        "audioMode": "DOLBY_ATMOS",
        "atmos": True,
        "bitrate": 768,
        "artworkURL": "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=500",
    },
    {
        "id": "track-lossless-1",
        "title": "Acoustic Reverie (CD Quality)",
        "artist": "Elena Rostova",
        "album": "Studio Unplugged",
        "duration": 198.0,
        "format": "flac",
        "audioQuality": "FLAC 16-bit / 44.1 kHz",
        "bitrate": 1411,
        "sampleRate": 44100,
        "bitDepth": 16,
        "artworkURL": "https://images.unsplash.com/photo-1470225620780-dba8ba36b745?w=500",
    },
    {
        "id": "track-high-1",
        "title": "Neon Horizon (High Quality 320k)",
        "artist": "Synthwave Pulse",
        "album": "Cyber Odyssey",
        "duration": 215.0,
        "format": "aac",
        "audioQuality": "320kbps AAC",
        "bitrate": 320,
        "artworkURL": "https://images.unsplash.com/photo-1508700115892-45ecd05ae2ad?w=500",
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
    return Response(content=b"DEMO_AUDIO_PAYLOAD", media_type="audio/flac")


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

def search_jiosaavn(query: str):
    url = f"https://www.jiosaavn.com/api.php?__call=search.getResults&_format=json&_marker=0&api_version=4&ctx=android&q={urllib.parse.quote(query)}&p=1&n=15"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"JioSaavn search error: {e}")
        return []

    songs = []
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

        track = {
            "id": s.get("id", ""),
            "title": title,
            "artist": artist_name,
            "album": album_name,
            "duration": duration,
            "format": "aac",
            "audioQuality": "320kbps AAC",
            "bitrate": 320,
            "artworkURL": image,
            "streamURL": stream_url
        }
        songs.append(track)
        RECENT_TRACKS[track["id"]] = track
    return songs


# ── Protected routes ──

@app.get("/search")
def search(q: str = "", quality: str = "lossless", claims=Depends(require_bitchord)):
    query = q.strip()
    if query:
        real_songs = search_jiosaavn(query)
        if real_songs:
            return {"tracks": real_songs}

    # Fallback to local sample catalog
    return {"tracks": TRACK_CATALOG}


@app.get("/stream/{track_id}")
def stream(track_id: str, request: Request, quality: str = "lossless", claims=Depends(require_bitchord)):
    if track_id in RECENT_TRACKS:
        track = RECENT_TRACKS[track_id]
        return {
            "url": track.get("streamURL") or signed_media_url(request, track["id"]),
            "format": track.get("format", "aac"),
            "audioQuality": track.get("audioQuality", "320kbps AAC"),
            "bitrate": track.get("bitrate", 320),
            "sampleRate": track.get("sampleRate"),
            "bitDepth": track.get("bitDepth"),
            "audioMode": track.get("audioMode"),
            "atmos": track.get("atmos")
        }

    track = next((t for t in TRACK_CATALOG if t["id"] == track_id), TRACK_CATALOG[0])
    media_url = signed_media_url(request, track["id"])
    return {
        "url": media_url,
        "format": track["format"],
        "audioQuality": track["audioQuality"],
        "bitrate": track["bitrate"],
        "sampleRate": track.get("sampleRate"),
        "bitDepth": track.get("bitDepth"),
        "audioMode": track.get("audioMode"),
        "atmos": track.get("atmos")
    }
