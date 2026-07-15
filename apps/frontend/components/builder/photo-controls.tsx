'use client';

import React from 'react';
import { RotateCcw } from 'lucide-react';
import { Switch } from '@/components/atelier/misc';
import { cn } from '@/lib/utils';
import {
  type PhotoConfig,
  type PhotoShape,
  type PhotoSize,
  type PhotoPosition,
  type PhotoCrop,
  DEFAULT_PHOTO_CONFIG,
  normalizePhotoConfig,
  resolvePhotoUrl,
} from '@/lib/types/photo';
import type { TemplateType } from '@/lib/types/template-settings';
import { photoCapability } from '@/lib/types/template-capabilities';
import { PhotoFrame } from '@/components/resume/photo-frame';
import { AvatarUploader } from '@/components/profile/avatar-uploader';

const headingCls =
  'mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]';
const chipBase = 'rounded-[var(--radius-at-md)] border px-2.5 py-1.5 text-xs transition-colors';
const chipActive = 'border-[var(--primary)] bg-[var(--accent)] text-[var(--primary)]';
const chipIdle =
  'border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] hover:bg-[var(--accent)]';

const SHAPES: PhotoShape[] = ['circle', 'rounded', 'square'];
const SIZES: PhotoSize[] = ['xs', 'sm', 'md', 'lg', 'xl'];
const CROPS: PhotoCrop[] = ['cover', 'contain', 'fill'];

interface PhotoControlsProps {
  /** The resume's current photo config (from personalInfo.photo). */
  value: PhotoConfig | null | undefined;
  /** Persist a new config (writes back to resume personalInfo.photo). */
  onChange: (config: PhotoConfig) => void;
  /** The user's canonical profile photo URL (personalInfo.avatarUrl / account). */
  profileAvatarUrl: string | null | undefined;
  /** Called after a successful upload/removal so the parent can refresh the URL. */
  onProfileAvatarChange?: (url: string | null) => void;
  /** Active template — drives capability-aware defaults + the live preview. */
  template: TemplateType;
  onError?: (message: string) => void;
}

/**
 * PhotoControls — the per-resume Photo System editor (Phases 5, 6, 16).
 *
 * Manages both the *canonical profile photo* (upload / replace / remove — the
 * one master) and this resume's *presentation + provenance* config (show, shape,
 * size, position, crop, reposition, zoom, frame). The live preview uses the same
 * `<PhotoFrame>` the templates render, so what you set is exactly what prints.
 */
