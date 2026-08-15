'use client';

/**
 * Admin > Pricing - what each action costs, and what each plan gives.
 *
 * Both tables were code and environment variables before, so changing a price or a
 * monthly allowance meant a redeploy. That is the wrong shape for something an operator
 * adjusts on a Tuesday afternoon.
 *
 * Two things this screen deliberately does:
 *
 * - **Shows the resulting user-facing number, not just the input.** A price is entered in
 *   credits, but what the operator actually cares about is "how many applications does
 *   this plan buy?" - so that figure is computed from the same per-action prices the
 *   customer sees and shown beside the raw credits.
 *
 * - **Names unpriced features.** A metered feature with no row here charges a built-in
 *   fallback that no operator can see or edit. Surfacing the gap is the difference between
 *   knowing the price list is incomplete and finding out from a margin report.
 */

import * as React from 'react';

import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Switch } from '@/components/atelier/misc';
import { ErrorState, LoadingSkeleton } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { adminApi, type FeaturePriceRow, type SubscriptionPlanRow } from '@/lib/api/admin';

export default function AdminPricingPage() {
  const [prices, setPrices] = React.useState<FeaturePriceRow[] | null>(null);
  const [unpriced, setUnpriced] = React.useState<string[]>([]);
  const [plans, setPlans] = React.useState<SubscriptionPlanRow[] | null>(null);
  const [failed, setFailed] = React.useState(false);
  const { toast } = useToast();

  const load = React.useCallback(() => {
    setFailed(false);
    Promise.all([adminApi.listFeaturePrices(), adminApi.listPlans()])
      .then(([p, pl]) => {
        setPrices(p.prices);
        setUnpriced(p.unpriced);
        setPlans(pl);
      })
      .catch(() => setFailed(true));
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  if (failed) {
    return <ErrorState title="Could not load pricing" onRetry={load} />;
  }
  if (prices === null || plans === null) return <LoadingSkeleton rows={6} />;

  // The headline figure, derived from the same rows the customer sees so the admin
  // preview cannot drift from the buy screen.
  const bundle = ['resume_tailor', 'cover_letter', 'extension_draft'];
  const perApplication =
    bundle.reduce((sum, f) => {
      const row = prices.find((p) => p.feature === f);
      return sum + (row && row.is_charged ? row.credits : 0);
    }, 0) || 1;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Pricing</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          One application currently costs <strong>{perApplication} credits</strong> (tailored resume
          + cover letter + drafted answer). Changes take effect immediately.
        </p>
      </div>

      {unpriced.length > 0 && (
        <Card className="border-[var(--at-warning)]/40 bg-[var(--at-warning)]/10 p-4">
          <p className="text-sm font-medium">Some features have no price row</p>
          <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
            These charge a built-in fallback that you cannot see or edit here: {unpriced.join(', ')}
            . Run <code>scripts/seed_pricing.py</code> to add them.
          </p>
        </Card>
      )}

      <FeaturePricesTable
        prices={prices}
        perApplication={perApplication}
        onSaved={(msg) => {
          toast({ title: msg, variant: 'success' });
          load();
        }}
        onError={(msg) => toast({ title: msg, variant: 'error' })}
      />

      <PlansTable
        plans={plans}
        perApplication={perApplication}
        onSaved={(msg) => {
          toast({ title: msg, variant: 'success' });
          load();
        }}
        onError={(msg) => toast({ title: msg, variant: 'error' })}
      />
    </div>
  );
}

function FeaturePricesTable({
  prices,
  perApplication,
  onSaved,
  onError,
}: {
  prices: FeaturePriceRow[];
  perApplication: number;
  onSaved: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  return (
    <Card className="space-y-3 p-6">
      <div>
        <p className="text-sm font-medium">What each action costs</p>
        <p className="text-xs text-[var(--muted-foreground)]">
          Turn &ldquo;Charged&rdquo; off to make an action free without losing its price.
        </p>
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {prices.map((row) => (
          <FeaturePriceRowEditor
            key={row.feature}
            row={row}
            perApplication={perApplication}
            onSaved={onSaved}
            onError={onError}
          />
        ))}
      </ul>
    </Card>
  );
}

function FeaturePriceRowEditor({
  row,
  perApplication,
  onSaved,
  onError,
}: {
  row: FeaturePriceRow;
  perApplication: number;
  onSaved: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [credits, setCredits] = React.useState(String(row.credits));
  const [isCharged, setIsCharged] = React.useState(row.is_charged);
  const [saving, setSaving] = React.useState(false);

  const dirty = credits !== String(row.credits) || isCharged !== row.is_charged;

  async function save() {
    setSaving(true);
    try {
      await adminApi.updateFeaturePrice(row.feature, {
        credits: Number(credits),
        is_charged: isCharged,
      });
      onSaved(`${row.label} updated`);
    } catch (err) {
      // The server's own words: it rejects a charged action priced at zero, and a
      // negative price, and the operator needs to know which.
      onError(err instanceof Error ? err.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="flex flex-wrap items-end gap-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{row.label}</p>
        <p className="text-xs text-[var(--muted-foreground)]">
          <code>{row.feature}</code>
          {row.description ? ` · ${row.description}` : ''}
        </p>
      </div>
      <div className="w-24">
        <Label htmlFor={`credits-${row.feature}`} className="text-xs">
          Credits
        </Label>
        <Input
          id={`credits-${row.feature}`}
          type="number"
          min={0}
          value={credits}
          onChange={(e) => setCredits(e.target.value)}
          disabled={!isCharged}
        />
      </div>
      <div className="flex items-center gap-2 pb-2">
        <Switch id={`charged-${row.feature}`} checked={isCharged} onCheckedChange={setIsCharged} />
        <Label htmlFor={`charged-${row.feature}`} className="text-xs">
          Charged
        </Label>
      </div>
      <div className="pb-1">
        {!isCharged ? (
          <Badge variant="success">Free</Badge>
        ) : (
          <span className="text-xs text-[var(--muted-foreground)]">
            {perApplication > 0
              ? `${(Number(credits) / perApplication).toFixed(2)}× an application`
              : ''}
          </span>
        )}
      </div>
      <Button size="sm" onClick={save} loading={saving} disabled={!dirty}>
        Save
      </Button>
    </li>
  );
}

function PlansTable({
  plans,
  perApplication,
  onSaved,
  onError,
}: {
  plans: SubscriptionPlanRow[];
  perApplication: number;
  onSaved: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  return (
    <Card className="space-y-3 p-6">
      <div>
        <p className="text-sm font-medium">Plans</p>
        <p className="text-xs text-[var(--muted-foreground)]">
          Price is per month. Searches are free but capped per day - leave the cap empty for
          unlimited. Exactly one plan can be the default new users land on.
        </p>
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {plans.map((plan) => (
          <PlanRowEditor
            key={plan.id}
            plan={plan}
            perApplication={perApplication}
            onSaved={onSaved}
            onError={onError}
          />
        ))}
      </ul>
    </Card>
  );
}

function PlanRowEditor({
  plan,
  perApplication,
  onSaved,
  onError,
}: {
  plan: SubscriptionPlanRow;
  perApplication: number;
  onSaved: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  // Rupees in the form, paise on the wire. The operator should never type paise.
  const [price, setPrice] = React.useState(String(plan.price_minor / 100));
  const [monthly, setMonthly] = React.useState(String(plan.monthly_credits));
  const [searchLimit, setSearchLimit] = React.useState(
    plan.search_daily_limit === null ? '' : String(plan.search_daily_limit)
  );
  const [active, setActive] = React.useState(plan.active);
  const [saving, setSaving] = React.useState(false);

  const dirty =
    price !== String(plan.price_minor / 100) ||
    monthly !== String(plan.monthly_credits) ||
    searchLimit !== (plan.search_daily_limit === null ? '' : String(plan.search_daily_limit)) ||
    active !== plan.active;

  async function save() {
    setSaving(true);
    try {
      const blank = searchLimit.trim() === '';
      await adminApi.updatePlan(plan.id, {
        price_minor: Math.round(Number(price) * 100),
        monthly_credits: Number(monthly),
        active,
        // An empty box means "uncapped", which needs the explicit flag - `null` alone
        // would be indistinguishable from "leave this field alone".
        ...(blank ? { clear_search_limit: true } : { search_daily_limit: Number(searchLimit) }),
      });
      onSaved(`${plan.label} updated`);
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not save');
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="space-y-3 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">{plan.label}</p>
        <code className="text-xs text-[var(--muted-foreground)]">{plan.id}</code>
        {plan.is_default && <Badge variant="primary">Default for new users</Badge>}
        {!plan.active && <Badge variant="outline">inactive</Badge>}
      </div>
      <div className="flex flex-wrap items-end gap-4">
        <div className="w-28">
          <Label htmlFor={`price-${plan.id}`} className="text-xs">
            Price (₹/mo)
          </Label>
          <Input
            id={`price-${plan.id}`}
            type="number"
            min={0}
            value={price}
            onChange={(e) => setPrice(e.target.value)}
          />
        </div>
        <div className="w-32">
          <Label htmlFor={`monthly-${plan.id}`} className="text-xs">
            Credits/mo
          </Label>
          <Input
            id={`monthly-${plan.id}`}
            type="number"
            min={0}
            value={monthly}
            onChange={(e) => setMonthly(e.target.value)}
          />
        </div>
        <div className="w-32">
          <Label htmlFor={`search-${plan.id}`} className="text-xs">
            Searches/day
          </Label>
          <Input
            id={`search-${plan.id}`}
            type="number"
            min={0}
            placeholder="unlimited"
            value={searchLimit}
            onChange={(e) => setSearchLimit(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-2 pb-2">
          <Switch id={`active-${plan.id}`} checked={active} onCheckedChange={setActive} />
          <Label htmlFor={`active-${plan.id}`} className="text-xs">
            Active
          </Label>
        </div>
        <Button size="sm" onClick={save} loading={saving} disabled={!dirty}>
          Save
        </Button>
      </div>
      {/* What the operator actually cares about, derived from the live prices. */}
      <p className="text-xs text-[var(--muted-foreground)]">
        Buys about{' '}
        <strong>{Math.floor(Number(monthly) / Math.max(1, perApplication))} applications</strong>
        {Number(price) > 0
          ? ` · ₹${(Number(price) / Math.max(1, Math.floor(Number(monthly) / Math.max(1, perApplication)))).toFixed(2)} per application`
          : ''}
      </p>
    </li>
  );
}
