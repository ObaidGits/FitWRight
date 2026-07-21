/**
 * Cloudinary derivation helpers (Photo System) - mirrors backend
 * `app/storage/image.py`. Every responsive/optimized variant is a **URL
 * transform** of the one canonical master (no re-upload, original never
 * mutated). For a non-Cloudinary master (local dev provider, external avatar)
 * the master URL is returned unchanged (graceful degradation).
 */

export function isCloudinaryUrl(url?: string | null): boolean {
  return !!url && url.includes('res.cloudinary.com') && url.includes('/image/upload/');
}

export interface DeriveOptions {
  width?: number;
  height?: number;
  crop?: 'fill' | 'fit' | 'thumb' | 'scale' | 'crop';
  gravity?: 'auto' | 'face' | 'faces' | 'center';
  radius?: number | 'max';
  format?: 'auto' | 'webp' | 'jpg' | 'png';
  quality?: 'auto' | number;
  dpr?: number | 'auto';
}

/** Return a transformed delivery URL derived from the canonical master. */
export function deriveCdnUrl(masterUrl?: string | null, opts: DeriveOptions = {}): string | null {
  if (!masterUrl) return masterUrl ?? null;
  if (!isCloudinaryUrl(masterUrl)) return masterUrl;

  const {
    width,
    height,
    crop = 'fill',
    gravity = 'auto',
    radius,
    format = 'auto',
    quality = 'auto',
    dpr,
  } = opts;

  const parts: string[] = [`f_${format}`, `q_${quality}`];
  if (crop) parts.push(`c_${crop}`);
  if (gravity && (crop === 'fill' || crop === 'thumb' || crop === 'crop'))
    parts.push(`g_${gravity}`);
  if (width) parts.push(`w_${Math.round(width)}`);
  if (height) parts.push(`h_${Math.round(height)}`);
  if (dpr) parts.push(`dpr_${dpr}`);
  if (radius !== undefined) parts.push(`r_${radius}`);

  return masterUrl.replace('/image/upload/', `/image/upload/${parts.join(',')}/`);
}

export interface SrcsetEntry {
  url: string;
  width: number;
}

/** Build responsive descriptors from the master (square by default). */
export function responsiveSrcset(
  masterUrl?: string | null,
  widths: number[] = [96, 192, 384, 768],
  square = true
): SrcsetEntry[] {
  return widths.map((w) => ({
    url:
      deriveCdnUrl(masterUrl, {
        width: w,
        height: square ? w : undefined,
        crop: square ? 'fill' : 'fit',
      }) ??
      masterUrl ??
      '',
    width: w,
  }));
}

/** Serialize a srcset list into an `<img srcSet>` string. */
export function toSrcSetAttr(entries: SrcsetEntry[]): string {
  return entries.map((e) => `${e.url} ${e.width}w`).join(', ');
}
