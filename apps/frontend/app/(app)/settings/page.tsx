'use client';

/**
 * Settings (Task 13 / Req 18,19) — Profile · AI Providers & Key · Preferences ·
 * Account. Wired to the existing config API. Replaces the legacy settings page.
 */
import * as React from 'react';
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
  type SupportedLanguage,
} from '@/lib/api/config';
import { resetDatabase } from '@/lib/api/config';
import { SINGLE_USER_MODE } from '@/lib/config/auth';
import { AccountSecurity } from '@/components/settings/account-security';
import { updateProfile } from '@/lib/api/auth';
import { describeAuthError } from '@/components/auth/error-banner';
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
          <ProfileSection />
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
      {SINGLE_USER_MODE ? (
        <p className="text-xs text-[var(--muted-foreground)]">
          Profile &amp; avatar are saved with your account when sign-in is enabled.
        </p>
      ) : (
        <Button onClick={onSave} loading={saving}>
          Save
        </Button>
      )}
    </Card>
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

  React.useEffect(() => {
    if (cfg.data) {
      setProvider(cfg.data.provider);
      setModel(cfg.data.model ?? '');
      setApiBase(cfg.data.api_base ?? '');
    }
  }, [cfg.data]);

  if (cfg.isLoading) return <LoadingSkeleton rows={3} />;

  // Providers that talk to a custom endpoint need a Base URL.
  const needsBase = provider === 'openai_compatible' || provider === 'ollama';
  // Is a key already stored for this provider's key-store name?
  const keyProvider = llmProviderToKeyProvider(provider);
  const savedKey = keyStatus.data?.providers.find((p) => p.provider === keyProvider);

  function buildConfig() {
    return {
      provider,
      model,
      ...(needsBase ? { api_base: apiBase.trim() || null } : {}),
      ...(apiKey ? { api_key: apiKey } : {}),
    };
  }

  async function onSave() {
    try {
      // 1) provider / model / base URL (key is NOT persisted by this endpoint).
      await update.mutateAsync({
        provider,
        model,
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
      toast({
        title: res.healthy ? 'Connection OK' : 'Connection failed',
        description: res.healthy ? undefined : res.error,
        variant: res.healthy ? 'success' : 'error',
      });
    } catch {
      toast({ title: 'Connection test failed', variant: 'error' });
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div className="space-y-1.5">
        <Label>Provider</Label>
        <Select value={provider} onValueChange={(v) => setProvider(v as LLMProvider)}>
          <SelectTrigger>
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
            Use this for any endpoint that speaks the OpenAI API — self-hosted servers or cloud
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
              ? `Saved (${savedKey.masked_key ?? '••••'}) — enter to replace`
              : PROVIDER_INFO[provider]?.requiresKey
                ? 'Enter API key'
                : 'Optional for this provider'
          }
          autoComplete="off"
        />
        <p className="text-xs text-[var(--muted-foreground)]">
          {savedKey?.configured
            ? 'A key is saved for this provider. Leave blank to keep it.'
            : 'Stored encrypted. Your key is never shown again after saving.'}
        </p>
      </div>
      {test.data && (
        <div
          className={`flex items-center gap-2 text-sm ${test.data.healthy ? 'text-[var(--at-success)]' : 'text-[var(--destructive)]'}`}
        >
          {test.data.healthy ? (
            <CheckCircle className="h-4 w-4" />
          ) : (
            <XCircle className="h-4 w-4" />
          )}
          {test.data.healthy ? 'Connected successfully' : 'Could not connect'}
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
          <SelectTrigger>
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
    </div>
  );
}

function AccountSection() {
  const { toast } = useToast();
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [resetting, setResetting] = React.useState(false);

  async function onReset() {
    setResetting(true);
    try {
      await resetDatabase();
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
