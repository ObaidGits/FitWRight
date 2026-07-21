'use client';

import React from 'react';
import { toSrcSetAttr, type SrcsetEntry } from '@/lib/cloudinary';

/**
 * ProfileAvatar - the shared, SEO-/perf-optimized public photo display.
 *
 * Used by every *public* surface (profile page, portfolio) so there is one
 * avatar rendering path. It derives nothing itself - the responsive `srcset` is
 * built server-side from the canonical master (`avatarSrcset`) so no extra bytes
 * are stored. Features: responsive `srcSet` + `sizes`, explicit width/height for
 * CLS reservation, dominant-colour placeholder (no layout jank), `decoding`,
 * and a loading strategy (`priority` -> eager + high fetchpriority for above-the-
 * fold hero; lazy otherwise). Falls back to initials when there is no photo - or
 * if the image fails to load at runtime (transient CDN/404), via `onError`.
 *
 * A client component so the `onError` fallback works; the initial `<img>` (with
 * the server-resolved live URL) still renders in SSR HTML for crawlers/SEO.
 */
export interface ProfileAvatarProps {
  url?: string | null;
  srcset?: SrcsetEntry[] | null;
  /** Rendered box edge in px (square). Reserves layout to prevent CLS. */
  size?: number;
  /** `sizes` attribute; defaults to the fixed pixel box. */
  sizes?: string;
  name?: string | null;
  dominantColor?: string | null;
  /** Above-the-fold hero -> eager load + high fetchpriority. */
  priority?: boolean;
  className?: string;
}

function initials(name?: string | null): string {
  const parts = (name || '').trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() ?? '').join('') || 'FW';
}

export function ProfileAvatar({
  url,
  srcset,
  size = 80,
  sizes,
  name,
  dominantColor,
  priority = false,
  className,
}: ProfileAvatarProps) {
  const [failed, setFailed] = React.useState(false);
  const boxStyle: React.CSSProperties = {
    width: size,
    height: size,
    background: dominantColor || undefined,
  };

  if (!url || failed) {
    return (
      <div
        className={
          className ??
          'flex items-center justify-center overflow-hidden rounded-full bg-[var(--primary)]/12 text-2xl font-semibold text-[var(--primary)]'
        }
        style={boxStyle}
        aria-hidden={!name}
      >
        {initials(name)}
      </div>
    );
  }

  const srcSet = srcset && srcset.length ? toSrcSetAttr(srcset) : undefined;

  return (
    <div
      className={className ?? 'overflow-hidden rounded-full bg-[var(--primary)]/12'}
      style={boxStyle}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- external CDN master; responsive srcset is built server-side, Next/Image proxying adds no value and breaks the public SSR/OG paths. */}
      <img
        src={url}
        srcSet={srcSet}
        sizes={srcSet ? (sizes ?? `${size}px`) : undefined}
        alt={name ? `${name} - profile photo` : 'Profile photo'}
        width={size}
        height={size}
        loading={priority ? 'eager' : 'lazy'}
        // fetchPriority is a valid DOM attribute; React 19 passes it through.
        fetchPriority={priority ? 'high' : 'auto'}
        decoding="async"
        onError={() => setFailed(true)}
        className="h-full w-full object-cover"
        style={{ background: dominantColor || undefined }}
      />
    </div>
  );
}

export default ProfileAvatar;
