/**
 * /cover-letter-generator - feature landing page for FitWright's tailored cover
 * letters. Thin route shell over the shared <CapabilityLanding> renderer.
 */
import type { Metadata } from 'next';
import { CapabilityLanding } from '@/components/marketing/capability-landing';
import { CAPABILITIES } from '@/components/marketing/capabilities-data';
import { buildMetadata } from '@/lib/seo/metadata';
import { KEYWORDS } from '@/lib/seo/page-keywords';

const CAP = CAPABILITIES['cover-letter-generator'];

export const metadata: Metadata = buildMetadata({
  title: CAP.metaTitle,
  description: CAP.metaDescription,
  path: `/${CAP.slug}`,
  keywords: KEYWORDS[CAP.keywordGroup],
  socialTitle: CAP.socialTitle,
});

export default function CoverLetterGeneratorPage() {
  return <CapabilityLanding capability={CAP} />;
}
