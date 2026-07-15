/**
 * Default Open Graph image (App Router metadata route), generated at the edge
 * with `next/og`. Applies site-wide unless a route provides its own
 * `opengraph-image`. The branded card itself lives in `lib/seo/og-image`.
 */
import { renderOgImage, OG_SIZE, OG_ALT, OG_CONTENT_TYPE } from '@/lib/seo/og-image';

export const runtime = 'edge';
export const alt = OG_ALT;
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;

export default function OpengraphImage() {
  return renderOgImage();
}
