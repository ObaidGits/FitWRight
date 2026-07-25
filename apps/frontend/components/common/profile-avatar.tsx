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
  // Retry a transient load failure a couple of times before falling back to
  // initials, instead of hiding the photo permanently on the first hiccup (a
  // single CDN 404/timeout used to flip to initials until remount).
  const MAX_RETRIES = 2;
  const [attempt, setAttempt] = React.useState(0);
  const [failed, setFailed] = React.useState(false);

  // A new URL (photo changed) restarts the attempt/fallback state so an updated
  // avatar is always re-tried even if a prior URL had failed on this mount.
  React.useEffect(() => {
    setAttempt(0);
    setFailed(false);
  }, [url]);

  const boxStyle: React.CSSProperties = {
    width: size,
    height: size,
    background: dominantColor || undefined,
  };

  function withRetryParam(u: string, n: number): string {
    const sep = u.includes('?') ? '&' : '?';
    return `${u}${sep}__r=${n}`;
  }

  function onImgError() {
    if (attempt < MAX_RETRIES) {
      const next = attempt + 1;
      // Small backoff; bumping `attempt` reloads with a cache-busting param so
      // a cached failed response isn't reused.
      window.setTimeout(() => setAttempt(next), 300 * next);
    } else {
      setFailed(true);
    }
  }

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

  // On a retry, drop the srcSet and use a single cache-busted src so the reload
  // actually re-fetches (a srcSet candidate would otherwise be reused as-is).
  const isRetry = attempt > 0;
  const displaySrc = isRetry ? withRetryParam(url, attempt) : url;
  const srcSet = !isRetry && srcset && srcset.length ? toSrcSetAttr(srcset) : undefined;

  return (
    <div
      className={className ?? 'overflow-hidden rounded-full bg-[var(--primary)]/12'}
      style={boxStyle}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- external CDN master; responsive srcset is built server-side, Next/Image proxying adds no value and breaks the public SSR/OG paths. */}
      <img
        key={attempt}
        src={displaySrc}
        srcSet={srcSet}
        sizes={srcSet ? (sizes ?? `${size}px`) : undefined}
        alt={name ? `${name} - profile photo` : 'Profile photo'}
        width={size}
        height={size}
        loading={priority ? 'eager' : 'lazy'}
        // fetchPriority is a valid DOM attribute; React 19 passes it through.
        fetchPriority={priority ? 'high' : 'auto'}
        decoding="async"
        onError={onImgError}
        className="h-full w-full object-cover"
        style={{ background: dominantColor || undefined }}
      />
    </div>
  );
}

export default ProfileAvatar;
