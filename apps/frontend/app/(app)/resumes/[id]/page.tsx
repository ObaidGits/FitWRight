'use client';

/**
 * Resume Editor (Task 7.4-7.6 / Req 10,11). Content-first single surface with
 * an always-visible live preview (reuses the render engine → matches the PDF),
 * an appearance inspector (template + options), inline field editing, export,
 * autosave/dirty guard. Deep rich-text/drag-drop editing links to the advanced
 * editor until fully ported (documented transitional decision).
 */
import * as React from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
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
import { ExportButton } from '@/components/resume/export-button';
import { VersionHistoryPanel } from '@/components/resume/version-history-panel';
import { AskAiDialog, type AskAiTarget } from '@/components/ai/ask-ai-dialog';
import { RecoveryBanner } from '@/components/resilience/recovery-banner';
import { useDraft } from '@/lib/hooks/use-draft';
import { useResume } from '@/features/resumes/hooks';
import { updateResume } from '@/lib/api/resume';
import type { ResumeData } from '@/components/dashboard/resume-component';
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
};

type Editable = {
  name: string;
  title: string;
  email: string;
  phone: string;
  location: string;
  summary: string;
  skills: string;
  experience: ItemEdit[];
  projects: ItemEdit[];
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

  const [edit, setEdit] = React.useState<Editable | null>(null);
  const [dirty, setDirty] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [settings, setSettings] = React.useState<TemplateSettings>(DEFAULT_TEMPLATE_SETTINGS);
  const [askAiOpen, setAskAiOpen] = React.useState(false);
  const [askAiTarget, setAskAiTarget] = React.useState<AskAiTarget | null>(null);

  // Draft persistence + recovery (Task 18 / Req 30.2).
  const draft = useDraft<Editable>(`resume-editor:${id}`);

  // Load persisted template settings for this resume.
  React.useEffect(() => {
    try {
      const raw = localStorage.getItem(settingsKey(id));
      if (raw) setSettings({ ...DEFAULT_TEMPLATE_SETTINGS, ...JSON.parse(raw) });
    } catch {
      /* ignore */
    }
  }, [id]);

  const processed = data?.processed_resume ?? null;

  React.useEffect(() => {
    if (!processed) return;
    setEdit({
      name: processed.personalInfo?.name ?? '',
      title: processed.personalInfo?.title ?? '',
      email: processed.personalInfo?.email ?? '',
      phone: processed.personalInfo?.phone ?? '',
      location: processed.personalInfo?.location ?? '',
      summary: processed.summary ?? '',
      skills: (processed.additional?.technicalSkills ?? []).join(', '),
      experience: (processed.workExperience ?? []).map((w) => ({
        heading: w.title ?? '',
        sub: w.company ?? '',
        years: w.years ?? '',
        bullets: (w.description ?? []).join('\n'),
      })),
      projects: (processed.personalProjects ?? []).map((p) => ({
        heading: p.name ?? '',
        sub: p.role ?? '',
        years: p.years ?? '',
        bullets: (p.description ?? []).join('\n'),
      })),
    });
  }, [processed]);

  // Unsaved-changes guard.
  React.useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  function persistSettings(next: TemplateSettings) {
    setSettings(next);
    try {
      localStorage.setItem(settingsKey(id), JSON.stringify(next));
    } catch {
      /* ignore */
    }
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
    // experience / project → replace that item's bullets by parsed index.
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
      },
      summary: edit.summary,
      workExperience: (processed.workExperience ?? []).map((w, i) => {
        const e = edit.experience[i];
        if (!e) return w;
        return {
          ...w,
          title: e.heading,
          company: e.sub,
          years: e.years,
          description: toLines(e.bullets),
        };
      }),
      personalProjects: (processed.personalProjects ?? []).map((p, i) => {
        const e = edit.projects[i];
        if (!e) return p;
        return {
          ...p,
          name: e.heading,
          role: e.sub,
          years: e.years,
          description: toLines(e.bullets),
        };
      }),
      additional: {
        ...(processed.additional ?? {}),
        technicalSkills: edit.skills
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
      },
    } as ResumeData;
  }, [processed, edit]);

  async function onSave() {
    if (!previewData) return;
    setSaving(true);
    try {
      await updateResume(id, previewData as never);
      setDirty(false);
      draft.clear();
      toast({ title: 'Resume saved', variant: 'success' });
    } catch {
      toast({ title: 'Could not save resume', variant: 'error' });
    } finally {
      setSaving(false);
    }
  }

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
          <VersionHistoryPanel resumeId={id} onRestored={() => refetch()} />
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
                    <SelectTrigger>
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
                  onChange={(patch) => updateItem('experience', i, patch)}
                  onAskAi={() => askAiForItem('experience', i)}
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
                  onChange={(patch) => updateItem('projects', i, patch)}
                  onAskAi={() => askAiForItem('projects', i)}
                />
              ))}
            </Card>
          )}

          <Card className="flex items-center justify-between gap-3 p-4">
            <div className="flex items-center gap-2 text-sm">
              <SlidersHorizontal className="h-4 w-4 text-[var(--muted-foreground)]" />
              Reorder sections &amp; drag &amp; drop
            </div>
            <Button asChild variant="ghost" size="sm">
              <Link href="/builder">Open advanced editor</Link>
            </Button>
          </Card>

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
            <div className="max-h-[70vh] overflow-y-auto p-4">
              {previewData && <RenderTemplate data={previewData} settings={settings} />}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** One experience/project item with editable fields, bullets, and per-item Ask AI. */
function ItemEditor({
  item,
  headingLabel,
  subLabel,
  onChange,
  onAskAi,
}: {
  item: ItemEdit;
  headingLabel: string;
  subLabel: string;
  onChange: (patch: Partial<ItemEdit>) => void;
  onAskAi: () => void;
}) {
  return (
    <div className="space-y-2 rounded-[var(--radius-at-md)] border border-[var(--border)] p-3">
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
          placeholder="• Led …&#10;• Built …"
        />
      </div>
    </div>
  );
}
