'use client';

/**
 * Local-time display with a UTC tooltip (R13.6).
 *
 * Timestamps from the backend are UTC ISO strings. We render them in the
 * viewer's local timezone (hydration-safe: server renders the raw ISO, the
 * client swaps to local time after mount) with the exact UTC value in the
 * `title` tooltip so there is never ambiguity about what an admin is seeing.
 */
import * as React from 'react';

function formatLocal(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function LocalTime({ iso, className }: { iso?: string | null; className?: string }) {
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  if (!iso) return <span className={className}>—</span>;
  return (
    <time dateTime={iso} title={`${iso} (UTC)`} className={className} suppressHydrationWarning>
      {mounted ? formatLocal(iso) : iso}
    </time>
  );
}

function relativeLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mins = Math.round((Date.now() - d.getTime()) / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}

/** Relative "x ago" with a UTC tooltip, for compact audit/lastActive display.
 *
 * The relative label is computed in an effect (not during render) so the render
 * stays pure — the wall clock (`Date.now()`) is read only inside the effect,
 * which also refreshes the label every minute. Server render shows the raw ISO
 * (hydration-safe), then the client swaps to the relative label after mount.
 */
export function RelativeTime({ iso, className }: { iso?: string | null; className?: string }) {
  const [label, setLabel] = React.useState<string>('');
  React.useEffect(() => {
    if (!iso) return;
    const update = () => setLabel(relativeLabel(iso));
    update();
    const t = setInterval(update, 60_000);
    return () => clearInterval(t);
  }, [iso]);
  if (!iso) return <span className={className}>—</span>;
  return (
    <time dateTime={iso} title={`${iso} (UTC)`} className={className} suppressHydrationWarning>
      {label || iso}
    </time>
  );
}
