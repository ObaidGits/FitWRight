'use client';

/**
 * Resume Editor (Task 7.4-7.6 / Req 10,11). Content-first single surface with
 * an always-visible live preview (reuses the render engine -> matches the PDF),
 * an appearance inspector (template + options), inline field editing, export,
 * autosave/dirty guard. Deep rich-text/drag-drop editing links to the advanced
 * editor until fully ported (documented transitional decision).
 */
import * as React from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
import ArrowUp from 'lucide-react/dist/esm/icons/arrow-up';
import ArrowDown from 'lucide-react/dist/esm/icons/arrow-down';
import Palette from 'lucide-react/dist/esm/icons/palette';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import SlidersHorizontal from 'lucide-react/dist/esm/icons/sliders-horizontal';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Input, Textarea } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Switch } from '@/components/atelier/misc';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/atelier/select';
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/atelier/sheet';
import { LoadingSkeleton, ErrorState } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { RenderTemplate } from '@/components/resume/render-template';
import { PhotoControls } from '@/components/builder/photo-controls';
import { ExportButton } from '@/components/resume/export-button';
import { GeneratedDocCard } from '@/components/resume/generated-doc-card';
import { InterviewPrepCard } from '@/components/resume/interview-prep-card';
import { JdMatchCard } from '@/components/resume/jd-match-card';
import { VersionHistoryPanel } from '@/components/resume/version-history-panel';
import { AskAiDialog, type AskAiTarget } from '@/components/ai/ask-ai-dialog';
import { RecoveryBanner } from '@/components/resilience/recovery-banner';
import { UnsavedChangesGuard } from '@/components/common/unsaved-changes-guard';
import { useDraft } from '@/lib/hooks/use-draft';
import { useResume } from '@/features/resumes/hooks';
import { useQueryClient } from '@tanstack/react-query';
import { invalidateResumeLists, queryKeys } from '@/lib/query/client';
import {
  updateResume,
  updateResumeTemplateSettings,
  normalizeTemplateSettings,
} from '@/lib/api/resume';
import type {
  ResumeData,
  SectionMeta,
  CustomSection,
} from '@/components/dashboard/resume-component';
import type { PhotoConfig } from '@/lib/types/photo';
import { DEFAULT_SECTION_META } from '@/lib/utils/section-helpers';
import { CustomSectionsEditor } from '@/components/resume/custom-sections-editor';
import {
  DEFAULT_TEMPLATE_SETTINGS,
  TEMPLATE_OPTIONS,
  applyTemplatePreset,
  type TemplateSettings,
  type TemplateType,
} from '@/lib/types/template-settings';

type ItemEdit = {
  heading: string; // job title / project name
  sub: string; // company / role
  years: string;
  bullets: string; // one bullet per line
  // The original entry this edit maps to. Carried so previewData can be rebuilt
  // in the edit array's ORDER (enabling reordering) while preserving fields the
  // UI doesn't edit (stable id, work location, project github/website).
  source: Record<string, unknown>;
};

type Editable = {
  name: string;
  title: string;
  email: string;
  phone: string;
  location: string;
  summary: string;
  skills: string;
  // Photo System: per-resume photo config + the resolved/canonical avatar URL,
  // edited via <PhotoControls> and flowed through the same dirty/draft/save path.
  photo: PhotoConfig | null;
  avatarUrl: string | null;
  experience: ItemEdit[];
  projects: ItemEdit[];
  // Section ordering/visibility + custom sections (drives the render engine's
  // getSortedSections). Held here so reorder/show-hide/custom-section edits flow
  // through the same dirty-guard, draft-recovery, and save path as everything else.
  sectionMeta: SectionMeta[];
  customSections: Record<string, CustomSection>;
};

function toLines(value: string): string[] {
  return value
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
}

function settingsKey(id: string) {
  return `fitwright-template-${id}`;
}

