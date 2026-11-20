// Cloudflare Workers implementation of AuraBeat / BitChord Addon Auth Server
// Run with Wrangler: npx wrangler dev worker.js or wrangler deploy

import { createBitChordAuth } from './bitchord-addon-auth.mjs';
import manifest from './manifest.json';

// Initialize verifier with your Cloudflare Worker domain(s)
const bitchord = createBitChordAuth({
  hosts: ['myaddon.workers.dev', 'myaddon.example.com', 'localhost']
});

const MEDIA_SECRET = 'aurabeat-worker-secret-key';

function b64url(buffer) {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function hmacSha256(keyStr, dataStr) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(keyStr),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  return crypto.subtle.sign('HMAC', key, enc.encode(dataStr));
}

async function signedMediaUrl(request, id, ttl = 1800) {
  const exp = Math.floor(Date.now() / 1000) + ttl;
  const sig = b64url(await hmacSha256(MEDIA_SECRET, `${id}.${exp}`));
  const url = new URL(request.url);
  return `${url.protocol}//${url.host}/media/${encodeURIComponent(id)}?exp=${exp}&sig=${sig}`;
}

async function mediaAllowed(id, exp, sig) {
  if (!/^\d+$/.test(String(exp)) || Number(exp) < Date.now() / 1000) return false;
  const expectedSig = b64url(await hmacSha256(MEDIA_SECRET, `${id}.${exp}`));
  return expectedSig === sig;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // 1. PUBLIC: Manifest
    if (url.pathname === '/manifest.json') {
      return Response.json(manifest, {
        headers: { 'Access-Control-Allow-Origin': '*' }
      });
    }

    // 2. PUBLIC: Media URL (verified with HMAC signature and expiry)
    if (url.pathname.startsWith('/media/')) {
      const id = decodeURIComponent(url.pathname.replace('/media/', ''));
      const exp = url.searchParams.get('exp');
      const sig = url.searchParams.get('sig');

      if (!await mediaAllowed(id, exp, sig)) {
        return new Response(JSON.stringify({ error: 'forbidden', message: 'URL expired or invalid' }), {
          status: 403,
          headers: { 'Content-Type': 'application/json' }
        });
      }

      return new Response('DEMO_AUDIO_PAYLOAD', {
        headers: {
          'Content-Type': 'audio/flac',
          'Accept-Ranges': 'bytes'
        }
      });
    }

    // 3. PROTECTED: Verify BitChord hardware-signed token & proof
    const denied = await bitchord.guard(request);
    if (denied) return denied;

    // Search
    if (url.pathname === '/search') {
      const q = (url.searchParams.get('q') || '').toLowerCase();
      return Response.json({
        tracks: [
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
            bitDepth: 24
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
            bitrate: 768
          }
        ]
      });
    }

    // Stream
    if (url.pathname.startsWith('/stream/')) {
      const id = url.pathname.replace('/stream/', '');
      const mediaUrl = await signedMediaUrl(request, id);
      return Response.json({
        url: mediaUrl,
        format: 'flac',
        audioQuality: 'FLAC 24-bit / 96 kHz',
        bitrate: 4608,
        sampleRate: 96000,
        bitDepth: 24
      });
    }

    return new Response('Not Found', { status: 404 });
  }
};
