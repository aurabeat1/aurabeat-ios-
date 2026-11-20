# AuraBeat / BitChord Addon Server (with Addon Auth)

A high-performance addon server template for AuraBeat & BitChord supporting all audio quality tiers and the official **BitChord Addon Auth** protocol (v1).

## Supported Audio Qualities
- **Hi-Res Lossless**: 24-bit / 96 kHz or 192 kHz FLAC (Studio Master quality)
- **Lossless CD**: 16-bit / 44.1 kHz FLAC / ALAC
- **Dolby Atmos / Spatial Audio**: Multi-channel E-AC-3 JOC
- **High Quality**: 320 kbps AAC / MP3
- **Standard**: 256 kbps AAC

---

## Quickstart

### Option A: Node.js / Express
```bash
cd addon-server
npm install
npm start
```

### Option B: Cloudflare Workers (Serverless Edge)
```bash
cd addon-server
npx wrangler dev worker.js
# Or deploy to Cloudflare's global edge network:
npx wrangler deploy worker.js
```

### Option C: Python FastAPI
```bash
cd addon-server
pip install -r requirements.txt
uvicorn main:app --reload --port 3000
```

---

## Configuration

Set your environment variables in `.env` (or in your hosting provider's dashboard):

- `BITCHORD_HOSTS`: **CRITICAL**. Comma-separated list of public hostnames where your addon is reachable (e.g., `myaddon.example.com,myaddon.onrender.com`).
  - No `https://` prefix, no port, no path.
  - Getting this wrong causes `invalid_proof` errors.
- `BITCHORD_AUTH_MODE`:
  - `enforce`: Returns `401` to any client that isn't the official BitChord Android app.
  - `log`: Verifies requests and logs results to the console without blocking (recommended for the first 48 hours).
- `MEDIA_SECRET`: A long random string used to HMAC-sign short-lived audio URLs (Task 4).

---

## Acceptance Checks

Test your deployed server against the specification:

```bash
# 1. Manifest: Must be public and declare "bitchordAuth": true
curl -s https://myaddon.example.com/manifest.json | grep -o '"bitchordAuth": *[^,}]*'

# 2. Search without auth: Must return 401 with BitChord WWW-Authenticate header
curl -si 'https://myaddon.example.com/search?q=test' | head -5
# Expect:
# HTTP/1.1 401 Unauthorized
# www-authenticate: BitChord realm="bitchord-addons", error="missing", ...

# 3. Stream without auth: Must return 401
curl -si https://myaddon.example.com/stream/track-hires-1 | head -1

# 4. Media URLs: Must be accessible without BitChord headers (via HMAC query params)
```
