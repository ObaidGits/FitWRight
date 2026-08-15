/**
 * /pricing - the public price list.
 *
 * A visitor deciding whether to sign up cannot be asked to sign up to see the price, and
 * before this page there was nowhere on the site that showed one.
 *
 * Rendered on the SERVER and fetched from the public pricing endpoint, for two reasons:
 * a pricing page is exactly what search engines and link previews should be able to read,
 * and the numbers come from the same admin-editable rows that the charge uses - so the
 * page cannot advertise a price the app will not honour. Nothing here is hardcoded.
 *
 * ``revalidate`` keeps it cheap without making a price edit wait for a deploy: an
 * operator's change is live within the window rather than instantly, which is the right
 * trade for a marketing page.
 */
import type { Metadata } from 'next';
import Link from 'next/link';

import { buildMetadata } from '@/lib/seo/metadata';
import { PricingTables } from '@/components/marketing/pricing-tables';
import { PRICING_REVALIDATE_SECONDS, fetchPublicPricing } from '@/lib/api/public-pricing';

export const metadata: Metadata = buildMetadata({
  title: 'Pricing',
  description:
    'Simple credit-based pricing. Start free, pay only for the AI that writes for you. Job searching is always free.',
  path: '/pricing',
});

export const revalidate = PRICING_REVALIDATE_SECONDS;

export default async function PricingPage() {
  const pricing = await fetchPublicPricing();

  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-16 sm:py-24">
      <header className="mx-auto max-w-2xl text-center">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          Pay for the writing, not the searching
        </h1>
        <p className="mt-4 text-lg text-[var(--muted-foreground)]">
          Start free. Credits are only used when AI writes something for you - a tailored resume, a
          cover letter, an answer to an application question. Searching for jobs never costs a
          credit.
        </p>
      </header>

      {pricing ? (
        <PricingTables pricing={pricing} />
      ) : (
        <div className="mt-12 rounded-[var(--radius-at-lg)] border border-[var(--border)] p-8 text-center">
          <p className="text-sm text-[var(--muted-foreground)]">
            Our live prices could not be loaded just now. Create a free account to see them - the
            free tier is enough to tailor your first few resumes.
          </p>
        </div>
      )}

      <section className="mt-16 space-y-6">
        <h2 className="text-center text-2xl font-semibold">Questions people actually ask</h2>
        <dl className="mx-auto grid max-w-3xl gap-6 sm:grid-cols-2">
          <div>
            <dt className="text-sm font-medium">Do unused credits expire?</dt>
            <dd className="mt-1 text-sm text-[var(--muted-foreground)]">
              Credits you buy never expire. The free monthly allowance resets each month.
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium">Am I charged if generation fails?</dt>
            <dd className="mt-1 text-sm text-[var(--muted-foreground)]">
              No. Credits are held before the work starts and released if anything goes wrong, so a
              failure costs you nothing.
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium">Can I use my own AI key instead?</dt>
            <dd className="mt-1 text-sm text-[var(--muted-foreground)]">
              Yes, and it&apos;s free forever if you do - add your own provider key in Settings and
              nothing counts against an allowance. Many providers have a free tier.
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium">Is there a contract?</dt>
            <dd className="mt-1 text-sm text-[var(--muted-foreground)]">
              No. Buy credits when you need them, and stop whenever your search is over.
            </dd>
          </div>
        </dl>
      </section>

      <div className="mt-16 text-center">
        <Link
          href="/signup"
          className="inline-flex items-center justify-center rounded-[var(--radius-at-md)] bg-[var(--primary)] px-6 py-3 text-sm font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90"
        >
          Start free
        </Link>
        <p className="mt-3 text-xs text-[var(--muted-foreground)]">
          No card needed. Need something custom?{' '}
          <Link href="/contact" className="underline">
            Talk to us
          </Link>
          .
        </p>
      </div>
    </main>
  );
}
