/**
 * Resume Photo Configuration — frontend contract (Photo System).
 *
 * Mirrors the backend `app/profile/photo.py` 1:1. A resume's photo is described
 * by a structured {@link PhotoConfig} (presentation + provenance), never a
 * boolean and never a copy of the image. The config lives in
 * `resumeData.personalInfo.photo`; the resolved URL lives in
 * `personalInfo.avatarUrl`.
 *
 * Presentation vs. provenance are separated on purpose:
 * - Presentation (shape/size/position/crop/offset/zoom/frame): pure render hints
 *   templates map to their own layout via {@link TemplatePhotoCapability}.
 * - Provenance (`ref` + `snapshot`): `canonical` tracks the live profile photo;
 *   `snapshot` is pinned to the master captured at generation time (immune to a
 *   later profile-photo change).
 */

export type PhotoShape = 'circle' | 'rounded' | 'square' | 'custom';
export type PhotoSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl' | 'custom';
export type PhotoAlign = 'left' | 'center' | 'right';
export type PhotoPosition =
  | 'template-default'
  | 'header-left'
  | 'header-right'
  | 'header-center'
  | 'sidebar'
  | 'floating';
export type PhotoCrop = 'cover' | 'contain' | 'fill';
export type PhotoRef = 'canonical' | 'snapshot';

export interface PhotoSnapshot {
  url?: string | null;
  checksum?: string | null;
  width?: number | null;
  height?: number | null;
}

export interface PhotoConfig {
  show: boolean;
  ref: PhotoRef;
  snapshot: PhotoSnapshot;
  // Presentation
  shape: PhotoShape;
  radius: number; // px, when shape === 'custom'
  size: PhotoSize;
  customSize?: number | null; // px, when size === 'custom'
  align: PhotoAlign;
  position: PhotoPosition;
  crop: PhotoCrop;
  offsetX: number; // object-position % (0-100)
  offsetY: number;
  zoom: number; // >= 1 scales the master up inside the fixed frame
  border: boolean;
  borderWidth: number;
  borderColor: string;
  shadow: boolean;
  background?: string | null;
  opacity: number; // 0-1
  margin: number; // px around the frame
}

/** Size token → rendered edge length in px (must match backend SIZE_PX). */
export const PHOTO_SIZE_PX: Record<Exclude<PhotoSize, 'custom'>, number> = {
  xs: 48,
  sm: 64,
  md: 96,
  lg: 128,
  xl: 160,
};

/** Safe default: hidden, tracking the canonical profile photo, circular, medium. */
export const DEFAULT_PHOTO_CONFIG: PhotoConfig = {
  show: false,
  ref: 'canonical',
  snapshot: {},
  shape: 'circle',
  radius: 12,
  size: 'md',
  customSize: null,
  align: 'left',
  position: 'template-default',
  crop: 'cover',
  offsetX: 50,
  offsetY: 50,
  zoom: 1,
  border: false,
  borderWidth: 2,
  borderColor: '#e5e7eb',
  shadow: false,
  background: null,
  opacity: 1,
  margin: 0,
};

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Normalize a partial/legacy config into a complete, clamped PhotoConfig. */
export function normalizePhotoConfig(input?: Partial<PhotoConfig> | null): PhotoConfig {
  if (!input) return { ...DEFAULT_PHOTO_CONFIG };
  return {
    ...DEFAULT_PHOTO_CONFIG,
    ...input,
    snapshot: { ...DEFAULT_PHOTO_CONFIG.snapshot, ...(input.snapshot ?? {}) },
    offsetX: clamp(input.offsetX ?? 50, 0, 100),
    offsetY: clamp(input.offsetY ?? 50, 0, 100),
    zoom: clamp(input.zoom ?? 1, 1, 3),
    opacity: clamp(input.opacity ?? 1, 0, 1),
  };
}

/** Rendered edge length in px for a config's size token. */
export function resolvedSizePx(config: PhotoConfig): number {
  if (config.size === 'custom' && config.customSize) {
    return clamp(Math.round(config.customSize), 24, 512);
  }
  return PHOTO_SIZE_PX[config.size as Exclude<PhotoSize, 'custom'>] ?? PHOTO_SIZE_PX.md;
}

/**
 * Resolve the URL a resume should render (provenance-aware). Single source of
 * truth mirroring the backend `resolve_photo_url`.
 */
export function resolvePhotoUrl(
  config: PhotoConfig | null | undefined,
  profileAvatarUrl: string | null | undefined
): string | null {
  if (!config || !config.show) return null;
  if (config.ref === 'snapshot') return config.snapshot?.url || profileAvatarUrl || null;
  return profileAvatarUrl || null;
}

/** CSS border-radius for a shape. */
export function shapeRadius(config: PhotoConfig): string {
  switch (config.shape) {
    case 'circle':
      return '9999px';
    case 'square':
      return '0px';
    case 'rounded':
      return '12px';
    case 'custom':
      return `${Math.max(0, config.radius)}px`;
    default:
      return '9999px';
  }
}
