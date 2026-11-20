import express from 'express';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createBitChordAuth } from './bitchord-addon-auth.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = process.env.PORT || 3000;
const MEDIA_SECRET = process.env.MEDIA_SECRET || 'aurabeat-addon-secret-key-change-in-production';
const AUTH_MODE = process.env.BITCHORD_AUTH_MODE || 'enforce'; // 'enforce' or 'log'

// Read configured hostnames from env (comma-separated), fallback to localhost
const rawHosts = process.env.BITCHORD_HOSTS || 'localhost,127.0.0.1';
const hosts = rawHosts.split(',').map(h => h.trim()).filter(Boolean);

console.log(`[AuraBeat Addon] Configured hosts: ${JSON.stringify(hosts)}`);
console.log(`[AuraBeat Addon] Auth mode: ${AUTH_MODE}`);

// Initialize BitChord Auth verifier at module scope
const bitchord = createBitChordAuth({ hosts });

const app = express();
app.use(express.json());

// Request logger to see incoming traffic in real-time
app.use((req, res, next) => {
  console.log(`[${new Date().toLocaleTimeString()}] ${req.method} ${req.originalUrl || req.url}`);
  next();
});

// Load manifest
const manifestPath = path.join(__dirname, 'manifest.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));

// ── Media Signing Helper (Task 4: short-lived media URLs expiring in 15–30 minutes)
export function signedMediaUrl(req, id, ttlSeconds = 1800) {
  const exp = Math.floor(Date.now() / 1000) + ttlSeconds;
  const sig = crypto.createHmac('sha256', MEDIA_SECRET).update(`${id}.${exp}`).digest('base64url');
  const proto = req.get('x-forwarded-proto') || req.protocol || 'http';
  const host = req.get('host') || 'localhost:3000';
  return `${proto}://${host}/media/${encodeURIComponent(id)}?exp=${exp}&sig=${sig}`;
}

export function mediaAllowed(id, exp, sig) {
  if (!/^\d+$/.test(String(exp)) || Number(exp) < Date.now() / 1000) return false;
  const want = crypto.createHmac('sha256', MEDIA_SECRET).update(`${id}.${exp}`).digest();
  const got = Buffer.from(String(sig ?? ''), 'base64url');
  return got.length === want.length && crypto.timingSafeEqual(got, want);
}

// ── Sample track database showcasing all audio qualities
const TRACK_CATALOG = [
  {
    id: 'track-hires-1',
    title: 'Aura of Serenity (Hi-Res Lossless)',
    artist: 'AuraBeat Orchestra',
    album: 'Master Sessions Vol. 1',
    duration: 245.0,
    format: 'flac',
    audioQuality: 'FLAC 24-bit / 96 kHz',
    bitrate: 4608,
    sampleRate: 96000,
    bitDepth: 24,
    artwork: 'https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=500',
  },
  {
    id: 'track-atmos-1',
    title: 'Dimensional Symphony (Spatial Audio)',
    artist: 'Spatial Sounds Collective',
    album: 'Immersive Spheres',
    duration: 312.0,
    format: 'eac3_joc',
    audioQuality: 'Dolby Atmos',
    audioMode: 'DOLBY_ATMOS',
    atmos: true,
    bitrate: 768,
    artwork: 'https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=500',
  },
  {
    id: 'track-lossless-1',
    title: 'Acoustic Reverie (CD Quality)',
    artist: 'Elena Rostova',
    album: 'Studio Unplugged',
    duration: 198.0,
    format: 'flac',
    audioQuality: 'FLAC 16-bit / 44.1 kHz',
    bitrate: 1411,
    sampleRate: 44100,
    bitDepth: 16,
    artwork: 'https://images.unsplash.com/photo-1470225620780-dba8ba36b745?w=500',
  },
  {
    id: 'track-high-1',
    title: 'Neon Horizon (High Quality 320k)',
    artist: 'Synthwave Pulse',
    album: 'Cyber Odyssey',
    duration: 215.0,
    format: 'aac',
    audioQuality: '320kbps AAC',
    bitrate: 320,
    artwork: 'https://images.unsplash.com/photo-1508700115892-45ecd05ae2ad?w=500',
  }
];

// ─────────────────────────────────────────────────────────────────────────────
// 1. PUBLIC ROUTES (No BitChord auth check required)
// ─────────────────────────────────────────────────────────────────────────────

// Root info
app.get('/', (req, res) => {
  res.json({
    status: 'online',
    addon: manifest.name,
    version: manifest.version,
    bitchordAuth: manifest.bitchordAuth,
    manifest: '/manifest.json',
  });
});

// /manifest.json MUST remain public
app.get('/manifest.json', (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.json(manifest);
});

// Media route: Fetched by player without BitChord headers. Protected by signed expiry & HMAC (Task 4)
app.get('/media/:id', (req, res) => {
  const { id } = req.params;
  const { exp, sig } = req.query;

  if (!mediaAllowed(id, exp, sig)) {
    return res.status(403).json({ error: 'forbidden', message: 'Media URL has expired or signature is invalid' });
  }

  // Stream audio (or proxy/redirect to storage CDN)
  res.setHeader('Content-Type', 'audio/flac');
  res.setHeader('Accept-Ranges', 'bytes');
  res.send(Buffer.from('DEMO_AUDIO_STREAM_PAYLOAD'));
});

// ─────────────────────────────────────────────────────────────────────────────
// 2. PROTECTED ROUTES (Requires BitChord Android hardware key verification)
// ─────────────────────────────────────────────────────────────────────────────

// Auth middleware applied to all routes below
app.use(async (req, res, next) => {
  if (AUTH_MODE === 'log') {
    const result = await bitchord.verifyRequest(req);
    if (!result.ok) {
      console.log(`[bitchord-auth LOG-ONLY] Request to ${req.path} failed auth: ${result.error} (${result.message})`);
    } else {
      req.bitchord = result.claims;
    }
    return next();
  }

  // Enforce mode
  return bitchord.express()(req, res, next);
});

// Search endpoint
app.get('/search', (req, res) => {
  const query = (req.query.q || '').toLowerCase();
  const quality = req.query.quality || 'lossless';

  console.log(`[Search] Query: "${query}", Quality preference: "${quality}", Client sub: ${req.bitchord?.sub || 'anonymous'}`);

  const matched = TRACK_CATALOG.filter(t => 
    t.title.toLowerCase().includes(query) || 
    t.artist.toLowerCase().includes(query) ||
    t.album.toLowerCase().includes(query) ||
    !query
  );

  res.json({ tracks: matched });
});

// Stream endpoint
app.get('/stream/:id', (req, res) => {
  const { id } = req.params;
  const quality = req.query.quality || 'lossless';
  const track = TRACK_CATALOG.find(t => t.id === id) || TRACK_CATALOG[0];

  console.log(`[Stream] Track: "${track.id}", Requested quality: "${quality}", Client sub: ${req.bitchord?.sub || 'anonymous'}`);

  // Return signed expiring media URL
  const streamUrl = signedMediaUrl(req, track.id);

  res.json({
    url: streamUrl,
    format: track.format,
    audioQuality: track.audioQuality,
    bitrate: track.bitrate,
    sampleRate: track.sampleRate,
    bitDepth: track.bitDepth,
    audioMode: track.audioMode,
    atmos: track.atmos
  });
});

app.listen(PORT, () => {
  console.log(`[AuraBeat Addon Server] Running on http://localhost:${PORT}`);
  console.log(`[AuraBeat Addon Server] Manifest available at http://localhost:${PORT}/manifest.json`);
});
