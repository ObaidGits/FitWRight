# AI provider and credits — runbook

What to do when something goes wrong with hosted AI. Written for the person on call at
2am, so every section starts with the symptom rather than the subsystem.

Everything here is inert while `AI_CREDITS_ENABLED=false`, which is the default.

---

## Users report "AI features are temporarily unavailable"

That message means **every operator channel is unusable and the user has no key of
their own**. It is deliberately distinct from "not configured" — if you are seeing it,
the problem is ours.

1. Open **Admin → AI ops**. A `channel_error_rate` or `channel_cap_reached` alert names
   the channel.
2. Open **Admin → AI channels**. Look for `cooling down`, `no key`, or `draining`.
3. Press **Test** on each active channel. The result distinguishes the three causes that
   matter:
   - `auth` — the provider rejected the credential. Re-enter the key.
   - `model` — the model name is wrong or was retired by the provider. Fix the model.
   - `rate_limit` — quota or billing at the provider. Their dashboard, not ours.
4. If one channel is healthy, set it `active` and traffic moves immediately.
5. If none are, users with their own key are unaffected. The message already tells
   everyone else they can add one — no action needed for them to keep working.

**Do not** delete a failing channel to "clear" it. Drain it. Deleting an active channel
is refused precisely because in-flight requests are still using it.

---

## A channel is burning money

Symptom: `channel_cap_approaching`, or a number in **Admin → AI spend** you did not
expect.

1. Set a `monthly_cost_cap_cents` on the channel. It is enforced in channel selection,
   so the channel stops taking traffic once month-to-date cost reaches it and the next
   channel in priority order takes over.
2. A cap of `0` means **unlimited**, not "spend nothing". To stop a channel entirely,
   set its state to `disabled`.
3. Caps are anchored to the first of the UTC month, matching provider invoices.

If spend looks wrong rather than high, check `unpriced calls` at the top of the spend
page. Any number above zero means the rate table is missing a model and **the cost shown
is lower than what you were actually billed**. Fix it with `AI_RATE_OVERRIDES`:

```
AI_RATE_OVERRIDES={"gpt-5-nano": [50, 400]}
```

Values are micros (millionths of a currency unit) per 1,000 tokens, prompt then
completion. They affect **your reporting only** — never what a user is charged.

---

## A provider changed its prices

1. Update `AI_RATE_OVERRIDES` with the new numbers.
2. Historic ledger rows keep the cost recorded **at the time of the call**. That is
   intentional: restating history would make yesterday's margin report disagree with
   itself, and reconciliation against an old invoice would fail.
3. If the change makes a channel uneconomic, lower its priority rather than deleting it
   — it remains a working fallback.

---

## A user says they were charged for something that failed

They almost certainly were not, and you can prove it.

1. **Admin → Users → (the user) → AI credits** shows their balance and history.
2. A failed call writes a ledger row with `credits_charged = 0` and `outcome = failed`.
   The row existing with a zero charge is the proof — absence would not be.
3. The hold is released on **any** exception, so a provider outage cannot bill them.
4. If they are genuinely short, grant credits with a reason. The reason lands in the
   credit ledger and the grant lands in the audit trail with your name on it.

---

## A user's credits are stuck

Symptom: their balance shows credits they cannot spend, with no error that explains it.

That is a **reservation that was never released** — a worker died mid-request.

1. **Admin → AI ops → Accounting checks**. `expired_holds_not_swept` above zero
   confirms it.
2. The sweep runs inside the normal admin job cycle. If that number is not zero, the
   job is not running:
   - `SCHEDULER_MODE=internal` — check the process is alive.
   - otherwise — check whatever calls `POST /api/v1/internal/run-jobs`.
3. Triggering one job run releases every expired hold.

Do not edit balances by hand to compensate. The hold is real; releasing it is the fix.

---

## Accounting checks are not zero

Each of these means an invariant broke. Nothing is repaired automatically, because the
evidence of *how* is the only thing that leads to the cause.

| Finding | What it means |
|---|---|
| `expired_holds_not_swept` | The maintenance job is not running (above). |
| `negative_available` | A balance went below zero. Should be impossible — the reserve refuses rather than overdrawing. |
| `negative_component_balances` | An allowance or wallet went negative. Suspect a manual database edit. |
| `settled_above_reserved` | A charge exceeded its hold. The settle caps this, so it should be arithmetically impossible. |
| `spent_more_than_granted` | Lifetime totals disagree with each other. |

Capture the numbers before changing anything.

---

## Everyone suddenly has credits, or nobody does

The allowance is granted **on first touch** and renewed on the first touch after the UTC
month rolls over, keyed `allowance:<user>:<YYYY-MM>`.

- **Nobody has any**: check `AI_CREDITS_ENABLED` and
  `AI_MONTHLY_ALLOWANCE_CREDITS`. A per-user override of `0` beats the global default
  deliberately, so check the user's own limits too.
- **Someone got twice their allowance**: should be impossible — the grant is idempotent
  per user per period. If it happened, the audit trail shows whether a human granted the
  extra.
- The allowance **replaces** rather than accumulates (use it or lose it). Purchased
  credits are additive and never expire.

---

## Rolling back

Migrations `0033`–`0037` are additive and reversible. `alembic downgrade 0032` removes
the whole feature's tables and columns.

Before rolling back, set `AI_CREDITS_ENABLED=false` and let in-flight requests finish.
Dropping the tables while a request holds a reservation will fail that request.

Production runs `DB_AUTO_MIGRATE=false` — migrations are applied by hand, on purpose.

---

## What is deliberately NOT automated

- **No auto-blocking on abuse signals.** Shared-IP is a family, an office, a campus or
  carrier NAT far more often than fraud. **Admin → AI ops** flags accounts for a human;
  every entry states the innocent explanation because it is usually the true one.
- **No auto-remediation of channel alerts.** A provider having a bad minute would
  otherwise take a channel out of rotation.
- **No automatic balance corrections.** See above.