export function PhotoControls({
  value,
  onChange,
  profileAvatarUrl,
  onProfileAvatarChange,
  template,
  onError,
}: PhotoControlsProps) {
  const config = normalizePhotoConfig(value);
  const cap = photoCapability(template);

  const resolvedUrl = resolvePhotoUrl({ ...config, show: true }, profileAvatarUrl);

  // Plain function: `config` is re-derived each render, so memoizing over it
  // would be a no-op (and the React Compiler flags it). The compiler memoizes
  // the surrounding render as appropriate.
  const patch = (p: Partial<PhotoConfig>) => onChange(normalizePhotoConfig({ ...config, ...p }));

  if (!cap.supportsPhoto) {
    return (
      <div className="rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] p-3 text-xs text-[var(--muted-foreground)]">
        The {template} template omits photos by convention. Choose another template to add one.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Upload / replace / remove — the shared canonical-photo uploader. */}
      <div>
        <h4 className={headingCls}>Profile photo</h4>
        <AvatarUploader
          avatarUrl={profileAvatarUrl}
          onUploaded={(result) => {
            onProfileAvatarChange?.(result.avatar_url);
            // First upload turns the photo on for this resume automatically.
            if (!config.show) patch({ show: true });
          }}
          onRemoved={() => onProfileAvatarChange?.(null)}
          onError={(m) => onError?.(m)}
        />
      </div>

      {/* Show on this resume */}
      <label className="flex items-center justify-between">
        <span className="text-sm text-[var(--foreground)]">Show photo on this resume</span>
        <Switch checked={config.show} onCheckedChange={(v) => patch({ show: v })} />
      </label>

      {config.show && resolvedUrl && (
        <>
          {/* Live preview (same PhotoFrame the templates render) */}
          <div className="flex items-center justify-center rounded-[var(--radius-at-md)] border border-[var(--border)] bg-white p-4">
            <PhotoFrame url={resolvedUrl} config={config} name="Preview" />
          </div>

          {/* Provenance */}
          <div>
            <h4 className={headingCls}>Source</h4>
            <div className="flex gap-2">
              {(['canonical', 'snapshot'] as const).map((r) => (
                <button
                  key={r}
                  type="button"
                  onClick={() => patch({ ref: r })}
                  className={cn(chipBase, config.ref === r ? chipActive : chipIdle)}
                >
                  {r === 'canonical' ? 'Track profile photo' : 'Pin current photo'}
                </button>
              ))}
            </div>
            <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
              {config.ref === 'canonical'
                ? 'Updates automatically when you change your profile photo.'
                : 'Frozen to the current photo — future profile changes won’t affect this resume.'}
            </p>
          </div>

          {/* Shape / Size / Position / Crop */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <h4 className={headingCls}>Shape</h4>
              <div className="flex flex-wrap gap-1.5">
                {SHAPES.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => patch({ shape: s })}
                    className={cn(chipBase, config.shape === s ? chipActive : chipIdle)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <h4 className={headingCls}>Size</h4>
              <div className="flex flex-wrap gap-1.5">
                {SIZES.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => patch({ size: s })}
                    className={cn(chipBase, config.size === s ? chipActive : chipIdle)}
                  >
                    {s.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <h4 className={headingCls}>Position</h4>
              <div className="flex flex-wrap gap-1.5">
                {(['template-default', ...cap.allowedPositions] as PhotoPosition[]).map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => patch({ position: p })}
                    className={cn(chipBase, config.position === p ? chipActive : chipIdle)}
                  >
                    {p.replace('template-default', 'auto').replace('header-', '')}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <h4 className={headingCls}>Fit</h4>
              <div className="flex flex-wrap gap-1.5">
                {CROPS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => patch({ crop: c })}
                    className={cn(chipBase, config.crop === c ? chipActive : chipIdle)}
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Reposition + zoom */}
          <div className="space-y-2">
            <h4 className={headingCls}>Reposition & zoom</h4>
            <Slider
              label="Horizontal"
              value={config.offsetX}
              min={0}
              max={100}
              step={1}
              onChange={(v) => patch({ offsetX: v })}
              suffix="%"
            />
            <Slider
              label="Vertical"
              value={config.offsetY}
              min={0}
              max={100}
              step={1}
              onChange={(v) => patch({ offsetY: v })}
              suffix="%"
            />
            <Slider
              label="Zoom"
              value={config.zoom}
              min={1}
              max={3}
              step={0.05}
              onChange={(v) => patch({ zoom: v })}
              suffix="×"
            />
          </div>

          {/* Framing */}
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={config.border} onCheckedChange={(v) => patch({ border: v })} />
              Border
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={config.shadow} onCheckedChange={(v) => patch({ shadow: v })} />
              Shadow
            </label>
            <button
              type="button"
              onClick={() =>
                onChange({
                  ...DEFAULT_PHOTO_CONFIG,
                  show: true,
                  snapshot: config.snapshot,
                  ref: config.ref,
                })
              }
              className={cn(chipBase, chipIdle, 'ml-auto inline-flex items-center gap-1')}
            >
              <RotateCcw size={12} /> Reset
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  suffix,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  suffix?: string;
}) {
  return (
    <label className="block">
      <div className="mb-1 flex items-center justify-between text-xs text-[var(--muted-foreground)]">
        <span>{label}</span>
        <span>
          {Math.round(value * 100) / 100}
          {suffix}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full accent-[var(--primary)]"
        aria-label={label}
      />
    </label>
  );
}

export default PhotoControls;
