/**
 * /resume-tailoring - feature landing page for the product's core, name-defining
 * capability. Thin route shell: metadata + the shared, data-driven
 * <CapabilityLanding> renderer. All content lives in CAPABILITIES data and the
 * keyword cluster in lib/seo/page-keywords (`tailoring`), so it never
 * cannibalizes the broad home page.
 */
import type { Metadata } from 'next';
import { CapabilityLanding } from '@/components/marketing/capability-landing';
import { CAPABILITIES } from '@/components/marketing/capabilities-data';
import { buildMetadata } from '@/lib/seo/metadata';
import { KEYWORDS } from '@/lib/seo/page-keywords';

const CAP = CAPABILITIES['resume-tailoring'];

export const metadata: Metadata = buildMetadata({
  title: CAP.metaTitle,
  description: CAP.metaDescription,
  path: `/${CAP.slug}`,
  keywords: KEYWORDS[CAP.keywordGroup],
  socialTitle: CAP.socialTitle,
});

export default function ResumeTailoringPage() {
  return <CapabilityLanding capability={CAP} />;
}
