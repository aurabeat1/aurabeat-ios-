# AuraBeat & BitChord Addon Server Specification (`ADDON.md`)

This document defines the complete, exact ("same to same") specification for building, deploying, and integrating an Audio Addon Server with **AuraBeat iOS** and **BitChord Android**.

---

## 1. Overview & Architecture

An AuraBeat Addon is a lightweight HTTP microservice that acts as an audio source provider. It exposes:
1. **Addon Manifest (`/manifest.json`)**: Declares addon metadata, features, and quality options.
2. **Search API (`/search`)**: Queries music catalogues and returns matched tracks with audio quality metadata.
3. **Stream Resolver API (`/stream/{track_id}`)**: Resolves a track ID to a playable media URL with codec/bitrate specifications.
4. **Media Server (`/media/{track_id}`)**: Delivers raw audio streams with HTTP range requests (`206 Partial Content`).
5. **BitChord Addon Auth (Optional / Configurable)**: Hardware-backed ECDSA request verification.

```
┌─────────────────────────────────┐
│     AuraBeat iOS / Android      │
└──────────────┬──────────────────┘
               │ 1. GET /manifest.json
               ▼
┌─────────────────────────────────┐
│         Addon Server            │
└──────────────┬──────────────────┘
               │ 2. GET /search?q=Artist+-+Title
               ▼
┌─────────────────────────────────┐
│  Track Matcher (Catalogue Search)│
└──────────────┬──────────────────┘
               │ 3. GET /stream/{track_id}
               ▼
┌─────────────────────────────────┐
│  Stream Resolver & HMAC Signer  │
└──────────────┬──────────────────┘
               │ 4. GET /media/{track_id}?exp=...&sig=... (HTTP 206)
               ▼
┌─────────────────────────────────┐
│ AVPlayer / Core Audio / ExoPlayer│
└─────────────────────────────────┘
```

---

## 2. Audio Quality Tiers & Codecs

The server supports all AuraBeat audio quality tiers:

| Quality Tier | Audio Format | Codec | Sample Rate / Bit Depth | Bitrate | `audioModes` / Flags |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Hi-Res Lossless** | `flac` | `flac` | 96 kHz / 24-bit | ~4608 kbps | `["STEREO"]` |
| **Lossless CD** | `flac` / `alac` | `flac` | 44.1 kHz / 16-bit | ~1411 kbps | `["STEREO"]` |
| **Dolby Atmos** | `eac3-joc` / `mp4` | `eac3-joc` | 48 kHz | 768 kbps | `["DOLBY_ATMOS"]`, `atmos: true` |
| **High Quality** | `m4a` / `aac` | `aac` | 44.1 kHz | 320 kbps | `["STEREO"]` |
| **Standard Quality** | `m4a` / `aac` | `aac` | 44.1 kHz | 256 kbps | `["STEREO"]` |
| **Data-Saver** | `m4a` / `aac` | `aac` | 44.1 kHz | 64–128 kbps | `["STEREO"]` |

---

## 3. API Endpoints Specification

### 3.1. Addon Manifest (`GET /manifest.json` or `GET /addon`)

The manifest is public and fetched once when the addon is added or refreshed.

#### Request
```http
GET /manifest.json HTTP/1.1
Host: your-addon-domain.com
Accept: application/json
```

#### Response (`200 OK`)
```json
{
  "id": "com.aurabeat.addon",
  "name": "AuraBeat Audio Addon",
  "version": "1.0.0",
  "description": "High fidelity audio addon supporting Hi-Res Lossless, Dolby Atmos, and High Quality streams",
  "resources": [
    "search",
    "stream"
  ],
  "bitchordAuth": true,
  "settings": [
    {
      "key": "quality",
      "type": "select",
      "default": "lossless",
      "options": [
        { "value": "lossless", "label": "Hi-Res Lossless (FLAC 24-bit/96kHz)" },
        { "value": "atmos", "label": "Dolby Atmos (Spatial Audio E-AC-3 JOC)" },
        { "value": "high", "label": "High Quality (AAC 320 kbps)" },
        { "value": "medium", "label": "Standard Quality (AAC 256 kbps)" },
        { "value": "low", "label": "Data-Saver (AAC 128 kbps)" }
      ]
    }
  ]
}
```

---

### 3.2. Search Tracks (`GET /search`)

Searches the audio provider catalogue for matching tracks.

#### Request Parameters
- `q` (string, required): Song title, artist, or search phrase (e.g., `Shakira - Waka Waka`).
- `quality` (string, optional): Desired tier (`lossless`, `atmos`, `high`, `medium`, `low`). Default: `lossless`.
- `atmos` (string, optional): `auto`, `true`, `false`. Default: `auto`.

#### Example Request
```http
GET /search?q=Aura+of+Serenity&quality=lossless&atmos=auto HTTP/1.1
Host: your-addon-domain.com
Accept: application/json
```

