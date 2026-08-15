/**
 * The homepage's pricing block: a server component that fetches real prices and hands them
 * to the interactive calculator.
 *
 * Split from the page so the fetch stays out of the page body, and so a pricing failure
 * degrades to a link rather than removing the section entirely - a homepage with no pricing
 * anchor at all reads as "they won't tell me the price".
 */
import Link from 'next/link';

import { PricingCalculator } from '@/components/marketing/pricing-calculator';
import { fetchPublicPricing } from '@/lib/api/public-pricing';

export async function HomePricing() {
  const pricing = await fetchPublicPricing();

  if (!pricing) {
    return (
      <div className="mt-10 text-center">
        <p className="text-sm text-[var(--muted-foreground)]">
          Start free - the free tier covers your first few tailored resumes.
        </p>
        <Link
          href="/pricing"
          className="mt-3 inline-block text-sm font-medium text-[var(--primary)] underline"
        >
          See pricing
        </Link>
      </div>
    );
  }

  return (
    <PricingCalculator
      plans={pricing.plans}
      creditsPerApplication={pricing.credits_per_application}
    />
  );
}
