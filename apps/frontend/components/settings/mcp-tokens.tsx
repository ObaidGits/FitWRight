'use client';

/**
 * Settings > Account > "MCP / API access".
 *
 * Token management for the FitWright MCP server (backend Tasks 1-8):
 * list / create / revoke access tokens. The raw token of a newly created
 * token is shown EXACTLY ONCE - it lives only in this component's memory
 * until the reveal is dismissed, is never written to localStorage or logs,
 * and the refetched list (masked by the backend) can never bring it back.
 *
 * Section visibility: the GET /mcp/tokens response itself. 200 -> shown,
 * 404 (MCP_ENABLED off, whole router unmounted server-side) -> hidden.
 */
import * as React from 'react';

import { Card } from '@/components/atelier/card';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Badge } from '@/components/atelier/badge';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/atelier/dialog';
import { useToast } from '@/components/atelier/toast';
import { useTranslations } from '@/lib/i18n';
import { toMessage } from '@/lib/api/errors';
import type { McpTokenCreated, McpTokenRecord } from '@/lib/api/mcp';
import { useMcpTokens, useCreateMcpToken, useRevokeMcpToken } from '@/features/settings/hooks';

/** Format an ISO timestamp in the active UI language (pattern:
 * cover-letter-preview.tsx), falling back to the raw string if unparseable. */
function formatDate(iso: string | null, locale: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium' }).format(d);
}

export function McpTokensSection() {
  const { t, locale } = useTranslations();
  const tokens = useMcpTokens();
  const create = useCreateMcpToken();
  const revoke = useRevokeMcpToken();
  const { toast } = useToast();

  const [createOpen, setCreateOpen] = React.useState(false);
  const [label, setLabel] = React.useState('');
  // The one-time reveal. In-memory ONLY; cleared on dialog close for good.
  const [created, setCreated] = React.useState<McpTokenCreated | null>(null);
  const [copied, setCopied] = React.useState(false);
  const [revokeTarget, setRevokeTarget] = React.useState<McpTokenRecord | null>(null);

  // Hidden while the probe is in flight (no flash of an empty section) and
  // hidden entirely when MCP is disabled (404) - see useMcpTokens. A non-404
  // probe failure (backend down, 500) also renders nothing: the same
  // silent-degrade convention the rest of the Settings page uses when its
  // config fetches fail (e.g. AiSection falls back to defaults) - a
  // half-loaded Account tab with an error-only card would be worse, and the
  // query is retried/refetched by react-query on the next visit.
  if (tokens.isPending || tokens.isError || !tokens.data?.enabled) return null;

  function resetCreateDialog() {
    setCreateOpen(false);
    setLabel('');
    setCreated(null);
    setCopied(false);
  }

  async function onCreate() {
    const trimmed = label.trim();
    if (!trimmed) return;
    try {
      const res = await create.mutateAsync(trimmed);
      // Keep the dialog open, but swap the form for the one-time reveal.
      setCreated(res);
      setCopied(false);
    } catch (err) {
      toast({
        title: t('settings.mcp.createFailed'),
        description: toMessage(err),
        variant: 'error',
      });
    }
  }

  async function onCopy() {
    if (!created) return;
    // Clipboard API can be absent (insecure context / test env); the token is
    // still selectable text, so a failed copy is not a dead end.
    await navigator.clipboard?.writeText(created.token).catch(() => undefined);
    setCopied(true);
  }

  async function onRevoke() {
    if (!revokeTarget) return;
    try {
      await revoke.mutateAsync(revokeTarget.id);
      setRevokeTarget(null);
      toast({ title: t('settings.mcp.revokedToast'), variant: 'success' });
    } catch (err) {
      toast({
        title: t('settings.mcp.revokeFailed'),
        description: toMessage(err),
        variant: 'error',
      });
    }
  }

  const items = tokens.data.items;

  return (
    <Card className="space-y-4 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium">{t('settings.mcp.title')}</p>
          <p className="text-xs text-[var(--muted-foreground)]">{t('settings.mcp.description')}</p>
        </div>
        <Button
          size="sm"
          onClick={() => {
            resetCreateDialog();
            setCreateOpen(true);
          }}
        >
          {t('settings.mcp.create')}
        </Button>
      </div>

      {items.length === 0 ? (
        <p className="text-sm text-[var(--muted-foreground)]">{t('settings.mcp.empty')}</p>
      ) : (
        <ul className="space-y-2">
          {items.map((tok) => (
            <li
              key={tok.id}
              className="flex items-center justify-between gap-4 rounded-md border border-[var(--border)] px-3 py-2"
            >
              <div className="min-w-0">
                <p className="flex items-center gap-2 truncate text-sm font-medium">
                  {tok.label}
                  {tok.revoked_at && <Badge variant="danger">{t('settings.mcp.revoked')}</Badge>}
                </p>
                <p className="text-xs text-[var(--muted-foreground)]">
                  {t('settings.mcp.created')} {formatDate(tok.created_at, locale)}
                  {' · '}
                  {t('settings.mcp.lastUsed')}{' '}
                  {tok.last_used_at
                    ? formatDate(tok.last_used_at, locale)
                    : t('settings.mcp.never')}
                </p>
              </div>
              {!tok.revoked_at && (
                <Button variant="outline" size="sm" onClick={() => setRevokeTarget(tok)}>
                  {t('settings.mcp.revoke')}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* Create dialog: form first, then the one-time raw-token reveal. */}
      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          if (!open) resetCreateDialog();
        }}
      >
        <DialogContent>
          {created ? (
            <>
              <DialogHeader>
                <DialogTitle>{t('settings.mcp.createdTitle')}</DialogTitle>
                <DialogDescription>{t('settings.mcp.createdWarning')}</DialogDescription>
              </DialogHeader>
              <div className="space-y-3">
                <code className="block break-all rounded bg-[var(--secondary)] px-3 py-2 text-sm">
                  {created.token}
                </code>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={onCopy}>
                    {copied ? t('settings.mcp.copied') : t('settings.mcp.copy')}
                  </Button>
                </div>
              </div>
              <DialogFooter>
                {/* Closing the reveal destroys the only in-memory copy. */}
                <Button onClick={resetCreateDialog} loading={create.isPending}>
                  {t('settings.mcp.done')}
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>{t('settings.mcp.title')}</DialogTitle>
                <DialogDescription>{t('settings.mcp.createDescription')}</DialogDescription>
              </DialogHeader>
              <div className="space-y-1.5">
                <Label htmlFor="mcp-token-label">{t('settings.mcp.nameLabel')}</Label>
                <Input
                  id="mcp-token-label"
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder={t('settings.mcp.namePlaceholder')}
                  autoComplete="off"
                />
              </div>
              <DialogFooter>
                <DialogClose asChild>
                  <Button variant="outline">{t('settings.mcp.cancel')}</Button>
                </DialogClose>
                <Button onClick={onCreate} loading={create.isPending} disabled={!label.trim()}>
                  {t('settings.mcp.createSubmit')}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Revoke confirmation: destructive, so confirmed before DELETE fires. */}
      <Dialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('settings.mcp.revokeTitle')}</DialogTitle>
            <DialogDescription>
              {t('settings.mcp.revokeDescription', { label: revokeTarget?.label ?? '' })}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">{t('settings.mcp.cancel')}</Button>
            </DialogClose>
            <Button variant="destructive" onClick={onRevoke} loading={revoke.isPending}>
              {t('settings.mcp.revokeConfirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