#### Response (`200 OK`)
```json
{
  "tracks": [
    {
      "id": "hires_track123",
      "title": "Aura of Serenity",
      "artist": "AuraBeat Orchestra",
      "album": "Master Sessions Vol. 1",
      "duration": 215.0,
      "format": "flac",
      "codec": "flac",
      "mediaType": "audio/flac",
      "audioQuality": "FLAC 24-bit / 96 kHz",
      "bitrate": 4608,
      "sampleRate": 96000,
      "bitDepth": 24,
      "artworkURL": "https://example.com/art.jpg",
      "audioMode": "STEREO",
      "audioModes": ["STEREO"]
    },
    {
      "id": "atmos_track123",
      "title": "Aura of Serenity (Spatial)",
      "artist": "AuraBeat Orchestra",
      "album": "Master Sessions Vol. 1",
      "duration": 215.0,
      "format": "eac3-joc",
      "codec": "eac3-joc",
      "mediaType": "audio/mp4",
      "audioQuality": "Dolby Atmos",
      "audioMode": "DOLBY_ATMOS",
      "audioModes": ["DOLBY_ATMOS"],
      "atmos": true,
      "bitrate": 768,
      "sampleRate": 48000,
      "artworkURL": "https://example.com/art.jpg"
    },
    {
      "id": "high_track123",
      "title": "Aura of Serenity",
      "artist": "AuraBeat Orchestra",
      "album": "Master Sessions Vol. 1",
      "duration": 215.0,
      "format": "m4a",
      "codec": "aac",
      "mediaType": "audio/mp4",
      "audioQuality": "320kbps AAC",
      "bitrate": 320,
      "sampleRate": 44100,
      "artworkURL": "https://example.com/art.jpg",
      "audioModes": ["STEREO"]
    }
  ]
}
```

---

### 3.3. Stream Resolution (`GET /stream/{track_id}`)

Resolves a track identifier to a playable stream URL and delivers technical stream metadata.

#### Request
```http
GET /stream/hires_track123?quality=lossless HTTP/1.1
Host: your-addon-domain.com
Accept: application/json
```

#### Response (`200 OK`) — Hi-Res Lossless
```json
{
  "url": "https://your-addon-domain.com/media/hires_track123?exp=1763836800&sig=5d41402abc4b2a76b9719d911017c592",
  "format": "flac",
  "codec": "flac",
  "mimeType": "audio/flac",
  "audioQuality": "FLAC 24-bit / 96 kHz",
  "bitrate": 4608,
  "sampleRate": 96000,
  "bitDepth": 24,
  "audioModes": ["STEREO"]
}
```

#### Response (`200 OK`) — Dolby Atmos
```json
{
  "url": "https://your-addon-domain.com/media/atmos_track123?exp=1763836800&sig=5d41402abc4b2a76b9719d911017c592",
  "format": "eac3-joc",
  "codec": "eac3-joc",
  "mimeType": "audio/mp4",
  "audioQuality": "Dolby Atmos",
  "audioMode": "DOLBY_ATMOS",
  "audioModes": ["DOLBY_ATMOS"],
  "atmos": true,
  "bitrate": 768,
  "sampleRate": 48000
}
```

---

### 3.4. Media Streaming (`GET /media/{track_id}`)

Streams the actual audio payload to AVPlayer / Core Audio. Supports HTTP Range requests (`Accept-Ranges: bytes`).

#### Request Header Requirements
- Supports `Range: bytes=0-` or `Range: bytes=0-16383` (first bytes probe).
- URL contains HMAC expiration timestamp `exp` and signature `sig`:
  $$\text{signature} = \text{HMAC-SHA256}(\text{MEDIA\_SECRET}, \text{track\_id} + "." + \text{exp})$$

#### Response (`206 Partial Content`)
```http
HTTP/1.1 206 Partial Content
Accept-Ranges: bytes
Content-Range: bytes 0-16383/48291044
Content-Length: 16384
Content-Type: audio/flac
Access-Control-Allow-Origin: *

<raw audio bytes>
```

---

## 4. BitChord Addon Authentication Protocol (v1)

When `"bitchordAuth": true` is enabled in `manifest.json`:
1. Requests to `/search` and `/stream/{id}` carry verification headers:
   - `X-BitChord-Key-ID`: Public key identifier.
   - `X-BitChord-Timestamp`: Unix epoch millisecond timestamp.
   - `X-BitChord-Signature`: Cryptographic signature verifying the request origin.
2. If invalid or missing in `enforce` mode, return:
   ```http
   HTTP/1.1 401 Unauthorized
   WWW-Authenticate: BitChord realm="bitchord-addons", error="missing"
   ```
3. In `log` mode (`BITCHORD_AUTH_MODE=log`), requests are verified and logged for diagnostics without rejecting unauthenticated requests (ideal for local testing).

---

## 5. Deployment Guide

### Option 1: Python FastAPI (Recommended)
```bash
# 1. Navigate to addon server directory
cd addon-server

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
export BITCHORD_HOSTS="localhost,127.0.0.1,your-domain.onrender.com"
export BITCHORD_AUTH_MODE="log"
export MEDIA_SECRET="super-secret-hmac-key"

# 4. Start the server
uvicorn main:app --host 0.0.0.0 --port 3000 --reload
```

### Option 2: Node.js / Express
```bash
cd addon-server
npm install
node server.mjs
```

### Option 3: Cloudflare Workers (Serverless Global Edge)
```bash
cd addon-server
npx wrangler dev worker.js
npx wrangler deploy worker.js
```

### Option 4: Render / Railway / Docker
Create a Web Service with:
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- **Environment Variables**:
  - `BITCHORD_HOSTS`: `your-app-name.onrender.com`
  - `BITCHORD_AUTH_MODE`: `log`
  - `MEDIA_SECRET`: `<random-secret>`

---

## 6. Connecting to AuraBeat iOS

1. Open **AuraBeat iOS**.
2. Go to **Settings › Sources › Add Source**.
3. Enter your Addon Server URL:
   ```
   https://your-addon-domain.com
   ```
   *(Or default background addon: `https://aurabeat-ios.onrender.com`)*
4. AuraBeat automatically loads `manifest.json`, validates endpoints, and prioritizes your Hi-Res Lossless / Dolby Atmos streams during playback.
