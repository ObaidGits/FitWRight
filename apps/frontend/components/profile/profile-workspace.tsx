'use client';

/**
 * Profile workspace - the single surface for editing the canonical career
 * document (docs/architecture/PROFILE_SYSTEM_PLAN.md).
 *
 * All edits mutate a local draft; Save persists through a version-CAS PATCH so a
 * concurrent write elsewhere can never be silently clobbered (a conflict prompts
 * a reload rather than overwriting). "Generate resume" projects the profile into
 * a new resume via the backend Projection Engine and deep-links to it. Sections
 * are switched with a segmented control; every field is labelled and keyboard-
 * reachable.
 */
import * as React from 'react';
import { useRouter } from 'next/navigation';
import Plus from 'lucide-react/dist/esm/icons/plus';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Save from 'lucide-react/dist/esm/icons/save';

import Sparkle from 'lucide-react/dist/esm/icons/sparkle';

import { useQuery, useQueryClient } from '@tanstack/react-query';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Input, Textarea } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { TabStrip } from '@/components/atelier/tab-strip';
import { EmptyState, ErrorState, LoadingSkeleton } from '@/components/atelier/states';
import { AiProgress } from '@/components/ai/ai-progress';
import { RESUME_GEN_STAGES, RESUME_GEN_MESSAGES, ESTIMATE_SHORT } from '@/lib/ai-progress-copy';
import { useToast } from '@/components/atelier/toast';
import { AvatarUploader } from '@/components/profile/avatar-uploader';
import { useSession } from '@/lib/context/session';
import { getProfile, type Profile } from '@/lib/api/profile';
import { queryKeys } from '@/lib/query/client';
import { CompletenessCard } from '@/components/profile/completeness-card';
import { AnalyticsCard } from '@/components/profile/analytics-card';
import { ImportDialog } from '@/components/profile/import-dialog';
import { ProfileSearch } from '@/components/profile/profile-search';
import { SyncDialog } from '@/components/profile/sync-dialog';
import { VersionHistory } from '@/components/profile/version-history';
import { ExportMenu } from '@/components/profile/export-menu';
import { ShareDialog } from '@/components/profile/share-dialog';
import { SkillTagInput } from '@/components/profile/skill-tag-input';
import { getPreferredTemplateSettings } from '@/lib/resume/preferred-template';
import {
  useAiSuggest,
  useGenerateResume,
  useProfile,
  useProfileCompleteness,
  useSaveProfile,
} from '@/features/profile/hooks';
import { ProfileConflictError, type ProfileData } from '@/lib/api/professional-profile';

type Section = 'overview' | 'experience' | 'education' | 'projects' | 'skills' | 'ai';

const SECTIONS: { id: Section; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'experience', label: 'Experience' },
  { id: 'education', label: 'Education' },
  { id: 'projects', label: 'Projects' },
  { id: 'skills', label: 'Skills' },
  { id: 'ai', label: 'AI memory' },
];

let uidCounter = 0;
function draftUid(): string {
  uidCounter += 1;
  return `new-${Date.now()}-${uidCounter}`;
}

function linesToList(value: string): string[] {
  return value
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
}

function commaToList(value: string): string[] {
  return value
    .split(',')
    .map((l) => l.trim())
    .filter(Boolean);
}

