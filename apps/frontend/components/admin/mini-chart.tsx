'use client';

/** Dependency-free SVG area chart for admin usage series (keeps bundle small). */
import * as React from 'react';
import type { UsageSeriesPoint } from '@/lib/api/admin';

export function MiniAreaChart({
  data,
  height = 120,
  label = 'Usage trend',
}: {
  data: UsageSeriesPoint[];
  height?: number;
  label?: string;
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
      aria-label={label}
    >
      {/* Accessible title element (R13.6) - screen readers announce this. */}
      <title>{label}</title>
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

/**
 * Accessible usage chart: the SVG area chart + a visually-hidden data-table
 * fallback so assistive tech can read the exact values (R13.6).
 */
export function UsageChart({
  data,
  label,
  valueHeader = 'Value',
}: {
  data: UsageSeriesPoint[];
  label: string;
  valueHeader?: string;
}) {
  const tableId = React.useId();
  return (
    <figure className="m-0" aria-describedby={tableId}>
      <MiniAreaChart data={data} label={label} />
      <figcaption className="sr-only">{label}</figcaption>
      <table id={tableId} className="sr-only">
        <caption>{label}</caption>
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">{valueHeader}</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.date}>
              <th scope="row">{d.date}</th>
              <td>{d.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
