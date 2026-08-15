'use client';

/**
 * The paid-plan badge.
 *
 * Derived from ``GET /credits``, which already returns the user's resolved plan, rather
 * than from a new field on the session user. ``SafeUser`` is a deliberately strict
 * whitelist built from explicit fields at every call site, and threading a billing
 * concern through it would touch all of them for something the client can already see.
 *
 * Renders NOTHING for the free tier. A badge that everyone has is decoration, and it
 * would also quietly tell every free user they are on a lesser tier every time they look
 * at the sidebar - which is a worse first impression than no badge at all.
 */

import * as React from 'react';

import { Badge } from '@/components/atelier/badge';
import { getMyCredits, type MyCredits } from '@/lib/api/credits';

export function PlanBadge({ className }: { className?: string }) {
  const [credits, setCredits] = React.useState<MyCredits | null>(null);

  React.useEffect(() => {
    let alive = true;
    getMyCredits()
      .then((d) => alive && setCredits(d))
      // A failed balance read must not render a wrong badge. Silence is correct.
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  if (!credits) return null;

  // Someone on their own key is not on a FitWright plan at all, and is paying their
  // provider rather than us - labelling them "Free" would be both wrong and rude.
  if (credits.mode === 'own_key') {
    return (
      <Badge variant="ai" className={className}>
        Own key
      </Badge>
    );
  }

  const plan = credits.plan;
  if (!plan || plan.is_free) return null;

  return (
    <Badge variant="primary" className={className}>
      {plan.label}
    </Badge>
  );
}
