import React from 'react';
import type { PhotoConfig, PhotoPosition } from '@/lib/types/photo';
import {
  normalizePhotoConfig,
  resolvePhotoUrl,
  resolvedSizePx,
  shapeRadius,
} from '@/lib/types/photo';
import { deriveCdnUrl } from '@/lib/cloudinary';
import { photoCapability, resolvePhotoPosition } from '@/lib/types/template-capabilities';
import type { TemplateType } from '@/lib/types/template-settings';
import type { ResumeData } from '@/components/dashboard/resume-component';

export interface ResolvedResumePhoto {
  config: PhotoConfig;
  url: string;
  name: string;
  slot: Exclude<PhotoPosition, 'template-default'>;
}

/**
 * Resolve a resume's photo against a template's capabilities (Photo System).
 *
 * Returns `null` when there's no photo to show: the template is photo-incapable
 * (e.g. LaTeX), the config is hidden, or no URL resolves. Otherwise it returns
 * the normalized config, the provenance-resolved URL, and the effective layout
 * `slot` (config `position` mapped through the template's allowed slots). This
 * is the one place render decisions are computed, so every template stays a
 * thin placement of `<PhotoFrame>`.
 */
export function resolveResumePhoto(
  data: ResumeData,
  template: TemplateType
): ResolvedResumePhoto | null {
  const cap = photoCapability(template);
  if (!cap.supportsPhoto) return null;
  const raw = data.personalInfo?.photo;
  if (!raw || !raw.show) return null;
  const config = normalizePhotoConfig(raw);
  const url = resolvePhotoUrl(config, data.personalInfo?.avatarUrl ?? null);
  if (!url) return null;
  return {
    config,
    url,
    name: data.personalInfo?.name || '',
    slot: resolvePhotoPosition(config.position, cap),
  };
}

/**
 * PhotoFrame — the single, template-agnostic photo renderer (Photo System).
 *
 * Every surface renders the header/sidebar photo through this one component:
 * the resume templates (so preview === PDF, since both mount the same template
 * components), and any future website/portfolio header. All shaping/cropping/
 * repositioning is CSS on the master (object-fit + object-position + scale) so
 * the original is never mutated and no per-shape variant is stored. For a
 * Cloudinary master the `src`/`srcSet` are DPR-aware URL transforms (crisp on
 * retina, small bytes); for a local/external master the URL is used as-is.
 *
 * Server-safe (no hooks/client APIs) so it renders inside the print server
 * component during headless-Chromium PDF export.
 */
export function PhotoFrame({
  url,
  config,
  name,
  className,
}: {
  url: string | null | undefined;
  config: PhotoConfig;
  name?: string;
  className?: string;
}) {
  if (!url || !config.show) return null;

  const size = resolvedSizePx(config);
  const radius = shapeRadius(config);
  const objectFit: React.CSSProperties['objectFit'] =
    config.crop === 'contain' ? 'contain' : config.crop === 'fill' ? 'fill' : 'cover';

  // Cloudinary crop mode aligned with the CSS object-fit semantics.
  const cloudCrop = config.crop === 'contain' ? 'fit' : config.crop === 'fill' ? 'scale' : 'fill';
  const src1x = deriveCdnUrl(url, { width: size, height: size, crop: cloudCrop }) ?? url;
  const src2x = deriveCdnUrl(url, { width: size * 2, height: size * 2, crop: cloudCrop }) ?? url;

  const frameStyle: React.CSSProperties = {
    width: `${size}px`,
    height: `${size}px`,
    borderRadius: radius,
    overflow: 'hidden',
    flex: '0 0 auto',
    margin: config.margin ? `${config.margin}px` : undefined,
    opacity: config.opacity,
    background: config.background || undefined,
    border: config.border ? `${config.borderWidth}px solid ${config.borderColor}` : undefined,
    boxShadow: config.shadow ? '0 2px 8px rgba(0,0,0,0.15)' : undefined,
    // Keep print rendering faithful to the on-screen frame.
    WebkitPrintColorAdjust: 'exact',
    printColorAdjust: 'exact',
  };

  const imgStyle: React.CSSProperties = {
    width: '100%',
    height: '100%',
    objectFit,
    objectPosition: `${config.offsetX}% ${config.offsetY}%`,
    transform: config.zoom && config.zoom !== 1 ? `scale(${config.zoom})` : undefined,
    transformOrigin: `${config.offsetX}% ${config.offsetY}%`,
    display: 'block',
  };

  return (
    <div className={className} style={frameStyle} data-photo-frame="">
      {/* eslint-disable-next-line @next/next/no-img-element -- external CDN master; Next/Image can't serve the print (headless) route and would break PDF parity. */}
      <img
        src={src1x}
        srcSet={`${src1x} 1x, ${src2x} 2x`}
        alt={name ? `${name} profile photo` : 'Profile photo'}
        width={size}
        height={size}
        // Eager + sync decode: a resume has exactly one header photo and the PDF
        // is captured on the `load` event, so the image MUST be present before
        // Playwright snapshots (no missing/blurry photo in export). Also avoids
        // CLS in the on-screen preview.
        loading="eager"
        fetchPriority="high"
        decoding="sync"
        style={imgStyle}
      />
    </div>
  );
}

export default PhotoFrame;
