'use client';

/**
 * Credit packs — pricing and offers, editable without a redeploy.
 *
 * Prices used to be an environment variable, so a price change or a weekend offer meant
 * a deploy. They now live in the database and are edited here.
 *
 * The one thing this screen is careful about: it always shows what a customer would
 * actually be charged RIGHT NOW, resolved by the same server-side code the buy screen
 * and the payment order use. A discount that has expired shows as expired here, because
 * the price reverts by itself rather than waiting for a job to run.
 *
 * Discounts are entered as a percentage because that is how an operator thinks, but the
 * server converts it once and stores an exact amount. That is deliberate: a stored
 * percentage would be re-multiplied wherever the price is shown or checked, and a
 * one-paisa disagreement between this page and the payment provider's amount check
 * fails a real customer's purchase.
 */
import * as React from 'react';
import Plus from 'lucide-react/dist/esm/icons/plus';
import Tag from 'lucide-react/dist/esm/icons/tag';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';

import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Switch } from '@/components/atelier/misc';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/atelier/dialog';
import { LoadingSkeleton, EmptyState, ErrorState } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { useCreditPacks, useDeleteCreditPack, useSaveCreditPack } from '@/features/admin/hooks';
import type { CreditPackRow } from '@/lib/api/admin';

/** Minor units (paise) to a readable amount. Money is integers everywhere else. */
function money(minor: number, currency = 'INR'): string {
  return `${currency} ${(minor / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function saleState(pack: CreditPackRow): 'none' | 'live' | 'scheduled' | 'expired' {
  if (pack.sale_amount_minor === null) return 'none';
  const now = Date.now();
  const starts = pack.sale_starts_at ? Date.parse(pack.sale_starts_at) : null;
  const ends = pack.sale_ends_at ? Date.parse(pack.sale_ends_at) : null;
  if (starts && now < starts) return 'scheduled';
  if (ends && now > ends) return 'expired';
  return 'live';
}

export default function AdminPacksPage() {
  const { data, isLoading, isError, refetch } = useCreditPacks();
  const [editing, setEditing] = React.useState<CreditPackRow | null>(null);
  const [creating, setCreating] = React.useState(false);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Credit packs</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            What customers can buy, and what they pay. Changes take effect immediately.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" /> Add pack
        </Button>
      </div>

      {isLoading ? (
        <LoadingSkeleton rows={3} />
      ) : isError ? (
        <ErrorState description="Could not load packs." onRetry={() => refetch()} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={Tag}
          title="No packs yet"
          description="Nothing is for sale until you add a pack and switch it on. There are no default prices on purpose — a default price is a guess."
          action={<Button onClick={() => setCreating(true)}>Add pack</Button>}
        />
      ) : (
        <div className="space-y-3">
          {(data ?? []).map((pack) => (
            <PackRow key={pack.id} pack={pack} onEdit={() => setEditing(pack)} />
          ))}
        </div>
      )}

      {(creating || editing) && (
        <PackDialog
          pack={editing}
          open
          onOpenChange={(open) => {
            if (!open) {
              setCreating(false);
              setEditing(null);
            }
          }}
        />
      )}
    </div>
  );
}

function PackRow({ pack, onEdit }: { pack: CreditPackRow; onEdit: () => void }) {
  const save = useSaveCreditPack();
  const del = useDeleteCreditPack();
  const { toast } = useToast();
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const sale = saleState(pack);

  async function toggleActive(next: boolean) {
    try {
      await save.mutateAsync({
        id: pack.id,
        isNew: false,
        input: {
          label: pack.label,
          credits: pack.credits,
          amount_minor: pack.amount_minor,
          active: next,
        },
      });
      toast({ title: next ? 'Pack is on sale' : 'Pack withdrawn', variant: 'success' });
    } catch (err) {
      toast({
        title: 'Could not change the pack',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 font-medium">
            <span className="truncate">{pack.label}</span>
            {pack.active ? (
              <Badge variant="success">on sale</Badge>
            ) : (
              <Badge variant="neutral">off</Badge>
            )}
            {sale === 'live' && <Badge variant="ai">{pack.percent_off}% off</Badge>}
            {sale === 'scheduled' && <Badge variant="warning">offer scheduled</Badge>}
            {/* Named explicitly: the price has already reverted on its own, and an
                operator seeing an old discount listed would otherwise assume it is live. */}
            {sale === 'expired' && <Badge variant="neutral">offer ended</Badge>}
          </p>
          <p className="text-xs text-[var(--muted-foreground)]">
            {pack.credits.toLocaleString()} credits · id {pack.id}
          </p>
          <p className="mt-1 text-sm">
            {sale === 'live' ? (
              <>
                <span className="font-semibold">
                  {money(pack.effective_amount_minor, pack.currency)}
                </span>{' '}
                <span className="text-[var(--muted-foreground)] line-through">
                  {money(pack.amount_minor, pack.currency)}
                </span>
              </>
            ) : (
              <span className="font-semibold">{money(pack.amount_minor, pack.currency)}</span>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Switch
            checked={pack.active}
            onCheckedChange={(v) => void toggleActive(v)}
            aria-label={`Sell ${pack.label}`}
          />
          <Button size="sm" variant="outline" onClick={onEdit}>
            Edit
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="text-[var(--destructive)]"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this pack?</DialogTitle>
            <DialogDescription>
              Past purchases keep their own price and credits, so history stays readable. Switching
              the pack off is usually better — it stops new sales while still explaining the
              purchases that reference it.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              loading={del.isPending}
              onClick={async () => {
                try {
                  await del.mutateAsync(pack.id);
                  toast({ title: 'Pack deleted', variant: 'success' });
                  setConfirmDelete(false);
                } catch (err) {
                  toast({
                    title: 'Could not delete the pack',
                    description: err instanceof Error ? err.message : undefined,
                    variant: 'error',
                  });
                }
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function PackDialog({
  pack,
  open,
  onOpenChange,
}: {
  pack: CreditPackRow | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const save = useSaveCreditPack();
  const { toast } = useToast();
  const isNew = pack === null;

  const [id, setId] = React.useState(pack?.id ?? '');
  const [label, setLabel] = React.useState(pack?.label ?? '');
  const [credits, setCredits] = React.useState(String(pack?.credits ?? ''));
  const [rupees, setRupees] = React.useState(pack ? String(pack.amount_minor / 100) : '');
  const [discount, setDiscount] = React.useState(
    pack && pack.sale_amount_minor !== null ? String(pack.percent_off) : ''
  );
  const [saleLabel, setSaleLabel] = React.useState(pack?.sale_label ?? '');
  const [saleEnds, setSaleEnds] = React.useState(
    pack?.sale_ends_at ? pack.sale_ends_at.slice(0, 10) : ''
  );

  const amountMinor = Math.round((Number(rupees) || 0) * 100);
  const pct = Number(discount) || 0;
  // Preview only. The server recomputes and stores the authoritative figure, so this
  // can never be the number that gets charged.
  const previewMinor = pct > 0 ? Math.round(amountMinor * (1 - pct / 100)) : amountMinor;

  const canSave = id.trim() && label.trim() && Number(credits) > 0 && amountMinor >= 100;

  async function submit() {
    try {
      await save.mutateAsync({
        id: id.trim(),
        isNew,
        input: {
          label: label.trim(),
          credits: Number(credits),
          amount_minor: amountMinor,
          discount_percent: pct > 0 ? pct : null,
          sale_label: pct > 0 && saleLabel.trim() ? saleLabel.trim() : null,
          // End of the chosen day, so an offer "until the 20th" includes the 20th.
          sale_ends_at: pct > 0 && saleEnds ? `${saleEnds}T23:59:59+00:00` : null,
          clear_sale: pct <= 0,
        },
      });
      toast({ title: isNew ? 'Pack created' : 'Pack updated', variant: 'success' });
      onOpenChange(false);
    } catch (err) {
      toast({
        title: 'Could not save the pack',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isNew ? 'Add a credit pack' : `Edit ${pack?.label}`}</DialogTitle>
          <DialogDescription>
            New packs start switched off, so nothing goes on sale until you say so.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {isNew && (
            <div className="space-y-1.5">
              <Label htmlFor="pk-id">Short id</Label>
              <Input
                id="pk-id"
                value={id}
                onChange={(e) => setId(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
                placeholder="starter"
              />
              <p className="text-xs text-[var(--muted-foreground)]">
                Permanent. Receipts reference it, so it cannot be changed later.
              </p>
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="pk-label">Name customers see</Label>
              <Input
                id="pk-label"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Starter pack"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pk-credits">Credits included</Label>
              <Input
                id="pk-credits"
                type="number"
                min={1}
                value={credits}
                onChange={(e) => setCredits(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="pk-price">Regular price (₹)</Label>
            <Input
              id="pk-price"
              type="number"
              min={1}
              step="0.01"
              value={rupees}
              onChange={(e) => setRupees(e.target.value)}
              placeholder="199"
            />
            <p className="text-xs text-[var(--muted-foreground)]">
              Tax-inclusive. A price that changes at checkout reads as a trick.
            </p>
          </div>

          <div className="space-y-3 rounded-[var(--radius-at-md)] border border-[var(--border)] p-3">
            <p className="text-sm font-medium">Discount (optional)</p>
            <div className="grid gap-3 sm:grid-cols-[7rem_1fr]">
              <div className="space-y-1.5">
                <Label htmlFor="pk-discount">% off</Label>
                <Input
                  id="pk-discount"
                  type="number"
                  min={0}
                  max={90}
                  value={discount}
                  onChange={(e) => setDiscount(e.target.value)}
                  placeholder="0"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pk-sale-label">Offer name</Label>
                <Input
                  id="pk-sale-label"
                  value={saleLabel}
                  onChange={(e) => setSaleLabel(e.target.value)}
                  placeholder="Launch offer"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pk-sale-ends">Ends on (optional)</Label>
              <Input
                id="pk-sale-ends"
                type="date"
                value={saleEnds}
                onChange={(e) => setSaleEnds(e.target.value)}
              />
              <p className="text-xs text-[var(--muted-foreground)]">
                The price goes back up by itself when the offer ends. Leave empty to run it until
                you turn it off.
              </p>
            </div>

            {pct > 0 && amountMinor > 0 && (
              <p className="text-sm">
                Customers pay <span className="font-semibold">{money(previewMinor)}</span>{' '}
                <span className="text-[var(--muted-foreground)] line-through">
                  {money(amountMinor)}
                </span>
              </p>
            )}
          </div>
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          <Button loading={save.isPending} disabled={!canSave} onClick={() => void submit()}>
            {isNew ? 'Create pack' : 'Save changes'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
