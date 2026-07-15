'use client';

/**
 * DegradationBanner (P4 R6.4, R2.4, R9.8).
 *
 * Names the current degradation level (Full / Degraded-AI / Offline-Read-Write /
 * Read-Only / Safe-Mode) with a text + SR label so behavior is always
 * communicated. In Safe-Mode (deploy/API skew) it offers a reload at a safe
 * point (R9.8). Hidden entirely when Full.
 */
import * as React from 'react';
import WifiOff from 'lucide-react/dist/esm/icons/wifi-off';
import AlertTriangle from 'lucide-react/dist/esm/icons/triangle-alert';
import RefreshCw from 'lucide-react/dist/esm/icons/refresh-cw';
import { Button } from '@/components/atelier/button';
import { describeDegradation, type DegradationLevel } from '@/lib/resilience/degradation';

export interface DegradationBannerProps {
  level: DegradationLevel;
  onReload?: () => void;
}

export function DegradationBanner({ level, onReload }: DegradationBannerProps) {
  if (level === 'full') return null;
  const { label, message } = describeDegradation(level);
  const Icon = level === 'offline-read-write' ? WifiOff : AlertTriangle;

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-wrap items-center gap-2 bg-[var(--at-warning)]/15 px-4 py-2 text-xs font-medium text-[var(--at-warning)]"
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="font-semibold">{label}.</span>
      <span className="text-[var(--foreground)]">{message}</span>
      {level === 'safe-mode' && onReload && (
        <Button size="sm" variant="secondary" className="ml-auto" onClick={onReload}>
          <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
          Reload
        </Button>
      )}
    </div>
  );
}
