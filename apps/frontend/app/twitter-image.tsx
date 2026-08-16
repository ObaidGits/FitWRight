/**
 * Twitter card image. Next.js does not automatically reuse `opengraph-image`
 * for `twitter:image`, so this route renders the same branded card to keep the
 * OG and Twitter previews identical.
 */
import { renderOgImage } from '@/lib/seo/og-image';

export const runtime = 'edge';
export const alt = 'FitWright - Built to fit.';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

export default function TwitterImage() {
  return renderOgImage();
}