export function ProfileWorkspace() {
  const { toast } = useToast();
  const router = useRouter();
  const qc = useQueryClient();
  const { refresh: refreshSession } = useSession();
  const profileQuery = useProfile();

  // The profile picture's single source of truth is the ACCOUNT master
  // (users.avatar_url), read via the account-profile query - the same source
  // Settings uses, so it works in both hosted and single-user mode (the session
  // user is synthetic/static in single-user mode and must not drive the photo).
  // It is intentionally NOT part of the versioned career-document draft, so
  // managing the photo never marks the document dirty; every other consumer
  // (generated resumes, public page) resolves the avatar live on the backend.
  const accountProfileQuery = useQuery<Profile>({
    queryKey: queryKeys.profile,
    queryFn: getProfile,
  });
  const avatarUrl = accountProfileQuery.data?.avatar_url ?? null;

  const applyAvatar = React.useCallback(
    (url: string | null) => {
      // Instant optimistic update of the shared account-profile cache, then
      // refresh the session so the nav badge updates too (a no-op in
      // single-user mode, where the nav avatar is intentionally static).
      qc.setQueryData<Profile>(queryKeys.profile, (old) =>
        old ? { ...old, avatar_url: url } : old
      );
      qc.invalidateQueries({ queryKey: queryKeys.profile });
      void refreshSession();
    },
    [qc, refreshSession]
  );
  const completenessQuery = useProfileCompleteness();
  const save = useSaveProfile();
  const generate = useGenerateResume();
  const aiSuggestMutation = useAiSuggest();

  const [section, setSection] = React.useState<Section>('overview');
  const [draft, setDraft] = React.useState<ProfileData | null>(null);
  const [baseVersion, setBaseVersion] = React.useState<number>(1);

  // Seed the draft when the server profile first loads (and after a save/restore
  // bumps the version), without stomping in-progress edits on background refetch.
  const serverVersion = profileQuery.data?.version;
  React.useEffect(() => {
    if (profileQuery.data && baseVersion !== serverVersion) {
      setDraft(structuredClone(profileQuery.data.data));
      setBaseVersion(profileQuery.data.version);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverVersion]);

  // First load.
  React.useEffect(() => {
    if (profileQuery.data && draft === null) {
      setDraft(structuredClone(profileQuery.data.data));
      setBaseVersion(profileQuery.data.version);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileQuery.data]);

  const dirty = React.useMemo(() => {
    if (!draft || !profileQuery.data) return false;
    return JSON.stringify(draft) !== JSON.stringify(profileQuery.data.data);
  }, [draft, profileQuery.data]);

  // Warn before navigating away with unsaved edits.
  React.useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  function patch(update: Partial<ProfileData>) {
    setDraft((prev) => (prev ? { ...prev, ...update } : prev));
  }
  function patchIdentity(update: Partial<ProfileData['identity']>) {
    setDraft((prev) => (prev ? { ...prev, identity: { ...prev.identity, ...update } } : prev));
  }

  async function onSave() {
    if (!draft) return;
    try {
      await save.mutateAsync({ data: draft, baseVersion });
      toast({ title: 'Profile saved', variant: 'success' });
    } catch (err) {
      if (err instanceof ProfileConflictError) {
        toast({
          title: 'Profile changed elsewhere',
          description: 'Reloading the latest version. Re-apply your edits.',
          variant: 'error',
        });
        await profileQuery.refetch();
        if (err.current) {
          setDraft(structuredClone(err.current.data));
          setBaseVersion(err.current.version);
        }
        return;
      }
      toast({
        title: 'Could not save',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function onImproveSummary() {
    try {
      const result = await aiSuggestMutation.mutateAsync({ kind: 'summary' });
      if (typeof result.suggestion === 'string' && result.suggestion) {
        patch({ summary: result.suggestion });
        toast({ title: 'Summary improved', description: 'Review and save.', variant: 'success' });
      } else {
        toast({
          title: 'No suggestion',
          description: result.note ?? undefined,
          variant: 'info',
        });
      }
    } catch (err) {
      toast({
        title: 'Could not get suggestion',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function onImproveBullets(uid: string) {
    try {
      const result = await aiSuggestMutation.mutateAsync({
        kind: 'experience_bullets',
        experienceUid: uid,
      });
      if (Array.isArray(result.suggestion) && result.suggestion.length) {
        setDraft((prev) =>
          prev
            ? {
                ...prev,
                workExperience: prev.workExperience.map((e) =>
                  e.uid === uid ? { ...e, description: result.suggestion as string[] } : e
                ),
              }
            : prev
        );
        toast({ title: 'Bullets improved', description: 'Review and save.', variant: 'success' });
      } else {
        toast({ title: 'No suggestion', description: result.note ?? undefined, variant: 'info' });
      }
    } catch (err) {
      toast({
        title: 'Could not get suggestion',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function onGenerate() {
    if (dirty) {
      toast({ title: 'Save your changes first', variant: 'error' });
      return;
    }
    try {
      const result = await generate.mutateAsync({
        persist: true,
        // Open the generated resume in the user's chosen template (Bug #2).
        template_settings: getPreferredTemplateSettings(),
      });
      toast({ title: 'Resume generated', variant: 'success' });
      if (result.resume_id) router.push(`/resumes/${result.resume_id}`);
    } catch (err) {
      toast({
        title: 'Could not generate resume',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  if (profileQuery.isLoading) {
    return <LoadingSkeleton rows={6} />;
  }
  if (profileQuery.isError || !draft) {
    return (
      <ErrorState
        description="Could not load your profile."
        onRetry={() => profileQuery.refetch()}
      />
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Your profile</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            The single source of truth for your career. Generate tailored resumes from it.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ImportDialog baseVersion={baseVersion} />
          <SyncDialog />
          <VersionHistory />
          <ShareDialog />
          <ExportMenu />
          <Button
            variant="outline"
            onClick={onGenerate}
            loading={generate.isPending}
            disabled={dirty}
          >
            <Sparkles className="h-4 w-4" /> Generate resume
          </Button>
          <Button onClick={onSave} loading={save.isPending} disabled={!dirty}>
            <Save className="h-4 w-4" /> {dirty ? 'Save changes' : 'Saved'}
          </Button>
        </div>
      </header>

      {generate.isPending && (
        <Card className="p-5">
          <AiProgress
            stages={RESUME_GEN_STAGES}
            active
            messages={RESUME_GEN_MESSAGES}
            estimate={ESTIMATE_SHORT}
          />
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_18rem]">
        <div className="space-y-5 lg:order-1">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <TabStrip
              tabs={SECTIONS}
              activeTab={section}
              onTabChange={(id) => setSection(id as Section)}
              aria-label="Profile sections"
            />
            <ProfileSearch
              onNavigate={(s) => {
                const valid: Section[] = [
                  'overview',
                  'experience',
                  'education',
                  'projects',
                  'skills',
                  'ai',
                ];
                setSection((valid.includes(s as Section) ? s : 'overview') as Section);
              }}
            />
          </div>

          {section === 'overview' && (
            <OverviewSection
              draft={draft}
              onIdentity={patchIdentity}
              onPatch={patch}
              onImproveSummary={onImproveSummary}
              improving={aiSuggestMutation.isPending}
              avatarUrl={avatarUrl}
              onAvatarUploaded={(result) => {
                applyAvatar(result.avatar_url);
                toast({
                  title: result.deduplicated ? 'Photo already up to date' : 'Photo updated',
                  variant: 'success',
                });
              }}
              onAvatarRemoved={() => {
                applyAvatar(null);
                toast({ title: 'Photo removed', variant: 'success' });
              }}
              onAvatarError={(message) => toast({ title: message, variant: 'error' })}
            />
          )}
          {section === 'experience' && (
            <ExperienceSection
              draft={draft}
              onPatch={patch}
              onImproveBullets={onImproveBullets}
              improving={aiSuggestMutation.isPending}
              dirty={dirty}
            />
          )}
          {section === 'education' && <EducationSection draft={draft} onPatch={patch} />}
          {section === 'projects' && <ProjectsSection draft={draft} onPatch={patch} />}
          {section === 'skills' && <SkillsSection draft={draft} onPatch={patch} />}
          {section === 'ai' && <AiMemorySection draft={draft} onPatch={patch} />}
        </div>

        <aside className="space-y-4 lg:order-2">
          <AnalyticsCard />
          {completenessQuery.data && (
            <CompletenessCard
              score={completenessQuery.data.score}
              suggestions={completenessQuery.data.suggestions}
            />
          )}
        </aside>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------

function Field({
  label,
  children,
  htmlFor,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

function OverviewSection({
  draft,
  onIdentity,
  onPatch,
  onImproveSummary,
  improving,
  avatarUrl,
  onAvatarUploaded,
  onAvatarRemoved,
  onAvatarError,
}: {
  draft: ProfileData;
  onIdentity: (u: Partial<ProfileData['identity']>) => void;
  onPatch: (u: Partial<ProfileData>) => void;
  onImproveSummary: () => void;
  improving: boolean;
  avatarUrl: string | null;
  onAvatarUploaded: (result: import('@/lib/api/profile').AvatarResult) => void | Promise<void>;
  onAvatarRemoved: () => void | Promise<void>;
  onAvatarError: (message: string) => void;
}) {
  const id = draft.identity;
  return (
    <Card className="space-y-4 p-5">
      {/* Profile photo - the shared canonical-photo uploader (same as Settings +
          the resume builder). Managed at the account level, so it is not part of
          the versioned career document and never marks it dirty. */}
      <div className="space-y-2">
        <Label htmlFor="profile-photo">Profile photo</Label>
        <AvatarUploader
          avatarUrl={avatarUrl}
          onUploaded={onAvatarUploaded}
          onRemoved={onAvatarRemoved}
          onError={onAvatarError}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Full name" htmlFor="p-name">
          <Input
            id="p-name"
            value={id.name}
            onChange={(e) => onIdentity({ name: e.target.value })}
          />
        </Field>
        <Field label="Headline" htmlFor="p-headline">
          <Input
            id="p-headline"
            value={id.headline}
            placeholder="Senior Software Engineer"
            onChange={(e) => onIdentity({ headline: e.target.value })}
          />
        </Field>
        <Field label="Email" htmlFor="p-email">
          <Input
            id="p-email"
            type="email"
            value={id.email}
            onChange={(e) => onIdentity({ email: e.target.value })}
          />
        </Field>
        <Field label="Phone" htmlFor="p-phone">
          <Input
            id="p-phone"
            value={id.phone}
            onChange={(e) => onIdentity({ phone: e.target.value })}
          />
        </Field>
        <Field label="Location" htmlFor="p-location">
          <Input
            id="p-location"
            value={id.location}
            onChange={(e) => onIdentity({ location: e.target.value })}
          />
        </Field>
        <Field label="LinkedIn" htmlFor="p-linkedin">
          <Input
            id="p-linkedin"
            value={id.linkedin ?? ''}
            placeholder="https://linkedin.com/in/..."
            onChange={(e) => onIdentity({ linkedin: e.target.value || null })}
          />
        </Field>
        <Field label="GitHub" htmlFor="p-github">
          <Input
            id="p-github"
            value={id.github ?? ''}
            placeholder="https://github.com/..."
            onChange={(e) => onIdentity({ github: e.target.value || null })}
          />
        </Field>
        <Field label="Website" htmlFor="p-website">
          <Input
            id="p-website"
            value={id.website ?? ''}
            onChange={(e) => onIdentity({ website: e.target.value || null })}
          />
        </Field>
      </div>
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label htmlFor="p-summary">Professional summary</Label>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onImproveSummary}
            loading={improving}
          >
            <Sparkle className="h-3.5 w-3.5" /> Improve with AI
          </Button>
        </div>
        <Textarea
          id="p-summary"
          rows={5}
          value={draft.summary}
          placeholder="A short, punchy overview of who you are and what you do best."
          onChange={(e) => onPatch({ summary: e.target.value })}
        />
        <p className="text-xs text-[var(--muted-foreground)]">
          AI polishes what you write - it never invents experience.
        </p>
      </div>
    </Card>
  );
}

function AiMemorySection({
  draft,
  onPatch,
}: {
  draft: ProfileData;
  onPatch: (u: Partial<ProfileData>) => void;
}) {
  const mem = draft.aiMemory;
  function update(u: Partial<ProfileData['aiMemory']>) {
    onPatch({ aiMemory: { ...mem, ...u } });
  }
  return (
    <Card className="space-y-4 p-5">
      <p className="text-sm text-[var(--muted-foreground)]">
        Preferences that steer AI suggestions. These are never shown on a resume.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Preferred tone" htmlFor="ai-tone">
          <Input
            id="ai-tone"
            value={mem.tone}
            placeholder="Confident, concise"
            onChange={(e) => update({ tone: e.target.value })}
          />
        </Field>
        <Field label="Writing style" htmlFor="ai-style">
          <Input
            id="ai-style"
            value={mem.writingStyle}
            placeholder="Action-led, quantified"
            onChange={(e) => update({ writingStyle: e.target.value })}
          />
        </Field>
      </div>
      <Field label="Target companies (comma separated)" htmlFor="ai-companies">
        <Input
          id="ai-companies"
          value={mem.targetCompanies.join(', ')}
          onChange={(e) => update({ targetCompanies: commaToList(e.target.value) })}
        />
      </Field>
      <Field label="Target industries (comma separated)" htmlFor="ai-industries">
        <Input
          id="ai-industries"
          value={mem.targetIndustries.join(', ')}
          onChange={(e) => update({ targetIndustries: commaToList(e.target.value) })}
        />
      </Field>
    </Card>
  );
}

function ExperienceSection({
  draft,
  onPatch,
  onImproveBullets,
  improving,
  dirty,
}: {
  draft: ProfileData;
  onPatch: (u: Partial<ProfileData>) => void;
  onImproveBullets: (uid: string) => void;
  improving: boolean;
  dirty: boolean;
}) {
  const items = draft.workExperience;
  function update(idx: number, u: Partial<ProfileData['workExperience'][number]>) {
    onPatch({ workExperience: items.map((it, i) => (i === idx ? { ...it, ...u } : it)) });
  }
  function add() {
    onPatch({
      workExperience: [
        ...items,
        {
          uid: draftUid(),
          title: '',
          company: '',
          location: null,
          years: '',
          current: false,
          description: [],
          tech: [],
        },
      ],
    });
  }
  function remove(idx: number) {
    onPatch({ workExperience: items.filter((_, i) => i !== idx) });
  }

  if (items.length === 0) {
    return (
      <EmptyState
        icon={Plus}
        title="No experience yet"
        description="Add your roles so they can flow into every resume you generate."
        action={<Button onClick={add}>Add experience</Button>}
      />
    );
  }

  return (
    <div className="space-y-4">
      {items.map((exp, idx) => (
        <Card key={exp.uid} className="space-y-4 p-5">
          <div className="flex items-start justify-between gap-2">
            <div className="grid flex-1 gap-4 sm:grid-cols-2">
              <Field label="Title" htmlFor={`exp-title-${idx}`}>
                <Input
                  id={`exp-title-${idx}`}
                  value={exp.title}
                  onChange={(e) => update(idx, { title: e.target.value })}
                />
              </Field>
              <Field label="Company" htmlFor={`exp-company-${idx}`}>
                <Input
                  id={`exp-company-${idx}`}
                  value={exp.company}
                  onChange={(e) => update(idx, { company: e.target.value })}
                />
              </Field>
              <Field label="Dates" htmlFor={`exp-years-${idx}`}>
                <Input
                  id={`exp-years-${idx}`}
                  value={exp.years}
                  placeholder="2021 - Present"
                  onChange={(e) => update(idx, { years: e.target.value })}
                />
              </Field>
              <Field label="Location" htmlFor={`exp-loc-${idx}`}>
                <Input
                  id={`exp-loc-${idx}`}
                  value={exp.location ?? ''}
                  onChange={(e) => update(idx, { location: e.target.value || null })}
                />
              </Field>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Remove experience"
              onClick={() => remove(idx)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label htmlFor={`exp-desc-${idx}`}>Highlights (one per line)</Label>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={dirty || exp.description.length === 0}
                loading={improving}
                onClick={() => onImproveBullets(exp.uid)}
                title={dirty ? 'Save your changes first' : undefined}
              >
                <Sparkle className="h-3.5 w-3.5" /> Improve with AI
              </Button>
            </div>
            <Textarea
              id={`exp-desc-${idx}`}
              rows={4}
              value={exp.description.join('\n')}
              placeholder={'Led migration to ...\nReduced latency by ...'}
              onChange={(e) => update(idx, { description: linesToList(e.target.value) })}
            />
          </div>
          <Field label="Technologies (comma separated)" htmlFor={`exp-tech-${idx}`}>
            <Input
              id={`exp-tech-${idx}`}
              value={exp.tech.join(', ')}
              onChange={(e) => update(idx, { tech: commaToList(e.target.value) })}
            />
          </Field>
        </Card>
      ))}
      <Button variant="outline" onClick={add}>
        <Plus className="h-4 w-4" /> Add experience
      </Button>
    </div>
  );
}

function EducationSection({
  draft,
  onPatch,
}: {
  draft: ProfileData;
  onPatch: (u: Partial<ProfileData>) => void;
}) {
  const items = draft.education;
  function update(idx: number, u: Partial<ProfileData['education'][number]>) {
    onPatch({ education: items.map((it, i) => (i === idx ? { ...it, ...u } : it)) });
  }
  function add() {
    onPatch({
      education: [
        ...items,
        { uid: draftUid(), institution: '', degree: '', years: '', description: null },
      ],
    });
  }
  function remove(idx: number) {
    onPatch({ education: items.filter((_, i) => i !== idx) });
  }

  if (items.length === 0) {
    return (
      <EmptyState
        icon={Plus}
        title="No education yet"
        description="Add degrees, bootcamps, or relevant coursework."
        action={<Button onClick={add}>Add education</Button>}
      />
    );
  }

  return (
    <div className="space-y-4">
      {items.map((edu, idx) => (
        <Card key={edu.uid} className="space-y-4 p-5">
          <div className="flex items-start justify-between gap-2">
            <div className="grid flex-1 gap-4 sm:grid-cols-2">
              <Field label="Institution" htmlFor={`edu-inst-${idx}`}>
                <Input
                  id={`edu-inst-${idx}`}
                  value={edu.institution}
                  onChange={(e) => update(idx, { institution: e.target.value })}
                />
              </Field>
              <Field label="Degree" htmlFor={`edu-deg-${idx}`}>
                <Input
                  id={`edu-deg-${idx}`}
                  value={edu.degree}
                  onChange={(e) => update(idx, { degree: e.target.value })}
                />
              </Field>
              <Field label="Dates" htmlFor={`edu-years-${idx}`}>
                <Input
                  id={`edu-years-${idx}`}
                  value={edu.years}
                  onChange={(e) => update(idx, { years: e.target.value })}
                />
              </Field>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Remove education"
              onClick={() => remove(idx)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
          <Field label="Notes" htmlFor={`edu-desc-${idx}`}>
            <Textarea
              id={`edu-desc-${idx}`}
              rows={2}
              value={edu.description ?? ''}
              onChange={(e) => update(idx, { description: e.target.value || null })}
            />
          </Field>
        </Card>
      ))}
      <Button variant="outline" onClick={add}>
        <Plus className="h-4 w-4" /> Add education
      </Button>
    </div>
  );
}

function ProjectsSection({
  draft,
  onPatch,
}: {
  draft: ProfileData;
  onPatch: (u: Partial<ProfileData>) => void;
}) {
  const items = draft.personalProjects;
  function update(idx: number, u: Partial<ProfileData['personalProjects'][number]>) {
    onPatch({
      personalProjects: items.map((it, i) => (i === idx ? { ...it, ...u } : it)),
    });
  }
  function add() {
    onPatch({
      personalProjects: [
        ...items,
        {
          uid: draftUid(),
          name: '',
          role: '',
          years: '',
          github: null,
          website: null,
          description: [],
          tech: [],
          experienceUid: null,
        },
      ],
    });
  }
  function remove(idx: number) {
    onPatch({ personalProjects: items.filter((_, i) => i !== idx) });
  }

  if (items.length === 0) {
    return (
      <EmptyState
        icon={Plus}
        title="No projects yet"
        description="Showcase side projects, open source, or portfolio work."
        action={<Button onClick={add}>Add project</Button>}
      />
    );
  }

  return (
    <div className="space-y-4">
      {items.map((proj, idx) => (
        <Card key={proj.uid} className="space-y-4 p-5">
          <div className="flex items-start justify-between gap-2">
            <div className="grid flex-1 gap-4 sm:grid-cols-2">
              <Field label="Name" htmlFor={`proj-name-${idx}`}>
                <Input
                  id={`proj-name-${idx}`}
                  value={proj.name}
                  onChange={(e) => update(idx, { name: e.target.value })}
                />
              </Field>
              <Field label="Role" htmlFor={`proj-role-${idx}`}>
                <Input
                  id={`proj-role-${idx}`}
                  value={proj.role}
                  onChange={(e) => update(idx, { role: e.target.value })}
                />
              </Field>
              <Field label="GitHub" htmlFor={`proj-gh-${idx}`}>
                <Input
                  id={`proj-gh-${idx}`}
                  value={proj.github ?? ''}
                  onChange={(e) => update(idx, { github: e.target.value || null })}
                />
              </Field>
              <Field label="Website" htmlFor={`proj-web-${idx}`}>
                <Input
                  id={`proj-web-${idx}`}
                  value={proj.website ?? ''}
                  onChange={(e) => update(idx, { website: e.target.value || null })}
                />
              </Field>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Remove project"
              onClick={() => remove(idx)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
          <Field label="Highlights (one per line)" htmlFor={`proj-desc-${idx}`}>
            <Textarea
              id={`proj-desc-${idx}`}
              rows={3}
              value={proj.description.join('\n')}
              onChange={(e) => update(idx, { description: linesToList(e.target.value) })}
            />
          </Field>
          <Field label="Technologies (comma separated)" htmlFor={`proj-tech-${idx}`}>
            <Input
              id={`proj-tech-${idx}`}
              value={proj.tech.join(', ')}
              onChange={(e) => update(idx, { tech: commaToList(e.target.value) })}
            />
          </Field>
        </Card>
      ))}
      <Button variant="outline" onClick={add}>
        <Plus className="h-4 w-4" /> Add project
      </Button>
    </div>
  );
}

function SkillsSection({
  draft,
  onPatch,
}: {
  draft: ProfileData;
  onPatch: (u: Partial<ProfileData>) => void;
}) {
  // Skills are edited as simple comma-separated display names per category; the
  // backend canonicalizes them (alias resolution, dedupe) on save.
  type Cat = keyof ProfileData['skills'];
  const cats: { key: Cat; label: string }[] = [
    { key: 'technical', label: 'Technical skills' },
    { key: 'tools', label: 'Tools' },
    { key: 'languages', label: 'Languages' },
    { key: 'soft', label: 'Soft skills' },
  ];

  function setCategory(cat: Cat, names: string[]) {
    const next = names.map((name) => ({
      uid: draftUid(),
      canonical: name.toLowerCase(),
      displayName: name,
      aliases: [],
      category: cat === 'languages' ? 'language' : cat === 'soft' ? 'soft' : 'technical',
      subcategory: '',
      yearsExperience: null,
      proficiency: '',
      lastUsed: '',
      confidence: null,
      verificationSource: '',
      aiNormalizedName: '',
      evidenceUids: [],
    }));
    onPatch({ skills: { ...draft.skills, [cat]: next } });
  }

  return (
    <Card className="space-y-4 p-5">
      {cats.map((c) => (
        <SkillTagInput
          key={c.key}
          id={`skills-${c.key}`}
          label={c.label}
          values={draft.skills[c.key].map((s) => s.displayName)}
          onChange={(names) => setCategory(c.key, names)}
          placeholder={c.key === 'technical' ? 'Python, React, PostgreSQL' : 'Add a skill'}
          autocomplete={c.key === 'technical' || c.key === 'tools'}
        />
      ))}
    </Card>
  );
}
