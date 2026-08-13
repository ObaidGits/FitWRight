'use client';

/**
 * Settings (Task 13 / Req 18,19) - Profile - AI Providers & Key - Preferences -
 * Account. Wired to the existing config API. Replaces the legacy settings page.
 */
import * as React from 'react';
import { useQueryClient } from '@tanstack/react-query';
import CheckCircle from 'lucide-react/dist/esm/icons/circle-check';
import XCircle from 'lucide-react/dist/esm/icons/circle-x';

import { Card } from '@/components/atelier/card';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Switch } from '@/components/atelier/misc';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/atelier/tabs';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/atelier/select';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/atelier/dialog';
import { LoadingSkeleton } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { useTheme } from '@/components/theme/theme-provider';
import { useSession } from '@/lib/context/session';
import {
  PROVIDER_INFO,
  llmProviderToKeyProvider,
  type LLMProvider,
  type LLMHealthCheck,
  type ReasoningEffort,
  type SupportedLanguage,
} from '@/lib/api/config';
import { resetDatabase } from '@/lib/api/config';
import { invalidateApplicationLists, invalidateResumeLists, queryKeys } from '@/lib/query/client';
import { SINGLE_USER_MODE } from '@/lib/config/auth';
import { AccountSecurity } from '@/components/settings/account-security';
import { updateProfile } from '@/lib/api/auth';
import { describeAuthError } from '@/components/auth/error-banner';
import { ProfileSettings } from '@/components/settings/profile-settings';
import { NotificationPreferences } from '@/components/settings/notification-preferences';
import { FeaturePromptsEditor } from '@/components/settings/feature-prompts-editor';
import {
  useLlmConfig,
  useApiKeyStatus,
  useFeatureConfig,
  useLanguageConfig,
  useUpdateLlmConfig,
  useUpdateApiKeys,
  useUpdateFeatureConfig,
  useUpdateLanguageConfig,
  useTestConnection,
} from '@/features/settings/hooks';

const LANGS: { value: SupportedLanguage; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Español' },
  { value: 'zh', label: '中文' },
  { value: 'ja', label: '日本語' },
  { value: 'pt', label: 'Português' },
  { value: 'fr', label: 'Français' },
];

export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <Tabs defaultValue="ai">
        <TabsList>
          <TabsTrigger value="profile">Profile</TabsTrigger>
          <TabsTrigger value="ai">AI Provider</TabsTrigger>
          <TabsTrigger value="prefs">Preferences</TabsTrigger>
          <TabsTrigger value="account">Account</TabsTrigger>
        </TabsList>
        <TabsContent value="profile">
          <div className="space-y-4">
            <ProfileSection />
            <ProfileSettings />
          </div>
        </TabsContent>
        <TabsContent value="ai">
          <AiSection />
        </TabsContent>
        <TabsContent value="prefs">
          <PreferencesSection />
        </TabsContent>
        <TabsContent value="account">
          <AccountSection />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function ProfileSection() {
  const { user, refresh } = useSession();
  const { toast } = useToast();
  const [name, setName] = React.useState(user?.name ?? '');
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    setName(user?.name ?? '');
  }, [user?.name]);

  async function onSave() {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await updateProfile({ name: name.trim() });
      await refresh();
      toast({ title: 'Profile updated', variant: 'success' });
    } catch (err) {
      toast({ title: describeAuthError(err), variant: 'error' });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div className="space-y-1.5">
        <Label htmlFor="pname">Name</Label>
        <Input
          id="pname"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Your name"
        />
      </div>
      <Button onClick={onSave} loading={saving}>
        Save name
      </Button>
    </Card>
  );
}

// Actionable hints for the backend health-check error codes so a failed
// "Test connection" always explains WHY (fixes the old bare "Connection
// failed" with no reason). The backend never sets a bare `error` field - it
// returns error_code / message / error_detail - so we derive text from those.
const HEALTH_ERROR_HINTS: Record<string, string> = {
  api_key_missing: 'No API key is saved for this provider. Enter your key above, then Save.',
  empty_content:
    'The model returned an empty response. Try another model, or check the endpoint/Base URL.',
  duplicate_v1_path: 'The Base URL has a duplicated /v1 segment - remove the extra /v1.',
  not_found_404: 'The endpoint returned 404. Check the Base URL and the model name.',
  html_response:
    'The endpoint returned a web page, not an API response. Check the Base URL (missing /v1?).',
  health_check_failed:
    'The provider rejected the request. Verify the API key, model name, and Base URL.',
};

function stripCodeFence(text?: string): string | undefined {
  if (!text) return undefined;
  const cleaned = text
    .replace(/^```[a-zA-Z]*\n?/, '')
    .replace(/\n?```$/, '')
    .trim();
  return cleaned || undefined;
}

