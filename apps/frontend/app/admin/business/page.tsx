'use client';

/**
 * Admin > Business - the seller block printed on receipts, and how mail is sent.
 *
 * Two things this screen is careful about:
 *
 * - **It refuses to pretend.** When mail is configured by environment variables the form is
 *   read-only with an explanation, because showing an editable form that a live env var
 *   silently overrides is worse than showing none.
 *
 * - **It never round-trips a password.** The server returns only whether a secret exists.
 *   A blank password field means "leave it alone", which is what makes editing the SMTP host
 *   without re-typing the password safe.
 *
 * GST is optional throughout. While the GSTIN is blank, receipts say "payment receipt" and
 * carry no tax line at all - the correct document for a business below the registration
 * threshold, and the reason the tax fields exist but stay out of the way.
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
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/atelier/select';
import { adminApi, type BusinessSettings } from '@/lib/api/admin';

export default function AdminBusinessPage() {
  const [data, setData] = React.useState<BusinessSettings | null>(null);
  const [failed, setFailed] = React.useState(false);

  const load = React.useCallback(() => {
    setFailed(false);
    adminApi
      .getBusinessSettings()
      .then(setData)
      .catch(() => setFailed(true));
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  if (failed) return <ErrorState title="Could not load business settings" onRetry={load} />;
  if (!data) return <LoadingSkeleton rows={6} />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Business</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          What appears on receipts, and how email is sent.
        </p>
      </div>
      <SellerForm data={data} onSaved={load} />
      <MailForm data={data} onSaved={load} />
    </div>
  );
}

function SellerForm({ data, onSaved }: { data: BusinessSettings; onSaved: () => void }) {
  const { toast } = useToast();
  const [form, setForm] = React.useState({ ...data.seller });
  const [saving, setSaving] = React.useState(false);

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function save() {
    setSaving(true);
    try {
      await adminApi.putSellerSettings({
        business_name: form.business_name,
        address: form.address,
        email: form.email,
        phone: form.phone,
        gstin: form.gstin,
        tax_percent: Number(form.tax_percent) || 0,
        footer_note: form.footer_note,
      });
      toast({ title: 'Receipt details saved', variant: 'success' });
      onSaved();
    } catch (err) {
      // The server rejects a tax percent with no GSTIN; the operator needs its words.
      toast({
        title: err instanceof Error ? err.message : 'Could not save',
        variant: 'error',
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-medium">Receipt details</p>
          <p className="text-xs text-[var(--muted-foreground)]">
            Printed on every receipt a customer downloads.
          </p>
        </div>
        {!data.seller.is_configured && (
          <Badge variant="warning">Not set - receipts will say &ldquo;FitWright&rdquo;</Badge>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="biz-name">Business name</Label>
          <Input
            id="biz-name"
            value={form.business_name}
            onChange={(e) => set('business_name', e.target.value)}
            placeholder="FitWright"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="biz-email">Contact email</Label>
          <Input id="biz-email" value={form.email} onChange={(e) => set('email', e.target.value)} />
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="biz-address">Address</Label>
          <Input
            id="biz-address"
            value={form.address}
            onChange={(e) => set('address', e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="biz-phone">Phone</Label>
          <Input id="biz-phone" value={form.phone} onChange={(e) => set('phone', e.target.value)} />
        </div>
      </div>

      <div className="rounded-[var(--radius-at-md)] border border-[var(--border)] p-4">
        <p className="text-sm font-medium">Tax (optional)</p>
        <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
          Leave the GSTIN blank if you&apos;re not registered yet. Receipts then say &ldquo;payment
          receipt&rdquo; with no tax line - which is the correct document below the registration
          threshold. Add a GSTIN and they become tax invoices.
        </p>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="biz-gstin">GSTIN</Label>
            <Input
              id="biz-gstin"
              value={form.gstin}
              onChange={(e) => set('gstin', e.target.value)}
              placeholder="Not registered"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="biz-tax">Tax percent</Label>
            <Input
              id="biz-tax"
              type="number"
              min={0}
              max={100}
              value={String(form.tax_percent)}
              onChange={(e) => set('tax_percent', Number(e.target.value) as never)}
              disabled={!form.gstin.trim()}
            />
            {!form.gstin.trim() && (
              <p className="text-[11px] text-[var(--muted-foreground)]">
                Add a GSTIN to charge tax.
              </p>
            )}
          </div>
        </div>
        {form.gstin.trim() && Number(form.tax_percent) > 0 && (
          <p className="mt-3 text-xs text-[var(--muted-foreground)]">
            Tax is taken OUT of the price you charge, not added on top - so the total on the receipt
            always matches what the customer actually paid.
          </p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="biz-footer">Footer note</Label>
        <Input
          id="biz-footer"
          value={form.footer_note}
          onChange={(e) => set('footer_note', e.target.value)}
          placeholder="This is a computer-generated receipt."
        />
      </div>

      <Button onClick={save} loading={saving} className="w-fit">
        Save receipt details
      </Button>
    </Card>
  );
}

function MailForm({ data, onSaved }: { data: BusinessSettings; onSaved: () => void }) {
  const { toast } = useToast();
  const [form, setForm] = React.useState({ ...data.mail });
  const [secret, setSecret] = React.useState('');
  const [saving, setSaving] = React.useState(false);
  const [testing, setTesting] = React.useState(false);

  const envManaged = data.mail.source === 'env';

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function save() {
    setSaving(true);
    try {
      await adminApi.putMailSettings({
        provider: form.provider,
        from_email: form.from_email,
        from_name: form.from_name,
        smtp_host: form.smtp_host,
        smtp_port: Number(form.smtp_port) || 587,
        smtp_user: form.smtp_user,
        smtp_use_tls: form.smtp_use_tls,
        // Blank = keep the stored one. The server cannot show it back, so blank must not
        // mean "delete it".
        secret,
        enabled_events: form.enabled_events,
      });
      setSecret('');
      toast({ title: 'Mail settings saved', variant: 'success' });
      onSaved();
    } catch (err) {
      toast({ title: err instanceof Error ? err.message : 'Could not save', variant: 'error' });
    } finally {
      setSaving(false);
    }
  }

  async function test() {
    setTesting(true);
    try {
      const res = await adminApi.testMailSettings();
      toast({
        title: res.delivered ? `Test sent to ${res.to}` : 'Test failed',
        description: res.detail,
        variant: res.delivered ? 'success' : 'error',
      });
    } catch (err) {
      toast({ title: err instanceof Error ? err.message : 'Test failed', variant: 'error' });
    } finally {
      setTesting(false);
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-medium">Email</p>
          <p className="text-xs text-[var(--muted-foreground)]">
            Used for verification links, password resets and purchase receipts.
          </p>
        </div>
        {envManaged ? (
          <Badge variant="neutral">Managed by environment</Badge>
        ) : form.provider ? (
          <Badge variant="success">{form.provider}</Badge>
        ) : (
          <Badge variant="warning">Not configured - emails only log</Badge>
        )}
      </div>

      {/* Read-only rather than hidden: the operator should be able to SEE what is in effect,
          just not edit it here. */}
      {envManaged && (
        <div className="rounded-[var(--radius-at-md)] border border-[var(--at-warning)]/40 bg-[var(--at-warning)]/10 p-3">
          <p className="text-sm font-medium">Mail is configured by environment variables</p>
          <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
            Currently sending via <strong>{data.mail.provider}</strong>
            {data.mail.smtp_host ? ` (${data.mail.smtp_host})` : ''} from{' '}
            <strong>{data.mail.from_email || 'unset'}</strong>. Remove{' '}
            <code className="rounded bg-[var(--secondary)] px-1">EMAIL_PROVIDER</code> from the
            environment to manage it here instead.
          </p>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label>Provider</Label>
          <Select
            value={form.provider || 'none'}
            onValueChange={(v) => set('provider', v === 'none' ? '' : v)}
            disabled={envManaged}
          >
            <SelectTrigger aria-label="Mail provider">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">Not configured (log only)</SelectItem>
              <SelectItem value="smtp">SMTP</SelectItem>
              <SelectItem value="resend">Resend</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="mail-from">From address</Label>
          <Input
            id="mail-from"
            value={form.from_email}
            onChange={(e) => set('from_email', e.target.value)}
            placeholder="noreply@yourdomain.com"
            disabled={envManaged}
          />
        </div>

        {form.provider === 'smtp' && !envManaged && (
          <>
            <div className="space-y-1.5">
              <Label htmlFor="mail-host">SMTP host</Label>
              <Input
                id="mail-host"
                value={form.smtp_host}
                onChange={(e) => set('smtp_host', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mail-port">Port</Label>
              <Input
                id="mail-port"
                type="number"
                value={String(form.smtp_port)}
                onChange={(e) => set('smtp_port', Number(e.target.value) as never)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mail-user">Username</Label>
              <Input
                id="mail-user"
                value={form.smtp_user}
                onChange={(e) => set('smtp_user', e.target.value)}
              />
            </div>
            <div className="flex items-center gap-2 pb-2 pt-6">
              <Switch
                id="mail-tls"
                checked={form.smtp_use_tls}
                onCheckedChange={(v) => set('smtp_use_tls', v)}
              />
              <Label htmlFor="mail-tls" className="text-xs">
                Use TLS
              </Label>
            </div>
          </>
        )}

        {form.provider && !envManaged && (
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="mail-secret">
              {form.provider === 'resend' ? 'API key' : 'Password'}
            </Label>
            <Input
              id="mail-secret"
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              placeholder={data.mail.has_secret ? 'Saved - enter to replace' : 'Enter to set'}
              autoComplete="off"
            />
            <p className="text-[11px] text-[var(--muted-foreground)]">
              Stored encrypted and never shown again. Leave blank to keep the current one.
            </p>
          </div>
        )}
      </div>

      {/* Per-event switches: an operator may want receipts but not welcome mail. */}
      <div className="rounded-[var(--radius-at-md)] border border-[var(--border)] p-4">
        <p className="text-sm font-medium">Which emails to send</p>
        <div className="mt-3 space-y-2">
          {Object.entries(data.mail_events).map(([key, label]) => (
            <div key={key} className="flex items-center justify-between gap-3">
              <Label htmlFor={`ev-${key}`} className="text-sm font-normal">
                {label}
              </Label>
              <Switch
                id={`ev-${key}`}
                checked={form.enabled_events[key] !== false}
                onCheckedChange={(v) => set('enabled_events', { ...form.enabled_events, [key]: v })}
                disabled={envManaged}
              />
            </div>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {!envManaged && (
          <Button onClick={save} loading={saving}>
            Save mail settings
          </Button>
        )}
        {/* Offered even when env-managed: verifying what is actually in effect is exactly
            what an operator wants there. */}
        <Button variant="outline" onClick={test} loading={testing} disabled={!data.mail.provider}>
          Send a test email
        </Button>
      </div>
    </Card>
  );
}
