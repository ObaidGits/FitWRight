'use client';

/** Dependency-free SVG area/bar chart for admin usage series (keeps bundle small). */
import * as React from 'react';
import type { UsageSeriesPoint } from '@/lib/api/admin';

export function MiniAreaChart({
  data,
  height = 120,
}: {
  data: UsageSeriesPoint[];
  height?: number;
}) {
  if (data.length === 0) return null;
  const w = 100;
  const max = Math.max(...data.map((d) => d.value), 1);
  const step = w / Math.max(data.length - 1, 1);
  const pts = data.map(
    (d, i) => `${(i * step).toFixed(2)},${(height - (d.value / max) * (height - 8) - 4).toFixed(2)}`
  );
  const line = pts.join(' ');
  const area = `0,${height} ${line} ${w},${height}`;

  return (
    <svg
      viewBox={`0 0 ${w} ${height}`}
      preserveAspectRatio="none"
      className="h-[120px] w-full"
      role="img"
      aria-label="Usage trend"
    >
      <polygon points={area} fill="var(--primary)" opacity="0.12" />
      <polyline
        points={line}
        fill="none"
        stroke="var(--primary)"
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
