/**
 * Shared Open Graph / Twitter image renderer.
 *
 * The route files (`app/opengraph-image.tsx`, `app/twitter-image.tsx`) declare
 * their own `runtime`/`size`/`alt` literals (Next.js requires these to be
 * statically analyzable per file) and delegate the actual pixels here so the
 * branded card is defined exactly once.
 */
import { ImageResponse } from 'next/og';
import { SITE_NAME, SITE_TAGLINE } from './config';

export const OG_SIZE = { width: 1200, height: 630 } as const;
export const OG_ALT = `${SITE_NAME} — ${SITE_TAGLINE}`;
export const OG_CONTENT_TYPE = 'image/png';

export function renderOgImage() {
  return new ImageResponse(
    <div
      style={{
        height: '100%',
        width: '100%',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        padding: '80px',
        background: 'linear-gradient(135deg, #0b1120 0%, #111827 55%, #1e293b 100%)',
        color: '#f8fafc',
        fontFamily: 'sans-serif',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 72,
            height: 72,
            borderRadius: 18,
            background: 'linear-gradient(135deg, #6366f1, #22d3ee)',
            fontSize: 34,
            fontWeight: 700,
          }}
        >
          FW
        </div>
        <div style={{ fontSize: 34, fontWeight: 600, letterSpacing: -0.5 }}>{SITE_NAME}</div>
      </div>

      <div
        style={{
          marginTop: 48,
          fontSize: 76,
          fontWeight: 700,
          lineHeight: 1.05,
          letterSpacing: -2,
          maxWidth: 900,
        }}
      >
        AI Resume Builder & Tailor
      </div>

      <div style={{ marginTop: 28, fontSize: 34, color: '#94a3b8', maxWidth: 920 }}>
        Tailor your resume to any job with honest ATS scoring, cover letters, and interview prep.
      </div>

      <div style={{ marginTop: 44, display: 'flex', gap: 16, fontSize: 24, color: '#cbd5e1' }}>
        <span>Open source</span>
        <span style={{ color: '#475569' }}>•</span>
        <span>Privacy-first</span>
        <span style={{ color: '#475569' }}>•</span>
        <span>Bring your own API key</span>
      </div>
    </div>,
    { ...OG_SIZE }
  );
}
