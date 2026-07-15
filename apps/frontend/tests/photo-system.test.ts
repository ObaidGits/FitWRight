import { describe, it, expect } from 'vitest';
import {
  DEFAULT_PHOTO_CONFIG,
  normalizePhotoConfig,
  resolvePhotoUrl,
  resolvedSizePx,
  shapeRadius,
  type PhotoConfig,
} from '@/lib/types/photo';
import { deriveCdnUrl, isCloudinaryUrl, responsiveSrcset, toSrcSetAttr } from '@/lib/cloudinary';
import {
  photoCapability,
  resolvePhotoPosition,
  TEMPLATE_PHOTO_CAPABILITIES,
} from '@/lib/types/template-capabilities';

const CLOUD = 'https://res.cloudinary.com/demo/image/upload/v1/u/abc.webp';
const LOCAL = 'http://localhost:8000/api/v1/media/u/abc.webp';

describe('PhotoConfig', () => {
  it('defaults are hidden + canonical', () => {
    expect(DEFAULT_PHOTO_CONFIG.show).toBe(false);
    expect(DEFAULT_PHOTO_CONFIG.ref).toBe('canonical');
  });

  it('normalize clamps ranges and fills defaults', () => {
    const c = normalizePhotoConfig({
      show: true,
      opacity: 5,
      offsetX: -10,
      offsetY: 300,
      zoom: 99,
    });
    expect(c.opacity).toBe(1);
    expect(c.offsetX).toBe(0);
    expect(c.offsetY).toBe(100);
    expect(c.zoom).toBe(3);
    expect(c.shape).toBe('circle'); // filled from default
  });

  it('resolvedSizePx maps tokens and custom', () => {
    expect(resolvedSizePx({ ...DEFAULT_PHOTO_CONFIG, size: 'xl' })).toBe(160);
    expect(resolvedSizePx({ ...DEFAULT_PHOTO_CONFIG, size: 'custom', customSize: 200 })).toBe(200);
  });

  it('shapeRadius maps shapes', () => {
    expect(shapeRadius({ ...DEFAULT_PHOTO_CONFIG, shape: 'circle' })).toBe('9999px');
    expect(shapeRadius({ ...DEFAULT_PHOTO_CONFIG, shape: 'square' })).toBe('0px');
    expect(shapeRadius({ ...DEFAULT_PHOTO_CONFIG, shape: 'custom', radius: 20 })).toBe('20px');
  });

  it('resolvePhotoUrl honours provenance', () => {
    const hidden: PhotoConfig = { ...DEFAULT_PHOTO_CONFIG, show: false };
    expect(resolvePhotoUrl(hidden, 'http://x/a.webp')).toBeNull();

    const canonical: PhotoConfig = { ...DEFAULT_PHOTO_CONFIG, show: true, ref: 'canonical' };
    expect(resolvePhotoUrl(canonical, 'http://live/new.webp')).toBe('http://live/new.webp');

    const snapshot: PhotoConfig = {
      ...DEFAULT_PHOTO_CONFIG,
      show: true,
      ref: 'snapshot',
      snapshot: { url: 'http://frozen/old.webp' },
    };
    // Even when the live profile photo changed, the snapshot stays frozen.
    expect(resolvePhotoUrl(snapshot, 'http://live/new.webp')).toBe('http://frozen/old.webp');
  });
});

describe('cloudinary derivation', () => {
  it('detects cloudinary urls', () => {
    expect(isCloudinaryUrl(CLOUD)).toBe(true);
    expect(isCloudinaryUrl(LOCAL)).toBe(false);
    expect(isCloudinaryUrl(null)).toBe(false);
  });

  it('injects a transform after /image/upload/', () => {
    const url = deriveCdnUrl(CLOUD, { width: 192, height: 192 })!;
    const seg = url.split('/image/upload/')[1].split('/')[0];
    expect(seg).toContain('w_192');
    expect(seg).toContain('h_192');
    expect(seg).toContain('c_fill');
    expect(seg).toContain('f_auto');
  });

  it('is a no-op for local/external masters', () => {
    expect(deriveCdnUrl(LOCAL, { width: 100 })).toBe(LOCAL);
    expect(deriveCdnUrl(null, { width: 100 })).toBeNull();
  });

  it('builds a responsive srcset', () => {
    const rows = responsiveSrcset(CLOUD, [96, 192]);
    expect(rows.map((r) => r.width)).toEqual([96, 192]);
    expect(toSrcSetAttr(rows)).toContain('96w');
    expect(toSrcSetAttr(rows)).toContain('192w');
  });
});

describe('template photo capabilities', () => {
  it('every template has a descriptor', () => {
    const keys = Object.keys(TEMPLATE_PHOTO_CAPABILITIES);
    expect(keys).toContain('swiss-single');
    expect(keys).toContain('latex');
  });

  it('latex is photo-incapable by convention', () => {
    expect(photoCapability('latex').supportsPhoto).toBe(false);
  });

  it('template-default resolves to the template default slot', () => {
    const cap = photoCapability('swiss-two-column');
    expect(resolvePhotoPosition('template-default', cap)).toBe('sidebar');
  });

  it('disallowed slot falls back to the default', () => {
    const cap = photoCapability('vivid'); // sidebar-only
    expect(resolvePhotoPosition('header-left', cap)).toBe('sidebar');
  });

  it('allowed slot is honoured', () => {
    const cap = photoCapability('swiss-single');
    expect(resolvePhotoPosition('header-left', cap)).toBe('header-left');
  });
});
