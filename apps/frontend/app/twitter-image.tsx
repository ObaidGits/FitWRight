/**
 * Twitter card image. Next.js does not automatically reuse `opengraph-image`
 * for `twitter:image`, so this route renders the same branded card to keep the
 * OG and Twitter previews identical.
 */
import { renderOgImage, OG_SIZE, OG_ALT, OG_CONTENT_TYPE } from '@/lib/seo/og-image';

export const runtime = 'edge';
export const alt = OG_ALT;
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;

export default function TwitterImage() {
  return renderOgImage();
}
