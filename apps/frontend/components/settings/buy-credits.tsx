'use client';

/**
 * Buying credits — Razorpay Standard Checkout.
 *
 * The flow, and why each step is where it is:
 *
 * 1. Ask the server for the packs. Prices come from the server, never from this file,
 *    so the figure shown is by construction the figure charged.
 * 2. On click, the server creates the Razorpay order and returns the order id plus the
 *    publishable key. The key is fetched at runtime rather than baked into the build, so
 *    rotating it does not need a rebuild of the Docker image.
 * 3. The modal opens. Razorpay handles the card, UPI or netbanking details — they never
 *    touch this app.
 * 4. On success the handler relays three values to the server, which verifies the
 *    signature with a secret this browser does not have. Credits are granted there, not
 *    here. This component's "success" is a report, not an authorisation.
 *
 * Every failure path is handled explicitly, because a payment screen that goes quiet is
 * one where the customer does not know whether they have been charged: dismissal, a
 * declined payment, a script that will not load, and a verification failure each say
 * something different.
 */
import * as React from 'react';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';

import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { LoadingSkeleton } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { apiPost } from '@/lib/api/client';

const CHECKOUT_SRC = 'https://checkout.razorpay.com/v1/checkout.js';

interface Pack {
  id: string;
  label: string;
  credits: number;
  currency: string;
  amount_minor: number;
  compare_at_minor: number | null;
  on_sale: boolean;
  percent_off: number;
  sale_label: string | null;
  description: string | null;
}

interface RazorpayHandlerResponse {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
}

/** Loaded on demand, once. Pulling a third-party script into every page load costs
 *  every user for a feature few of them use on any given visit. */
function loadCheckoutScript(): Promise<boolean> {
  return new Promise((resolve) => {
    if (typeof window === 'undefined') return resolve(false);
    if ((window as { Razorpay?: unknown }).Razorpay) return resolve(true);

    const existing = document.querySelector<HTMLScriptElement>(`script[src="${CHECKOUT_SRC}"]`);
    if (existing) {
      existing.addEventListener('load', () => resolve(true), { once: true });
      existing.addEventListener('error', () => resolve(false), { once: true });
      return;
    }
    const script = document.createElement('script');
    script.src = CHECKOUT_SRC;
    script.async = true;
    script.onload = () => resolve(true);
    script.onerror = () => resolve(false);
    document.body.appendChild(script);
  });
}

