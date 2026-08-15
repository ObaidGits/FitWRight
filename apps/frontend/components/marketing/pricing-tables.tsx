/**
 * The plan cards and per-action price list on the public pricing page.
 *
 * A server component: it receives already-fetched data and renders it, so the pricing page
 * stays crawlable and there is no client-side loading state on the one page a visitor
 * judges the product's cost by.
 *
 * Every number is passed in from the API. Nothing here recomputes a price or an
 * applications estimate, because a marketing page that derives its own figures is exactly
 * how a site ends up advertising a number the product does not charge.
 */

interface Plan {
  id: string;
  label: string;
  price_minor: number;
  currency: string;
  monthly_credits: number;
  search_daily_limit: number | null;
  is_free: boolean;
  description: string | null;
  approx_applications: number;
}

interface Feature {
  feature: string;
  label: string;
  credits: number;
  is_free: boolean;
  description: string | null;
}

function money(minor: number, currency: string): string {
  const symbol = currency === 'INR' ? '₹' : `${currency} `;
  return `${symbol}${(minor / 100).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function PricingTables({
  pricing,
}: {
  pricing: {
    credits_per_application: number;
    plans: Plan[];
    features: Feature[];
  };
}) {
  const charged = pricing.features.filter((f) => !f.is_free);
  const free = pricing.features.filter((f) => f.is_free);

  return (
    <>
      {pricing.plans.length > 0 && (
        <section className="mt-12 grid gap-6 sm:grid-cols-3">
          {pricing.plans.map((plan, index) => {
            // The middle tier is the one most people want; saying so is more useful than
            // making every card look equally weighted.
            const highlighted = !plan.is_free && index === 1;
            return (
              <div
                key={plan.id}
                className={`flex flex-col rounded-[var(--radius-at-lg)] border p-6 ${
                  highlighted
                    ? 'border-[var(--primary)] shadow-[var(--shadow-at-e2)]'
                    : 'border-[var(--border)]'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-base font-medium">{plan.label}</h3>
                  {highlighted && (
                    <span className="rounded-full bg-[var(--primary)]/12 px-2 py-0.5 text-xs font-medium text-[var(--primary)]">
                      Most popular
                    </span>
                  )}
                </div>

                <p className="mt-4 text-3xl font-semibold">
                  {plan.is_free ? 'Free' : money(plan.price_minor, plan.currency)}
                  {!plan.is_free && (
                    <span className="text-sm font-normal text-[var(--muted-foreground)]">
                      /month
                    </span>
                  )}
                </p>

                {plan.description && (
                  <p className="mt-2 text-sm text-[var(--muted-foreground)]">{plan.description}</p>
                )}

                <ul className="mt-6 space-y-2 text-sm">
                  {/* Applications first. "2,000 credits" is not something a job seeker
                      can picture; "about 76 applications" is. */}
                  <li className="font-medium">~{plan.approx_applications} applications a month</li>
                  <li className="text-[var(--muted-foreground)]">
                    {plan.monthly_credits.toLocaleString()} credits
                  </li>
                  <li className="text-[var(--muted-foreground)]">
                    {plan.search_daily_limit === null
                      ? 'Unlimited job searches'
                      : `${plan.search_daily_limit} job searches a day`}
                  </li>
                  <li className="text-[var(--muted-foreground)]">
                    Tailored resumes, cover letters, interview prep
                  </li>
                </ul>
              </div>
            );
          })}
        </section>
      )}

      <section className="mt-16">
        <h2 className="text-center text-2xl font-semibold">What a credit buys</h2>
        <p className="mx-auto mt-2 max-w-2xl text-center text-sm text-[var(--muted-foreground)]">
          One full application - a tailored resume, a cover letter and the answers drafted for you -
          is about {pricing.credits_per_application} credits.
        </p>

        <div className="mx-auto mt-8 max-w-2xl divide-y divide-[var(--border)] rounded-[var(--radius-at-lg)] border border-[var(--border)] px-6">
          {charged.map((f) => (
            <div key={f.feature} className="flex items-start justify-between gap-4 py-3">
              <div className="min-w-0">
                <p className="text-sm">{f.label}</p>
                {f.description && (
                  <p className="text-xs text-[var(--muted-foreground)]">{f.description}</p>
                )}
              </div>
              <p className="shrink-0 text-sm font-medium">{f.credits} credits</p>
            </div>
          ))}
          {free.map((f) => (
            <div key={f.feature} className="flex items-start justify-between gap-4 py-3">
              <div className="min-w-0">
                <p className="text-sm">{f.label}</p>
                {f.description && (
                  <p className="text-xs text-[var(--muted-foreground)]">{f.description}</p>
                )}
              </div>
              <p className="shrink-0 text-sm font-medium text-[var(--at-success)]">Free</p>
            </div>
          ))}
          <div className="flex items-start justify-between gap-4 py-3">
            <div>
              <p className="text-sm">Searching for jobs</p>
              <p className="text-xs text-[var(--muted-foreground)]">
                Across every supported job board
              </p>
            </div>
            <p className="shrink-0 text-sm font-medium text-[var(--at-success)]">Free</p>
          </div>
          <div className="flex items-start justify-between gap-4 py-3">
            <div>
              <p className="text-sm">Autofilling applications</p>
              <p className="text-xs text-[var(--muted-foreground)]">
                Runs in your own browser - you review and submit
              </p>
            </div>
            <p className="shrink-0 text-sm font-medium text-[var(--at-success)]">Free</p>
          </div>
        </div>
      </section>
    </>
  );
}
