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
const rawHosts = process.env.BITCHORD_HOSTS || 'localhost,127.0.0.1,192.168.1.13';
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
    id: 'track-expand-1',
    title: 'Cosmic Drift (300% Stereo Expand)',
    artist: 'AuraBeat Sound Lab',
    album: 'Spatial Dimensions',
    duration: 120.0,
    format: 'flac',
    audioQuality: '300% Stereo Expand (Spatial 3.0x)',
    audioMode: 'STEREO_EXPAND_300',
    stereoExpand: 300,
    bitrate: 1411,
    sampleRate: 44100,
    bitDepth: 16,
    artwork: 'https://images.unsplash.com/photo-1508700115892-45ecd05ae2ad?w=500',
  },
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
  },
  {
    id: 'track-low-1',
    title: 'Midnight Whisper (Data-Saver 64k)',
    artist: 'Lofi Chill Dreamer',
    album: 'Quiet Nights',
    duration: 120.0,
    format: 'aac',
    audioQuality: '64kbps Data-Saver',
    bitrate: 64,
    artwork: 'https://images.unsplash.com/photo-1518609878373-06d740f60d8b?w=500',
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

  const fileMap = {
    'track-expand-1': { file: 'hires.flac', type: 'audio/flac' },
    'track-hires-1': { file: 'hires.flac', type: 'audio/flac' },
    'track-lossless-1': { file: 'lossless.flac', type: 'audio/flac' },
    'track-atmos-1': { file: 'atmos.mp4', type: 'audio/mp4' },
    'track-high-1': { file: 'high.m4a', type: 'audio/mp4' },
    'track-low-1': { file: 'low.m4a', type: 'audio/mp4' },
  };

  const match = fileMap[id];
  if (match) {
    const fixturePath = path.join(__dirname, 'fixtures', match.file);
    if (fs.existsSync(fixturePath)) {
      res.setHeader('Content-Type', match.type);
      return res.sendFile(fixturePath);
    }
  }

  const defaultPath = path.join(__dirname, 'fixtures', 'hires.flac');
  if (fs.existsSync(defaultPath)) {
    res.setHeader('Content-Type', 'audio/flac');
    return res.sendFile(defaultPath);
  }

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

// ── AutoMix Neural DJ Transition Endpoints (https://github.com/clawd139/automix)
app.get('/automix/info', (req, res) => {
  res.json({
    engine: "AutoMix Neural DJ Transition Generation",
    repository: "https://github.com/clawd139/automix",
    version: "2.0.0",
    author: "Clawd",
    license: "MIT",
    stem_names: ["drums", "bass", "vocals", "other"],
    stem_params: ["gain", "eq_low", "eq_mid", "eq_high", "lpf", "hpf"],
    global_params: ["reverb_send", "delay_send", "delay_feedback"],
    total_params: 54,
    features: [
      "stem_separation_demucs",
      "conditional_diffusion_curves",
      "differentiable_mixer",
      "bass_swap",
      "vocal_collision_avoidance",
      "filter_sweep",
      "reverb_washout",
      "delay_throw"
    ]
  });
});

app.get('/automix/status', (req, res) => res.redirect('/automix/info'));

app.all('/automix/plan', (req, res) => {
  const duration = Math.max(parseFloat(req.body?.duration || req.query.duration || 15.0), 1.0);
  const steps = Math.max(parseInt(req.body?.steps || req.query.steps || 50, 10), 2);
  const bpm_a = parseFloat(req.body?.bpm_a || req.query.bpm_a || 120.0);
  const bpm_b = parseFloat(req.body?.bpm_b || req.query.bpm_b || 124.0);
  const style = String(req.body?.style || req.query.style || 'smart');

  const t_step = duration / (steps - 1);
  const frames = [];
  const bass_swap_center = 0.50;
  const bass_swap_width = 0.10;
  const bass_swap_start = bass_swap_center - (bass_swap_width / 2.0);
  const bass_swap_end = bass_swap_center + (bass_swap_width / 2.0);

  for (let i = 0; i < steps; i++) {
    const p = i / (steps - 1);
    const t = +(i * t_step).toFixed(3);
    const gain_a = Math.cos(p * (Math.PI / 2.0));
    const gain_b = Math.sin(p * (Math.PI / 2.0));

    let eq_low_a = 1.0, eq_low_b = 0.0;
    if (p <= bass_swap_start) {
      eq_low_a = 1.0; eq_low_b = 0.0;
    } else if (p >= bass_swap_end) {
      eq_low_a = 0.0; eq_low_b = 1.0;
    } else {
      const swap_p = (p - bass_swap_start) / bass_swap_width;
      eq_low_a = Math.cos(swap_p * (Math.PI / 2.0));
      eq_low_b = Math.sin(swap_p * (Math.PI / 2.0));
    }

    const lpf_a = Math.max(0.2, 1.0 - Math.pow(p, 1.5) * 0.8);
    const hpf_b = Math.max(0.0, Math.pow(1.0 - p, 1.8) * 0.35);

    frames.push({
      step: i,
      progress: +p.toFixed(4),
      time_seconds: t,
      track_a: {
        gain: +gain_a.toFixed(4),
        stems: {
          drums: { gain: +gain_a.toFixed(4), eq_low: +eq_low_a.toFixed(4), eq_mid: 1.0, eq_high: 1.0, lpf: +lpf_a.toFixed(4), hpf: 0.0 },
          bass: { gain: +(gain_a * eq_low_a).toFixed(4), eq_low: +eq_low_a.toFixed(4), eq_mid: 1.0, eq_high: 1.0, lpf: +lpf_a.toFixed(4), hpf: 0.0 },
          vocals: { gain: +gain_a.toFixed(4), eq_low: 1.0, eq_mid: 1.0, eq_high: 1.0, lpf: +lpf_a.toFixed(4), hpf: 0.0 },
          other: { gain: +gain_a.toFixed(4), eq_low: 1.0, eq_mid: 1.0, eq_high: 1.0, lpf: +lpf_a.toFixed(4), hpf: 0.0 }
        },
        reverb_send: p > 0.5 ? +(Math.exp(-Math.pow(p - 0.85, 2) / 0.03) * 0.45).toFixed(4) : 0.0,
        delay_send: (p >= 0.85 && p <= 0.95) ? 0.35 : 0.0
      },
      track_b: {
        gain: +gain_b.toFixed(4),
        stems: {
          drums: { gain: +gain_b.toFixed(4), eq_low: +eq_low_b.toFixed(4), eq_mid: 1.0, eq_high: 1.0, lpf: 1.0, hpf: +hpf_b.toFixed(4) },
          bass: { gain: +(gain_b * eq_low_b).toFixed(4), eq_low: +eq_low_b.toFixed(4), eq_mid: 1.0, eq_high: 1.0, lpf: 1.0, hpf: +hpf_b.toFixed(4) },
          vocals: { gain: +gain_b.toFixed(4), eq_low: 1.0, eq_mid: 1.0, eq_high: 1.0, lpf: 1.0, hpf: +hpf_b.toFixed(4) },
          other: { gain: +gain_b.toFixed(4), eq_low: 1.0, eq_mid: 1.0, eq_high: 1.0, lpf: 1.0, hpf: +hpf_b.toFixed(4) }
        },
        reverb_send: 0.05,
        delay_send: 0.0
      }
    });
  }

  res.json({
    status: "success",
    duration,
    bpm_a,
    bpm_b,
    style,
    steps_count: frames.length,
    bass_swap_point: bass_swap_center,
    frames
  });
});

app.listen(PORT, () => {
  console.log(`[AuraBeat Addon Server] Running on http://localhost:${PORT}`);
  console.log(`[AuraBeat Addon Server] Manifest available at http://localhost:${PORT}/manifest.json`);
});