function money(minor: number, currency: string): string {
  const symbol = currency === 'INR' ? '₹' : `${currency} `;
  return `${symbol}${(minor / 100).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;
}

export function BuyCredits({ onPurchased }: { onPurchased?: () => void }) {
  const [packs, setPacks] = React.useState<Pack[] | null>(null);
  const [enabled, setEnabled] = React.useState(false);
  const [busyPack, setBusyPack] = React.useState<string | null>(null);
  const { toast } = useToast();

  React.useEffect(() => {
    let alive = true;
    fetch('/api/v1/credits/packs', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : { enabled: false, packs: [] }))
      .then((d) => {
        if (!alive) return;
        setEnabled(Boolean(d.enabled));
        setPacks(d.packs ?? []);
      })
      .catch(() => alive && setPacks([]));
    return () => {
      alive = false;
    };
  }, []);

  async function buy(pack: Pack) {
    setBusyPack(pack.id);
    try {
      const ready = await loadCheckoutScript();
      if (!ready) {
        // Usually an ad blocker or a locked-down network. Saying so beats a silent
        // no-op, which reads as the button being broken.
        toast({
          title: 'Payment window could not load',
          description:
            'Check whether an ad blocker or your network is blocking checkout.razorpay.com, then try again.',
          variant: 'error',
        });
        return;
      }

      const res = await apiPost('/credits/purchase', { pack_id: pack.id });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        toast({
          title: 'Could not start the payment',
          description: body?.error?.message ?? 'Please try again in a moment.',
          variant: 'error',
        });
        return;
      }
      const order = await res.json();

      const RazorpayCtor = (
        window as unknown as {
          Razorpay: new (o: unknown) => {
            open: () => void;
            on: (e: string, cb: (r: unknown) => void) => void;
          };
        }
      ).Razorpay;
      const checkout = new RazorpayCtor({
        key: order.key_id,
        order_id: order.order_id,
        amount: order.amount_minor,
        currency: order.currency,
        name: 'FitWright',
        description: `${pack.credits} AI credits`,
        // Success is REPORTED here and CONFIRMED on the server. Nothing about this
        // callback is trusted on its own.
        handler: async (response: RazorpayHandlerResponse) => {
          try {
            const verify = await apiPost('/credits/purchase/confirm', {
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
            });
            if (!verify.ok) {
              // The money may well have left their account, so never say "failed".
              // Razorpay's webhook is the backstop and will credit them shortly.
              toast({
                title: 'Payment received, still confirming',
                description:
                  'We could not confirm it immediately. Your credits will appear shortly — no need to pay again.',
                variant: 'info',
              });
              return;
            }
            const result = await verify.json();
            toast({
              title:
                result.status === 'granted' ? `${pack.credits} credits added` : 'Already credited',
              variant: 'success',
            });
            onPurchased?.();
          } catch {
            toast({
              title: 'Payment received, still confirming',
              description: 'Your credits will appear shortly.',
              variant: 'info',
            });
          }
        },
        modal: {
          // Dismissal is not failure. Saying "cancelled" avoids alarming someone who
          // simply changed their mind.
          ondismiss: () => {
            toast({ title: 'Payment cancelled', description: 'Nothing was charged.' });
          },
        },
        theme: { color: '#1d4ed8' },
      });

      checkout.on('payment.failed', (event: unknown) => {
        const reason =
          (event as { error?: { description?: string } })?.error?.description ??
          'The payment did not go through.';
        toast({ title: 'Payment failed', description: reason, variant: 'error' });
      });

      checkout.open();
    } finally {
      setBusyPack(null);
    }
  }

  if (packs === null) return <LoadingSkeleton rows={2} />;
  // Nothing on sale is a normal state, not an error: no packs configured, or purchases
  // switched off. Rendering nothing is correct.
  if (!enabled || packs.length === 0) return null;

  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-center gap-2">
        <Sparkles className="h-5 w-5 text-[var(--at-ai)]" />
        <p className="text-sm font-medium">Top up your credits</p>
      </div>
      <ul className="grid gap-3 sm:grid-cols-3">
        {packs.map((pack) => (
          <li
            key={pack.id}
            className="flex flex-col gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] p-3"
          >
            <div>
              <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                {pack.label}
                {pack.on_sale && <Badge variant="ai">{pack.percent_off}% off</Badge>}
              </p>
              <p className="text-xs text-[var(--muted-foreground)]">
                {pack.credits.toLocaleString()} credits
              </p>
            </div>
            <p className="text-lg font-semibold">
              {money(pack.amount_minor, pack.currency)}
              {pack.compare_at_minor !== null && (
                <span className="ml-2 text-sm font-normal text-[var(--muted-foreground)] line-through">
                  {money(pack.compare_at_minor, pack.currency)}
                </span>
              )}
            </p>
            {pack.sale_label && (
              <p className="text-[11px] text-[var(--at-ai)]">{pack.sale_label}</p>
            )}
            <Button
              size="sm"
              className="mt-auto"
              loading={busyPack === pack.id}
              onClick={() => void buy(pack)}
            >
              Buy
            </Button>
          </li>
        ))}
      </ul>
      <p className="text-[11px] text-[var(--muted-foreground)]">
        Credits you buy never expire. Payments are handled by Razorpay — card details never reach
        FitWright.
      </p>
    </Card>
  );
}