export default function ResumeEditorPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const { data, isLoading, isError, refetch } = useResume(id);
  const { toast } = useToast();
  const qc = useQueryClient();

  const [edit, setEdit] = React.useState<Editable | null>(null);
  const [dirty, setDirty] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [settings, setSettings] = React.useState<TemplateSettings>(DEFAULT_TEMPLATE_SETTINGS);
  const [askAiOpen, setAskAiOpen] = React.useState(false);
  const [askAiTarget, setAskAiTarget] = React.useState<AskAiTarget | null>(null);

  // Draft persistence + recovery (Task 18 / Req 30.2).
  const draft = useDraft<Editable>(`resume-editor:${id}`);

  // Load persisted template settings. The backend is the source of truth (so a
  // resume opens in the same template on any device); localStorage is only a
  // fast, offline-friendly cache used until the fetch resolves. `adoptedRef`
  // ensures the backend value is adopted exactly once per resume, so it never
  // clobbers subsequent in-session edits.
  const adoptedRef = React.useRef(false);
  React.useEffect(() => {
    adoptedRef.current = false;
    try {
      const raw = localStorage.getItem(settingsKey(id));
      if (raw) setSettings(normalizeTemplateSettings(JSON.parse(raw)));
    } catch {
      /* ignore */
    }
  }, [id]);

  React.useEffect(() => {
    if (adoptedRef.current) return;
    const persisted = data?.template_settings;
    if (persisted) {
      adoptedRef.current = true;
      const normalized = normalizeTemplateSettings(persisted);
      setSettings(normalized);
      try {
        localStorage.setItem(settingsKey(id), JSON.stringify(normalized));
      } catch {
        /* ignore */
      }
    }
  }, [data?.template_settings, id]);

  const processed = data?.processed_resume ?? null;

  React.useEffect(() => {
    if (!processed) return;
    setEdit({
      name: processed.personalInfo?.name ?? '',
      title: processed.personalInfo?.title ?? '',
      email: processed.personalInfo?.email ?? '',
      phone: processed.personalInfo?.phone ?? '',
      location: processed.personalInfo?.location ?? '',
      photo: processed.personalInfo?.photo ?? null,
      avatarUrl: processed.personalInfo?.avatarUrl ?? null,
      summary: processed.summary ?? '',
      skills: (processed.additional?.technicalSkills ?? []).join(', '),
      experience: (processed.workExperience ?? []).map((w) => ({
        heading: w.title ?? '',
        sub: w.company ?? '',
        years: w.years ?? '',
        bullets: (w.description ?? []).join('\n'),
        source: w as unknown as Record<string, unknown>,
      })),
      projects: (processed.personalProjects ?? []).map((p) => ({
        heading: p.name ?? '',
        sub: p.role ?? '',
        years: p.years ?? '',
        bullets: (p.description ?? []).join('\n'),
        source: p as unknown as Record<string, unknown>,
      })),
      sectionMeta:
        processed.sectionMeta && processed.sectionMeta.length
          ? processed.sectionMeta
          : DEFAULT_SECTION_META,
      customSections: processed.customSections ?? {},
    });
  }, [processed]);

  // Unsaved-changes protection now lives in <UnsavedChangesGuard when={dirty} />
  // (reload/close + in-app link nav + Back/Forward). See below in the render.

  // Keyboard save (⌘/Ctrl+S) - matches the expectation set by the editor's
  // dirty guard. Keep a ref so the handler always saves the latest state.
  const onSaveRef = React.useRef<() => void>(() => {});
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        if (dirty && !saving) onSaveRef.current();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [dirty, saving]);

  function persistSettings(next: TemplateSettings) {
    // Once the user changes the appearance we own it - never let a late backend
    // adoption overwrite the choice.
    adoptedRef.current = true;
    setSettings(next);
    try {
      localStorage.setItem(settingsKey(id), JSON.stringify(next));
    } catch {
      /* ignore */
    }
    // Persist to the backend (source of truth) so the template survives across
    // devices, export, and duplication. Fire-and-forget: appearance never bumps
    // the content version, so this can't conflict with an in-flight save.
    void updateResumeTemplateSettings(id, next).catch(() => {
      /* best-effort; localStorage cache keeps the session correct */
    });
  }

  function field<K extends keyof Editable>(key: K, value: string) {
    setEdit((prev) => {
      if (!prev) return prev;
      const next = { ...prev, [key]: value };
      draft.save(next);
      return next;
    });
    setDirty(true);
  }

  function restoreDraft() {
    if (!draft.recovered) return;
    setEdit(draft.recovered);
    setDirty(true);
    draft.dismissRecovery();
  }

  // Update one experience/project item field.
  function updateItem(kind: 'experience' | 'projects', index: number, patch: Partial<ItemEdit>) {
    setEdit((prev) => {
      if (!prev) return prev;
      const arr = prev[kind].map((it, i) => (i === index ? { ...it, ...patch } : it));
      const next = { ...prev, [kind]: arr };
      draft.save(next);
      return next;
    });
    setDirty(true);
  }

  // Reorder an experience/project item by one position (identity-based: the
  // whole item - including its preserved `source` - moves, so no field is lost).
  function moveItem(kind: 'experience' | 'projects', index: number, dir: -1 | 1) {
    setEdit((prev) => {
      if (!prev) return prev;
      const arr = [...prev[kind]];
      const target = index + dir;
      if (target < 0 || target >= arr.length) return prev;
      [arr[index], arr[target]] = [arr[target], arr[index]];
      const next = { ...prev, [kind]: arr };
      draft.save(next);
      return next;
    });
    setDirty(true);
  }

  // Apply a section-management change (reorder / show-hide / add / edit / delete
  // custom sections) from the CustomSectionsEditor.
  function updateSections(next: {
    sectionMeta: SectionMeta[];
    customSections: Record<string, CustomSection>;
  }) {
    setEdit((prev) => {
      if (!prev) return prev;
      const merged = { ...prev, ...next };
      draft.save(merged);
      return merged;
    });
    setDirty(true);
  }

  // Photo System: update the per-resume photo config / canonical avatar URL,
  // flowing through the same dirty-guard + draft-recovery + save path.
  function updatePhoto(photo: PhotoConfig) {
    setEdit((prev) => {
      if (!prev) return prev;
      const next = { ...prev, photo };
      draft.save(next);
      return next;
    });
    setDirty(true);
  }
  function updateAvatarUrl(url: string | null) {
    setEdit((prev) => {
      if (!prev) return prev;
      const next = { ...prev, avatarUrl: url };
      draft.save(next);
      return next;
    });
    setDirty(true);
  }

  // Open the contextual AI dialog for a specific experience/project item.
  function askAiForItem(kind: 'experience' | 'projects', index: number) {
    if (!edit) return;
    const item = edit[kind][index];
    const itemType = kind === 'experience' ? 'experience' : 'project';
    const prefix = kind === 'experience' ? 'exp_' : 'proj_';
    setAskAiTarget({
      resumeId: id,
      itemId: `${prefix}${index}`,
      itemType,
      title: item.heading || (itemType === 'experience' ? 'Experience' : 'Project'),
      subtitle: item.sub || undefined,
      currentContent: toLines(item.bullets),
    });
    setAskAiOpen(true);
  }

  // Apply an accepted AI suggestion back into the editor (preview-before-apply).
  function applyAi(target: AskAiTarget, newContent: string[]) {
    if (target.itemType === 'skills') {
      const value = newContent.join(', ');
      setEdit((prev) => {
        if (!prev) return prev;
        const next = { ...prev, skills: value };
        draft.save(next);
        return next;
      });
      setDirty(true);
      return;
    }
    // experience / project -> replace that item's bullets by parsed index.
    const kind = target.itemType === 'experience' ? 'experience' : 'projects';
    const prefix = target.itemType === 'experience' ? 'exp_' : 'proj_';
    const index = Number.parseInt(target.itemId.slice(prefix.length), 10);
    if (Number.isNaN(index)) return;
    updateItem(kind, index, { bullets: newContent.join('\n') });
  }

  // Merge edits back into the processed-resume shape for preview + save.
  const previewData: ResumeData | null = React.useMemo(() => {
    if (!processed || !edit) return null;
    return {
      ...(processed as unknown as ResumeData),
      personalInfo: {
        ...(processed.personalInfo ?? {}),
        name: edit.name,
        title: edit.title,
        email: edit.email,
        phone: edit.phone,
        location: edit.location,
        avatarUrl: edit.avatarUrl,
        photo: edit.photo,
      },
      summary: edit.summary,
      // Build from the EDIT array's order (not the processed index) so reordering
      // takes effect; spread each item's original `source` to preserve fields the
      // UI doesn't edit (id, location, github, website).
      workExperience: edit.experience.map((e) => ({
        ...e.source,
        title: e.heading,
        company: e.sub,
        years: e.years,
        description: toLines(e.bullets),
      })),
      personalProjects: edit.projects.map((e) => ({
        ...e.source,
        name: e.heading,
        role: e.sub,
        years: e.years,
        description: toLines(e.bullets),
      })),
      additional: {
        ...(processed.additional ?? {}),
        technicalSkills: edit.skills
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
      },
      // Section order/visibility + custom sections drive the render engine.
      sectionMeta: edit.sectionMeta,
      customSections: edit.customSections,
    } as ResumeData;
  }, [processed, edit]);

  const onSave = React.useCallback(async () => {
    if (!previewData) return;
    setSaving(true);
    try {
      await updateResume(id, previewData as never);
      setDirty(false);
      draft.clear();
      // Refresh the list surfaces (home / resumes list / tailor picker) so a
      // renamed/edited resume shows immediately - without refetching THIS
      // detail (that could clobber edits made right after saving).
      invalidateResumeLists(qc);
      toast({ title: 'Resume saved', variant: 'success' });
    } catch (err) {
      toast({
        title: (err as Error)?.message || 'Could not save resume',
        variant: 'error',
      });
    } finally {
      setSaving(false);
    }
  }, [previewData, id, draft, toast, qc]);

  React.useEffect(() => {
    onSaveRef.current = () => void onSave();
  }, [onSave]);

  if (isLoading) return <LoadingSkeleton rows={5} />;
  if (isError || !data)
    return <ErrorState description="Could not load this resume." onRetry={() => refetch()} />;

  const status = data.raw_resume?.processing_status;
  if (status === 'failed') {
    return (
      <ErrorState
        title="This resume failed to process"
        description="It may be a scanned/image PDF. Try re-uploading a file with selectable text."
      />
    );
  }
  if (!processed || !edit) {
    return <LoadingSkeleton rows={5} />;
  }

  return (
    <div className="space-y-4">
      <UnsavedChangesGuard when={dirty} />
      {draft.recovered && (
        <RecoveryBanner
          savedAt={draft.recoveredAt}
          onRestore={restoreDraft}
          onDiscard={draft.clear}
        />
      )}
      <AskAiDialog
        open={askAiOpen}
        onOpenChange={setAskAiOpen}
        target={askAiTarget}
        onApply={applyAi}
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link
          href="/resumes"
          className="inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
        >
          <ArrowLeft className="h-4 w-4" /> Resumes
        </Link>
        <div className="flex items-center gap-2">
          {dirty && <Badge variant="warning">Unsaved</Badge>}
          <VersionHistoryPanel
            resumeId={id}
            onRestored={() => {
              // A restore changes the resume content (and possibly its title):
              // refetch this editor AND refresh the list surfaces.
              refetch();
              qc.invalidateQueries({ queryKey: queryKeys.resume(id) });
              invalidateResumeLists(qc);
            }}
          />
          <Sheet>
            <SheetTrigger asChild>
              <Button variant="outline" size="sm">
                <Palette className="h-4 w-4" /> Appearance
              </Button>
            </SheetTrigger>
            <SheetContent side="right" className="p-6">
              <SheetTitle className="mb-4 text-lg font-semibold">Appearance</SheetTitle>
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label>Template</Label>
                  <Select
                    value={settings.template}
                    onValueChange={(v) =>
                      persistSettings(applyTemplatePreset(settings, v as TemplateType))
                    }
                  >
                    <SelectTrigger aria-label="Resume template">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TEMPLATE_OPTIONS.map((t) => (
                        <SelectItem key={t.id} value={t.id}>
                          {t.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-center justify-between">
                  <Label>Contact icons</Label>
                  <Switch
                    checked={settings.showContactIcons}
                    onCheckedChange={(c) => persistSettings({ ...settings, showContactIcons: c })}
                  />
                </div>
                <div className="flex items-center justify-between">
                  <Label>Compact mode</Label>
                  <Switch
                    checked={settings.compactMode}
                    onCheckedChange={(c) => persistSettings({ ...settings, compactMode: c })}
                  />
                </div>
                <div className="border-t border-[var(--border)] pt-3">
                  <Button
                    asChild
                    variant="ghost"
                    size="sm"
                    className="text-[var(--muted-foreground)]"
                  >
                    <Link href={`/builder?id=${id}`}>
                      <SlidersHorizontal className="h-4 w-4" /> Fine-grained formatting (margins,
                      fonts, colors)
                    </Link>
                  </Button>
                </div>
              </div>
            </SheetContent>
          </Sheet>
          <ExportButton kind="resume" resumeId={id} settings={settings} />
          <Button size="sm" onClick={onSave} loading={saving} disabled={!dirty}>
            Save
          </Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Content editor */}
        <div className="space-y-4">
          <Card className="space-y-3 p-5">
            <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Details</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  value={edit.name}
                  onChange={(e) => field('name', e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="title">Title</Label>
                <Input
                  id="title"
                  value={edit.title}
                  onChange={(e) => field('title', e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  value={edit.email}
                  onChange={(e) => field('email', e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="phone">Phone</Label>
                <Input
                  id="phone"
                  value={edit.phone}
                  onChange={(e) => field('phone', e.target.value)}
                />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="location">Location</Label>
                <Input
                  id="location"
                  value={edit.location}
                  onChange={(e) => field('location', e.target.value)}
                />
              </div>
            </div>
          </Card>

          {/* Photo - same PhotoControls used by the resume builder + settings. */}
          <Card className="space-y-2 p-5">
            <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Photo</h2>
            <PhotoControls
              template={settings.template}
              value={edit.photo}
              profileAvatarUrl={edit.avatarUrl}
              onChange={updatePhoto}
              onProfileAvatarChange={updateAvatarUrl}
              onError={(message) => toast({ title: message, variant: 'error' })}
            />
          </Card>

          <Card className="space-y-2 p-5">
            <Label htmlFor="summary">Summary</Label>
            <Textarea
              id="summary"
              value={edit.summary}
              onChange={(e) => field('summary', e.target.value)}
              className="min-h-28"
            />
          </Card>

          <Card className="space-y-2 p-5">
            <div className="flex items-center justify-between">
              <Label htmlFor="skills">Technical skills (comma-separated)</Label>
              <Button
                variant="ghost"
                size="sm"
                className="text-[var(--at-ai)]"
                onClick={() => {
                  setAskAiTarget({
                    resumeId: id,
                    itemId: 'skills',
                    itemType: 'skills',
                    title: 'Technical skills',
                    currentContent: edit.skills
                      .split(',')
                      .map((s) => s.trim())
                      .filter(Boolean),
                  });
                  setAskAiOpen(true);
                }}
              >
                <Sparkles className="h-4 w-4" /> Ask AI
              </Button>
            </div>
            <Textarea
              id="skills"
              value={edit.skills}
              onChange={(e) => field('skills', e.target.value)}
            />
          </Card>

          {edit.experience.length > 0 && (
            <Card className="space-y-4 p-5">
              <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Experience</h2>
              {edit.experience.map((item, i) => (
                <ItemEditor
                  key={`exp-${i}`}
                  item={item}
                  headingLabel="Job title"
                  subLabel="Company"
                  position={i}
                  count={edit.experience.length}
                  onChange={(patch) => updateItem('experience', i, patch)}
                  onAskAi={() => askAiForItem('experience', i)}
                  onMove={(dir) => moveItem('experience', i, dir)}
                />
              ))}
            </Card>
          )}

          {edit.projects.length > 0 && (
            <Card className="space-y-4 p-5">
              <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Projects</h2>
              {edit.projects.map((item, i) => (
                <ItemEditor
                  key={`proj-${i}`}
                  item={item}
                  headingLabel="Project"
                  subLabel="Role"
                  position={i}
                  count={edit.projects.length}
                  onChange={(patch) => updateItem('projects', i, patch)}
                  onAskAi={() => askAiForItem('projects', i)}
                  onMove={(dir) => moveItem('projects', i, dir)}
                />
              ))}
            </Card>
          )}

          <CustomSectionsEditor
            sectionMeta={edit.sectionMeta}
            customSections={edit.customSections}
            onChange={updateSections}
          />

          <GeneratedDocCard
            kind="cover-letter"
            resumeId={id}
            initialContent={data.cover_letter}
            isTailored={Boolean(data.parent_id)}
            onSaved={() => refetch()}
          />

          <GeneratedDocCard
            kind="outreach"
            resumeId={id}
            initialContent={data.outreach_message}
            isTailored={Boolean(data.parent_id)}
            onSaved={() => refetch()}
          />

          <InterviewPrepCard
            resumeId={id}
            initialPrep={data.interview_prep}
            isTailored={Boolean(data.parent_id)}
            onGenerated={() => refetch()}
          />

          {previewData && (
            <JdMatchCard
              resumeId={id}
              resumeData={previewData}
              isTailored={Boolean(data.parent_id)}
            />
          )}

          <Card className="flex items-center justify-between gap-3 p-4">
            <div className="flex items-center gap-2 text-sm">
              <Sparkles className="h-4 w-4 text-[var(--at-ai)]" /> Tailor this resume to a job
            </div>
            <Button asChild size="sm">
              <Link href={`/tailor?resume=${id}`}>Tailor</Link>
            </Button>
          </Card>
        </div>

        {/* Live preview */}
        <div className="lg:sticky lg:top-6 lg:self-start">
          <p className="mb-2 text-sm font-semibold text-[var(--muted-foreground)]">Live preview</p>
          <div className="overflow-hidden rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-white">
            {/* scrollbar-gutter:stable reserves the scrollbar track so toggling
                it (as content height crosses the max-height) can't change the
                preview's available width and re-trigger a fit-to-width rescale. */}
            <div className="max-h-[70vh] overflow-y-auto p-4 [scrollbar-gutter:stable]">
              {previewData && <RenderTemplate data={previewData} settings={settings} />}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** One experience/project item with editable fields, bullets, reorder, and Ask AI. */
function ItemEditor({
  item,
  headingLabel,
  subLabel,
  position,
  count,
  onChange,
  onAskAi,
  onMove,
}: {
  item: ItemEdit;
  headingLabel: string;
  subLabel: string;
  position: number;
  count: number;
  onChange: (patch: Partial<ItemEdit>) => void;
  onAskAi: () => void;
  onMove: (dir: -1 | 1) => void;
}) {
  return (
    <div className="space-y-2 rounded-[var(--radius-at-md)] border border-[var(--border)] p-3">
      {count > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            {position + 1} of {count}
          </span>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              aria-label="Move up"
              disabled={position === 0}
              onClick={() => onMove(-1)}
            >
              <ArrowUp className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Move down"
              disabled={position === count - 1}
              onClick={() => onMove(1)}
            >
              <ArrowDown className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="space-y-1">
          <Label className="text-xs">{headingLabel}</Label>
          <Input value={item.heading} onChange={(e) => onChange({ heading: e.target.value })} />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">{subLabel}</Label>
          <Input value={item.sub} onChange={(e) => onChange({ sub: e.target.value })} />
        </div>
      </div>
      <div className="space-y-1">
        <Label className="text-xs">Dates</Label>
        <Input value={item.years} onChange={(e) => onChange({ years: e.target.value })} />
      </div>
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label className="text-xs">Bullet points (one per line)</Label>
          <Button variant="ghost" size="sm" className="text-[var(--at-ai)]" onClick={onAskAi}>
            <Sparkles className="h-4 w-4" /> Ask AI
          </Button>
        </div>
        <Textarea
          value={item.bullets}
          onChange={(e) => onChange({ bullets: e.target.value })}
          className="min-h-24"
          placeholder="- Led ...&#10;- Built ..."
        />
      </div>
    </div>
  );
}