// Build a human-readable reason for a failed connection test from whatever the
// backend provided (fix 5). Prefers an actionable hint for known codes, then
// the server message, then the (secret-scrubbed) detail, then the raw code.
function describeHealthFailure(res: LLMHealthCheck): string {
  if (res.error_code && HEALTH_ERROR_HINTS[res.error_code]) {
    return HEALTH_ERROR_HINTS[res.error_code];
  }
  return (
    res.message ||
    stripCodeFence(res.error_detail) ||
    res.error ||
    res.error_code ||
    'Unknown error. Check the provider, model, API key, and Base URL.'
  );
}

function AiSection() {
  const cfg = useLlmConfig();
  const keyStatus = useApiKeyStatus();
  const update = useUpdateLlmConfig();
  const updateKeys = useUpdateApiKeys();
  const test = useTestConnection();
  const { toast } = useToast();

  const [provider, setProvider] = React.useState<LLMProvider>('openai');
  const [model, setModel] = React.useState('');
  const [apiBase, setApiBase] = React.useState('');
  const [apiKey, setApiKey] = React.useState('');
  // Reasoning effort. Radix <SelectItem> forbids an empty value, so 'default'
  // is the sentinel for "unset" and maps to '' (clear) on save/test.
  const [reasoningEffort, setReasoningEffort] = React.useState<ReasoningEffort | 'default'>(
    'default'
  );

  React.useEffect(() => {
    if (cfg.data) {
      setProvider(cfg.data.provider);
      setModel(cfg.data.model ?? '');
      setApiBase(cfg.data.api_base ?? '');
      setReasoningEffort(cfg.data.reasoning_effort ?? 'default');
    }
  }, [cfg.data]);

  if (cfg.isLoading) return <LoadingSkeleton rows={3} />;

  // Providers that talk to a custom endpoint need a Base URL.
  const needsBase = provider === 'openai_compatible' || provider === 'ollama';
  // Is a key already stored for this provider's key-store name?
  const keyProvider = llmProviderToKeyProvider(provider);
  const savedKey = keyStatus.data?.providers.find((p) => p.provider === keyProvider);

  // Map the 'default' sentinel to '' (the backend's explicit-clear value).
  const reasoningValue: ReasoningEffort | '' = reasoningEffort === 'default' ? '' : reasoningEffort;

  function buildConfig() {
    return {
      provider,
      model,
      reasoning_effort: reasoningValue,
      ...(needsBase ? { api_base: apiBase.trim() || null } : {}),
      ...(apiKey ? { api_key: apiKey } : {}),
    };
  }

  async function onSave() {
    try {
      // 1) provider / model / base URL / reasoning effort (key is NOT persisted
      //    by this endpoint - it lives in the encrypted per-provider store).
      await update.mutateAsync({
        provider,
        model,
        reasoning_effort: reasoningValue,
        ...(needsBase ? { api_base: apiBase.trim() || null } : {}),
      });
      // 2) the API key persists in the encrypted per-provider key store.
      if (apiKey.trim()) {
        await updateKeys.mutateAsync({ [keyProvider]: apiKey.trim() });
      }
      setApiKey('');
      toast({ title: 'AI settings saved', variant: 'success' });
    } catch {
      toast({ title: 'Could not save settings', variant: 'error' });
    }
  }
  async function onTest() {
    try {
      const res = await test.mutateAsync(buildConfig());
      if (res.healthy) {
        if (res.structured_verdict === 'unsupported') {
          toast({
            title: 'Connected, but this model may not work for tailoring',
            description:
              res.structured_message ??
              'It failed to return valid structured output. Try another model.',
            variant: 'error',
          });
        } else if (res.structured_verdict === 'flaky') {
          toast({
            title: 'Connection OK (structured output is a bit flaky)',
            description: 'Resume tailoring may occasionally need a retry on this model.',
            variant: 'info',
          });
        } else {
          toast({
            title: 'Connection OK',
            description: res.warning || undefined,
            variant: 'success',
          });
        }
      } else {
        toast({
          title: 'Connection failed',
          description: describeHealthFailure(res),
          variant: 'error',
        });
      }
    } catch {
      toast({ title: 'Connection test failed', variant: 'error' });
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div className="space-y-1.5">
        <Label>Provider</Label>
        <Select value={provider} onValueChange={(v) => setProvider(v as LLMProvider)}>
          <SelectTrigger aria-label="LLM provider">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(PROVIDER_INFO) as LLMProvider[]).map((p) => (
              <SelectItem key={p} value={p}>
                {PROVIDER_INFO[p].name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {provider === 'openai_compatible' && (
          <p className="text-xs text-[var(--muted-foreground)]">
            Use this for any endpoint that speaks the OpenAI API - self-hosted servers or cloud
            gateways. Set the Base URL below.
          </p>
        )}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="model">Model</Label>
        <Input
          id="model"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder={PROVIDER_INFO[provider]?.defaultModel}
        />
        <p className="text-xs text-[var(--muted-foreground)]">
          Enter the exact model ID from your provider. The greyed text is only an example
          {PROVIDER_INFO[provider]?.defaultModel ? (
            <>
              {' '}
              (e.g. <code>{PROVIDER_INFO[provider].defaultModel}</code>)
            </>
          ) : null}
          , not a saved value - always confirm the current ID in your provider&apos;s docs.
        </p>
      </div>
      {needsBase && (
        <div className="space-y-1.5">
          <Label htmlFor="apibase">Base URL</Label>
          <Input
            id="apibase"
            value={apiBase}
            onChange={(e) => setApiBase(e.target.value)}
            placeholder={
              provider === 'ollama' ? 'http://localhost:11434' : 'https://your-endpoint.com/v1'
            }
            autoComplete="off"
            spellCheck={false}
          />
          <p className="text-xs text-[var(--muted-foreground)]">
            The API base URL for your endpoint (include <code>/v1</code> if required).
          </p>
        </div>
      )}
      <div className="space-y-1.5">
        <Label htmlFor="apikey">API key</Label>
        <Input
          id="apikey"
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={
            savedKey?.configured
              ? `Saved (${savedKey.masked_key ?? '----'}) - enter to replace`
              : PROVIDER_INFO[provider]?.requiresKey
                ? 'Enter API key'
                : 'Optional for this provider'
          }
          autoComplete="off"
        />
        {savedKey?.unreadable ? (
          /* The key was not deleted - it cannot be decrypted, because the
             encryption secret differs from the one that saved it. Saying so is
             the difference between a one-line fix and believing the app loses
             your data. */
          <p className="text-xs text-[var(--at-warning)]">
            A key is stored for this provider but cannot be read, because the encryption secret
            changed since it was saved. This usually means{' '}
            <code className="rounded bg-[var(--secondary)] px-1">APP_ENCRYPTION_KEY</code> differs
            between how you ran FitWright then and now. Enter the key again to fix it.
          </p>
        ) : (
          <p className="text-xs text-[var(--muted-foreground)]">
            {savedKey?.configured
              ? 'A key is saved for this provider. Leave blank to keep it.'
              : 'Stored encrypted. Your key is never shown again after saving.'}
          </p>
        )}
      </div>
      <div className="space-y-1.5">
        <Label>Reasoning effort</Label>
        <Select
          value={reasoningEffort}
          onValueChange={(v) => setReasoningEffort(v as ReasoningEffort | 'default')}
        >
          <SelectTrigger aria-label="Reasoning effort">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="default">Default (let the model decide)</SelectItem>
            <SelectItem value="minimal">Minimal</SelectItem>
            <SelectItem value="low">Low</SelectItem>
            <SelectItem value="medium">Medium</SelectItem>
            <SelectItem value="high">High</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-xs text-[var(--muted-foreground)]">
          Only used by reasoning-capable models (e.g. OpenAI gpt-5, DeepSeek R1). It is safely
          ignored by models that don&apos;t support it. Choose Default to leave it unset.
        </p>
      </div>
      {test.data && (
        <div className="space-y-1.5">
          <div
            className={`flex items-start gap-2 text-sm ${test.data.healthy ? 'text-[var(--at-success)]' : 'text-[var(--destructive)]'}`}
          >
            {test.data.healthy ? (
              <CheckCircle className="mt-0.5 h-4 w-4 shrink-0" />
            ) : (
              <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
            )}
            <span>
              {test.data.healthy ? 'Connected successfully' : describeHealthFailure(test.data)}
            </span>
          </div>
          {/* Structured-output verdict: the signal that predicts whether resume
              tailoring will actually work on this model. Shown only after a
              successful connection. */}
          {test.data.healthy && test.data.structured_verdict && (
            <div
              className={`flex items-start gap-2 text-xs ${
                test.data.structured_verdict === 'unsupported'
                  ? 'text-[var(--destructive)]'
                  : test.data.structured_verdict === 'flaky'
                    ? 'text-[var(--at-warning)]'
                    : test.data.structured_verdict === 'reliable'
                      ? 'text-[var(--at-success)]'
                      : 'text-[var(--muted-foreground)]'
              }`}
            >
              {test.data.structured_verdict === 'unsupported' ? (
                <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              ) : test.data.structured_verdict === 'reliable' ? (
                <CheckCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              ) : null}
              <span>
                {test.data.structured_verdict === 'reliable' &&
                  'Structured output: reliable - resume tailoring should work well.'}
                {test.data.structured_verdict === 'flaky' &&
                  'Structured output: occasionally invalid - tailoring may need a retry now and then.'}
                {test.data.structured_verdict === 'unsupported' &&
                  (test.data.structured_message ??
                    'This model failed to return valid structured output; resume tailoring may fail. Try another model.')}
                {test.data.structured_verdict === 'unknown' &&
                  'Could not verify structured output (a provider error interrupted the check).'}
              </span>
            </div>
          )}
        </div>
      )}
      <div className="flex gap-2">
        <Button onClick={onSave} loading={update.isPending || updateKeys.isPending}>
          Save
        </Button>
        <Button variant="outline" onClick={onTest} loading={test.isPending}>
          Test connection
        </Button>
      </div>
    </Card>
  );
}

function PreferencesSection() {
  const features = useFeatureConfig();
  const lang = useLanguageConfig();
  const updateFeatures = useUpdateFeatureConfig();
  const updateLang = useUpdateLanguageConfig();
  const { theme, toggleTheme } = useTheme();
  const { toast } = useToast();

  if (features.isLoading || lang.isLoading) return <LoadingSkeleton rows={3} />;

  return (
    <div className="space-y-4">
      <Card className="space-y-4 p-6">
        <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Appearance</h2>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Dark mode</p>
            <p className="text-xs text-[var(--muted-foreground)]">
              Switch between light and dark themes.
            </p>
          </div>
          <Switch checked={theme === 'dark'} onCheckedChange={toggleTheme} aria-label="Dark mode" />
        </div>
      </Card>

      <Card className="space-y-4 p-6">
        <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Content language</h2>
        <Select
          value={lang.data?.content_language ?? 'en'}
          onValueChange={async (v) => {
            try {
              await updateLang.mutateAsync({ content_language: v as SupportedLanguage });
              toast({ title: 'Language updated', variant: 'success' });
            } catch {
              toast({ title: 'Could not update language', variant: 'error' });
            }
          }}
        >
          <SelectTrigger aria-label="Content language">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {LANGS.map((l) => (
              <SelectItem key={l.value} value={l.value}>
                {l.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Card>

      <Card className="space-y-4 p-6">
        <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Features</h2>
        {[
          { key: 'enable_cover_letter' as const, label: 'Cover letter generation' },
          { key: 'enable_outreach_message' as const, label: 'Outreach message generation' },
        ].map((f) => (
          <div key={f.key} className="flex items-center justify-between">
            <p className="text-sm font-medium">{f.label}</p>
            <Switch
              checked={Boolean(features.data?.[f.key])}
              onCheckedChange={async (checked) => {
                try {
                  await updateFeatures.mutateAsync({ [f.key]: checked });
                } catch {
                  toast({ title: 'Could not update feature', variant: 'error' });
                }
              }}
              aria-label={f.label}
            />
          </div>
        ))}
      </Card>

      {(features.data?.enable_cover_letter || features.data?.enable_outreach_message) && (
        <FeaturePromptsEditor />
      )}

      <NotificationPreferences />
    </div>
  );
}

function AccountSection() {
  const { toast } = useToast();
  const qc = useQueryClient();
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [resetting, setResetting] = React.useState(false);

  async function onReset() {
    setResetting(true);
    try {
      await resetDatabase();
      // Reset deletes resumes/applications and therefore changes persisted
      // onboarding facts. Clear every affected list/status cache immediately;
      // otherwise a later /home visit can reuse stale "setup complete" data.
      invalidateResumeLists(qc);
      invalidateApplicationLists(qc);
      qc.invalidateQueries({ queryKey: queryKeys.status });
      toast({ title: 'All data reset', variant: 'success' });
      setConfirmOpen(false);
    } catch {
      toast({ title: 'Reset failed', variant: 'error' });
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="space-y-4">
      {!SINGLE_USER_MODE && <AccountSecurity />}

      <Card className="space-y-4 p-6">
        <div>
          <p className="text-sm font-medium">Reset all data</p>
          <p className="text-xs text-[var(--muted-foreground)]">
            Permanently delete all resumes, job descriptions, and generated documents.
          </p>
        </div>
        <Button variant="destructive" onClick={() => setConfirmOpen(true)}>
          Reset everything
        </Button>

        <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Reset everything?</DialogTitle>
              <DialogDescription>
                This permanently deletes all resumes, job descriptions, and generated documents.
                This cannot be undone.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline">Cancel</Button>
              </DialogClose>
              <Button variant="destructive" loading={resetting} onClick={onReset}>
                Reset all data
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </Card>
    </div>
  );
}
