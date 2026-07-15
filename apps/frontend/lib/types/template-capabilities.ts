/**
 * Template photo capabilities (Photo System, Phase 7).
 *
 * Templates are **photo-aware but not photo-coupled**: each declares *what it
 * supports* and its sensible defaults; it never hardcodes image layout logic.
 * The shared `<PhotoFrame>` reads these descriptors so a resume's
 * {@link PhotoConfig} (`position: 'template-default'`, etc.) resolves against the
 * active template without editing any template's internals. Adding a future
 * template = add one descriptor here; no photo code changes.
 */
import type { TemplateType } from './template-settings';
import type { PhotoPosition, PhotoShape, PhotoSize } from './photo';

export interface TemplatePhotoCapability {
  /** Whether this template renders a header/sidebar photo at all. */
  supportsPhoto: boolean;
  /** Slot used when the resume's config says `position: 'template-default'`. */
  defaultPosition: Exclude<PhotoPosition, 'template-default'>;
  /** Preferred size token when the template drives the size. */
  preferredSize: PhotoSize;
  /** Preferred shape when the template drives the shape. */
  preferredShape: PhotoShape;
  /** Position slots this template can honour (others fall back to default). */
  allowedPositions: Array<Exclude<PhotoPosition, 'template-default'>>;
  /** Human note for the settings UI. */
  note?: string;
}

const HEADER_ONLY: Array<Exclude<PhotoPosition, 'template-default'>> = [
  'header-left',
  'header-center',
  'header-right',
];

const SIDEBAR_CAPABLE: Array<Exclude<PhotoPosition, 'template-default'>> = [
  'sidebar',
  'header-left',
  'header-center',
  'header-right',
];

export const TEMPLATE_PHOTO_CAPABILITIES: Record<TemplateType, TemplatePhotoCapability> = {
  'swiss-single': {
    supportsPhoto: true,
    defaultPosition: 'header-center',
    preferredSize: 'md',
    preferredShape: 'circle',
    allowedPositions: HEADER_ONLY,
    note: 'Centered header photo above the name.',
  },
  'swiss-two-column': {
    supportsPhoto: true,
    defaultPosition: 'sidebar',
    preferredSize: 'lg',
    preferredShape: 'circle',
    allowedPositions: SIDEBAR_CAPABLE,
    note: 'Photo sits at the top of the sidebar.',
  },
  modern: {
    supportsPhoto: true,
    defaultPosition: 'header-left',
    preferredSize: 'lg',
    preferredShape: 'rounded',
    allowedPositions: HEADER_ONLY,
    note: 'Photo beside the name in the accented header.',
  },
  'modern-two-column': {
    supportsPhoto: true,
    defaultPosition: 'sidebar',
    preferredSize: 'lg',
    preferredShape: 'rounded',
    allowedPositions: ['sidebar'],
  },
  latex: {
    // Academic/print convention: no photo. The config's `show` is respected
    // only where supported; here the template renders its no-photo header.
    supportsPhoto: false,
    defaultPosition: 'header-right',
    preferredSize: 'sm',
    preferredShape: 'square',
    allowedPositions: HEADER_ONLY,
    note: 'LaTeX/academic layout omits photos by convention.',
  },
  clean: {
    supportsPhoto: true,
    defaultPosition: 'header-right',
    preferredSize: 'md',
    preferredShape: 'circle',
    allowedPositions: HEADER_ONLY,
  },
  vivid: {
    supportsPhoto: true,
    defaultPosition: 'sidebar',
    preferredSize: 'lg',
    preferredShape: 'circle',
    allowedPositions: ['sidebar'],
  },
};

export function photoCapability(template: TemplateType): TemplatePhotoCapability {
  return TEMPLATE_PHOTO_CAPABILITIES[template] ?? TEMPLATE_PHOTO_CAPABILITIES['swiss-single'];
}

/** Resolve the effective position slot for a config against a template. */
export function resolvePhotoPosition(
  configPosition: PhotoPosition,
  cap: TemplatePhotoCapability
): Exclude<PhotoPosition, 'template-default'> {
  if (configPosition === 'template-default') return cap.defaultPosition;
  return cap.allowedPositions.includes(configPosition) ? configPosition : cap.defaultPosition;
}
