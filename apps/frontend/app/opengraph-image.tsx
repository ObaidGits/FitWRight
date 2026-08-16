/**
 * Default Open Graph image (App Router metadata route), generated at the edge
 * with `next/og`. Applies site-wide unless a route provides its own
 * `opengraph-image`. The branded card itself lives in `lib/seo/og-image`.
 */
import { renderOgImage } from '@/lib/seo/og-image';

export const runtime = 'edge';
export const alt = 'FitWright - Built to fit.';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

export default function OpengraphImage() {
  return renderOgImage();
}
