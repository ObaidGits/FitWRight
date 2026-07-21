/**
 * /interview-preparation - feature landing page for FitWright's resume-grounded
 * interview prep. Thin route shell over the shared <CapabilityLanding> renderer.
 */
import type { Metadata } from 'next';
import { CapabilityLanding } from '@/components/marketing/capability-landing';
import { CAPABILITIES } from '@/components/marketing/capabilities-data';
import { buildMetadata } from '@/lib/seo/metadata';
import { KEYWORDS } from '@/lib/seo/page-keywords';

const CAP = CAPABILITIES['interview-preparation'];

export const metadata: Metadata = buildMetadata({
  title: CAP.metaTitle,
  description: CAP.metaDescription,
  path: `/${CAP.slug}`,
  keywords: KEYWORDS[CAP.keywordGroup],
  socialTitle: CAP.socialTitle,
});

export default function InterviewPreparationPage() {
  return <CapabilityLanding capability={CAP} />;
}
