'use client';

/**
 * Structured section surfaces for the wizard (Phase P1 — W-P1.1/W-P1.2).
 *
 * Identity, Contact, and Skills are captured with discrete, validated fields
 * (and chips for skills) rather than free-text prose parsed by an LLM. Each
 * surface reports its value up via `onChange` and validity via `onValidityChange`
 * so the page can drive an optimistic preview and gate the Continue button.
 */
import * as React from 'react';
import X from 'lucide-react/dist/esm/icons/x';
import Plus from 'lucide-react/dist/esm/icons/plus';

import Sparkles from 'lucide-react/dist/esm/icons/sparkles';

import { Input, Textarea } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Badge } from '@/components/atelier/badge';
import { Button } from '@/components/atelier/button';
import type { ResumeData } from '@/components/dashboard/resume-component';
import { useRotatingMessages } from '@/lib/hooks/use-ai-progress';
import {
  assistResumeWizard,
  type ExperienceInput,
  type ProjectInput,
  type ResumeWizardParsedEntry,
  type ResumeWizardSection,
  type ResumeWizardStructuredUpdate,
} from '@/lib/api/resume-wizard';

export const STRUCTURED_SECTIONS: ReadonlySet<ResumeWizardSection> = new Set([
  'intro',
  'contact',
  'workExperience',
  'internships',
  'personalProjects',
  'education',
  'skills',
]);

export function isStructuredSection(section: ResumeWizardSection): boolean {
  return STRUCTURED_SECTIONS.has(section);
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidEmail(value: string): boolean {
  return EMAIL_RE.test(value.trim());
}

export function isValidUrlish(value: string): boolean {
  const v = value.trim();
  // Lenient: a bare domain or a full URL both pass; only obvious junk fails.
  return v.includes('.') && !/\s/.test(v);
}

interface FieldProps {
  data: ResumeData;
  onChange: (update: ResumeWizardStructuredUpdate) => void;
  onValidityChange: (valid: boolean) => void;
}

/** Identity: name (required) + target role. */
export function IdentityFields({ data, onChange, onValidityChange }: FieldProps) {
  const [name, setName] = React.useState(data.personalInfo?.name ?? '');
  const [title, setTitle] = React.useState(data.personalInfo?.title ?? '');

  React.useEffect(() => {
    onValidityChange(name.trim().length > 0);
    onChange({ personal_info: { name, title }, next_section: 'contact' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name, title]);

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="wiz-name">Your name</Label>
        <Input
          id="wiz-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Jane Doe"
          aria-required
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="wiz-title">Target role</Label>
        <Input
          id="wiz-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Senior Software Engineer"
        />
      </div>
    </div>
  );
}

/** Contact: discrete, validated fields (all optional but format-checked). */
export function ContactFields({ data, onChange, onValidityChange }: FieldProps) {
  const info = data.personalInfo ?? {};
  const [email, setEmail] = React.useState(info.email ?? '');
  const [phone, setPhone] = React.useState(info.phone ?? '');
  const [linkedin, setLinkedin] = React.useState(info.linkedin ?? '');
  const [github, setGithub] = React.useState(info.github ?? '');
  const [website, setWebsite] = React.useState(info.website ?? '');

  const emailInvalid = email.trim().length > 0 && !isValidEmail(email);
  const linkInvalid = (v: string) => v.trim().length > 0 && !isValidUrlish(v);
  const anyLinkInvalid = [linkedin, github, website].some(linkInvalid);

  React.useEffect(() => {
    onValidityChange(!emailInvalid && !anyLinkInvalid);
    onChange({
      personal_info: { email, phone, linkedin, github, website },
      next_section: null,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [email, phone, linkedin, github, website]);

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor="wiz-email">Email</Label>
        <Input
          id="wiz-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="jane@example.com"
          aria-invalid={emailInvalid}
        />
        {emailInvalid && (
          <p className="text-xs text-[var(--at-error,var(--destructive))]">
            Enter a valid email address.
          </p>
        )}
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="wiz-phone">Phone</Label>
        <Input
          id="wiz-phone"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="+1 555 123 4567"
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="wiz-linkedin">LinkedIn</Label>
          <Input
            id="wiz-linkedin"
            value={linkedin}
            onChange={(e) => setLinkedin(e.target.value)}
            placeholder="linkedin.com/in/jane"
            aria-invalid={linkInvalid(linkedin)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="wiz-github">GitHub</Label>
          <Input
            id="wiz-github"
            value={github}
            onChange={(e) => setGithub(e.target.value)}
            placeholder="github.com/jane"
            aria-invalid={linkInvalid(github)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="wiz-website">Website</Label>
          <Input
            id="wiz-website"
            value={website}
            onChange={(e) => setWebsite(e.target.value)}
            placeholder="jane.dev"
            aria-invalid={linkInvalid(website)}
          />
        </div>
      </div>
      {anyLinkInvalid && (
        <p className="text-xs text-[var(--at-error,var(--destructive))]">
          Links should look like a URL (e.g. linkedin.com/in/jane).
        </p>
      )}
    </div>
  );
}

/** Skills: chip input + confirmable AI suggestions (never auto-applied). */
export function SkillsChips({
  data,
  suggestions,
  onChange,
  onValidityChange,
}: FieldProps & { suggestions: string[] }) {
  const [skills, setSkills] = React.useState<string[]>(
    () => data.additional?.technicalSkills ?? []
  );
  const [draft, setDraft] = React.useState('');

  React.useEffect(() => {
    onValidityChange(skills.length > 0);
    onChange({ technical_skills: skills, next_section: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skills]);

  const addSkill = (value: string) => {
    const v = value.trim();
    if (!v) return;
    setSkills((prev) =>
      prev.some((s) => s.toLowerCase() === v.toLowerCase()) ? prev : [...prev, v]
    );
    setDraft('');
  };
  const removeSkill = (value: string) => setSkills((prev) => prev.filter((s) => s !== value));

  const remainingSuggestions = suggestions.filter(
    (s) => !skills.some((k) => k.toLowerCase() === s.toLowerCase())
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5" data-testid="skill-chips">
        {skills.map((skill) => (
          <Badge key={skill} variant="neutral" className="gap-1">
            {skill}
            <button
              type="button"
              aria-label={`Remove ${skill}`}
              onClick={() => removeSkill(skill)}
              className="opacity-70 hover:opacity-100"
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        ))}
      </div>
      <div className="flex gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') {
              e.preventDefault();
              addSkill(draft);
            }
          }}
          placeholder="Add a skill and press Enter"
          aria-label="Add a skill"
        />
        <Button type="button" variant="outline" size="sm" onClick={() => addSkill(draft)}>
          Add
        </Button>
      </div>
      {remainingSuggestions.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs text-[var(--muted-foreground)]">
            Suggestions from what you told me (tap to add):
          </p>
          <div className="flex flex-wrap gap-1.5">
            {remainingSuggestions.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => addSkill(s)}
                className="inline-flex items-center gap-1 rounded-full border border-dashed border-[var(--border)] px-2 py-0.5 text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              >
                <Plus className="h-3 w-3" /> {s}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const GRADE_LABELS: Record<string, string> = {
  cgpa: 'CGPA',
  gpa: 'GPA',
  percentage: 'Percentage',
};

/**
 * Compose the structured education extras (specialization, grade/score,
 * achievements) into a single human-readable description string so they render
 * in every template + the PDF, which only display
 * institution/degree/years/description.
 */
export function composeEducationDescription(edu: {
  specialization?: string;
  gradeType?: string;
  score?: string;
  achievements?: string[];
}): string {
  const parts: string[] = [];
  const specialization = (edu.specialization ?? '').trim();
  if (specialization) parts.push(`Specialization: ${specialization}`);
  const score = (edu.score ?? '').trim();
  if (edu.gradeType && score) parts.push(`${GRADE_LABELS[edu.gradeType] ?? 'Grade'}: ${score}`);
  for (const achievement of edu.achievements ?? []) {
    const a = achievement.trim();
    if (a) parts.push(a);
  }
  return parts.join(' • ');
}

/** Build the `years` display string from structured year fields. */
export function educationYearsDisplay(edu: {
  startYear?: string;
  endYear?: string;
  currentlyStudying?: boolean;
}): string {
  const start = (edu.startYear ?? '').trim();
  const end = (edu.endYear ?? '').trim();
  if (edu.currentlyStudying) return start ? `${start} - Present` : 'Present';
  if (start && end) return `${start} - ${end}`;
  return start || end;
}

/**
 * Apply a structured update to a copy of resume data for the OPTIMISTIC preview
 * (W-P1.3) — reflected instantly, before/without the server round-trip.
 */
export function applyStructuredToResume(
  data: ResumeData,
  update: ResumeWizardStructuredUpdate | null
): ResumeData {
  if (!update) return data;
  const next: ResumeData = {
    ...data,
    personalInfo: { ...data.personalInfo },
    additional: { ...data.additional },
  };
  if (update.personal_info) {
    for (const [key, value] of Object.entries(update.personal_info)) {
      (next.personalInfo as Record<string, unknown>)[key] = value;
    }
  }
  if (update.technical_skills) {
    next.additional = { ...next.additional, technicalSkills: update.technical_skills };
  }
  if (update.education && (update.education.institution || update.education.degree)) {
    const existing = next.education ?? [];
    next.education = [
      ...existing,
      {
        id: existing.length + 1,
        ...update.education,
        years: update.education.years || educationYearsDisplay(update.education),
      },
    ];
  }
  const expEntries = (update.experiences ?? []).filter(
    (e) => (e.title ?? '').trim() || (e.company ?? '').trim()
  );
  if (expEntries.length) {
    const existing = next.workExperience ?? [];
    next.workExperience = [
      ...existing,
      ...expEntries.map((exp, i) => ({ id: existing.length + i + 1, ...exp })),
    ];
  }
  const projEntries = (update.projects ?? []).filter((p) => (p.name ?? '').trim());
  if (projEntries.length) {
    const existing = next.personalProjects ?? [];
    next.personalProjects = [
      ...existing,
      ...projEntries.map((proj, i) => ({ id: existing.length + i + 1, ...proj })),
    ];
  }
  return next;
}

/** Compose a `years` display string from start/end/current for experience. */
export function composeYears(start?: string, end?: string, current?: boolean): string {
  const s = (start ?? '').trim();
  const e = (end ?? '').trim();
  if (current) return s ? `${s} – Present` : 'Present';
  if (s && e) return `${s} – ${e}`;
  return s || e;
}

/** Education: structured card with progressive disclosure (W-P2.1/W-P2.2/N3). */
export function EducationCard({ data, onChange, onValidityChange }: FieldProps) {
  const [institution, setInstitution] = React.useState('');
  const [degree, setDegree] = React.useState('');
  const [specialization, setSpecialization] = React.useState('');
  const [location, setLocation] = React.useState('');
  const [startYear, setStartYear] = React.useState('');
  const [endYear, setEndYear] = React.useState('');
  const [currentlyStudying, setCurrentlyStudying] = React.useState(false);
  const [gradeType, setGradeType] = React.useState('');
  const [score, setScore] = React.useState('');
  const [achievements, setAchievements] = React.useState('');
  const [showAdvanced, setShowAdvanced] = React.useState(false);
  // `data` is intentionally unread: the card always adds a fresh entry.
  void data;

  React.useEffect(() => {
    const valid = institution.trim().length > 0 || degree.trim().length > 0;
    onValidityChange(valid);
    const achievementsList = achievements
      .split('\n')
      .map((a) => a.trim())
      .filter(Boolean);
    onChange({
      education: {
        institution,
        degree,
        specialization,
        location,
        startYear,
        endYear,
        currentlyStudying,
        gradeType: (gradeType || null) as 'cgpa' | 'gpa' | 'percentage' | null,
        score,
        achievements: achievementsList,
        years: educationYearsDisplay({ startYear, endYear, currentlyStudying }),
        // Compose the structured extras into `description` so they actually
        // render in every template + the PDF (which only display
        // institution/degree/years/description). The dedicated fields are still
        // sent for the profile/future structured rendering.
        description: composeEducationDescription({
          specialization,
          gradeType,
          score,
          achievements: achievementsList,
        }),
      },
      next_section: null,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    institution,
    degree,
    specialization,
    location,
    startYear,
    endYear,
    currentlyStudying,
    gradeType,
    score,
    achievements,
  ]);

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="edu-institution">School / University</Label>
          <Input
            id="edu-institution"
            value={institution}
            onChange={(e) => setInstitution(e.target.value)}
            placeholder="Massachusetts Institute of Technology"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="edu-degree">Degree</Label>
          <Input
            id="edu-degree"
            value={degree}
            onChange={(e) => setDegree(e.target.value)}
            placeholder="B.Sc. Computer Science"
          />
        </div>
      </div>

      <button
        type="button"
        onClick={() => setShowAdvanced((s) => !s)}
        className="text-xs text-[var(--muted-foreground)] underline-offset-2 hover:underline"
        aria-expanded={showAdvanced}
      >
        {showAdvanced ? 'Hide details' : 'Add details (dates, grade, achievements)'}
      </button>

      {showAdvanced && (
        <div className="space-y-3 rounded-[var(--radius-at-md)] border border-[var(--border)] p-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="edu-specialization">Specialization</Label>
              <Input
                id="edu-specialization"
                value={specialization}
                onChange={(e) => setSpecialization(e.target.value)}
                placeholder="Machine Learning"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edu-location">Location</Label>
              <Input
                id="edu-location"
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="Cambridge, MA"
              />
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="edu-start">Start year</Label>
              <Input
                id="edu-start"
                value={startYear}
                onChange={(e) => setStartYear(e.target.value)}
                placeholder="2019"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edu-end">End year</Label>
              <Input
                id="edu-end"
                value={endYear}
                onChange={(e) => setEndYear(e.target.value)}
                placeholder="2023"
                disabled={currentlyStudying}
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={currentlyStudying}
              onChange={(e) => setCurrentlyStudying(e.target.checked)}
            />
            I&apos;m currently studying here
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="edu-grade-type">Grade type</Label>
              <select
                id="edu-grade-type"
                value={gradeType}
                onChange={(e) => setGradeType(e.target.value)}
                className="flex h-10 w-full items-center rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] px-3 text-sm"
              >
                <option value="">None</option>
                <option value="cgpa">CGPA</option>
                <option value="gpa">GPA</option>
                <option value="percentage">Percentage</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edu-score">Score</Label>
              <Input
                id="edu-score"
                value={score}
                onChange={(e) => setScore(e.target.value)}
                placeholder="3.9 / 4.0"
                disabled={!gradeType}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="edu-achievements">Achievements (one per line)</Label>
            <textarea
              id="edu-achievements"
              value={achievements}
              onChange={(e) => setAchievements(e.target.value)}
              placeholder={'Dean&apos;s List\nGraduated with honors'}
              className="min-h-20 w-full rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] p-2 text-sm"
            />
          </div>
        </div>
      )}
    </div>
  );
}

/** A small editable bullet list with an optional "draft with AI" helper. */
function BulletsEditor({
  bullets,
  onBulletsChange,
  onDraft,
  drafting,
  canDraft,
}: {
  bullets: string[];
  onBulletsChange: (next: string[]) => void;
  onDraft: () => void;
  drafting: boolean;
  canDraft: boolean;
}) {
  const draftMsg = useRotatingMessages(
    ['Drafting your highlights…', 'Grounded in what you described — nothing invented.'],
    { active: drafting, intervalMs: 2000 }
  );
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>Highlights</Label>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onDraft}
          loading={drafting}
          disabled={!canDraft}
        >
          <Sparkles className="h-3.5 w-3.5" /> Draft with AI
        </Button>
      </div>
      {drafting && (
        <p className="text-xs text-[var(--muted-foreground)]" role="status" aria-live="polite">
          {draftMsg}
        </p>
      )}
      {bullets.map((bullet, i) => (
        <div key={i} className="flex gap-2">
          <Input
            value={bullet}
            onChange={(e) => {
              const next = [...bullets];
              next[i] = e.target.value;
              onBulletsChange(next);
            }}
            placeholder="Shipped X, improving Y by Z%"
            aria-label={`Highlight ${i + 1}`}
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Remove highlight ${i + 1}`}
            onClick={() => onBulletsChange(bullets.filter((_, j) => j !== i))}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onBulletsChange([...bullets, ''])}
      >
        <Plus className="h-3.5 w-3.5" /> Add highlight
      </Button>
    </div>
  );
}

/** A "paste a blob → AI extracts structured entries" sub-surface (W-P2.2). */
function PasteExtractor({
  section,
  onExtracted,
}: {
  section: ResumeWizardSection;
  onExtracted: (entries: ResumeWizardParsedEntry[]) => void;
}) {
  const [text, setText] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const busyMsg = useRotatingMessages(
    ['Reading your paste…', 'Splitting into roles…', 'Structuring the fields…'],
    { active: busy, intervalMs: 1800 }
  );

  async function extract() {
    setBusy(true);
    setError('');
    try {
      const res = await assistResumeWizard({ kind: 'parse_entries', section, text: text.trim() });
      if (!res.entries.length) {
        setError('No entries detected — try manual entry.');
        return;
      }
      onExtracted(res.entries);
    } catch {
      setError('Could not read that. Try manual entry.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Paste one or more roles here — company, title, dates and bullets — and AI will split them into fields for you to confirm."
        className="min-h-28"
        aria-label="Paste your experience"
      />
      {error && <p className="text-xs text-[var(--at-error,var(--destructive))]">{error}</p>}
      {busy && (
        <p className="text-xs text-[var(--muted-foreground)]" role="status" aria-live="polite">
          {busyMsg}
        </p>
      )}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={extract}
        loading={busy}
        disabled={text.trim().length === 0}
      >
        <Sparkles className="h-3.5 w-3.5" /> Extract fields
      </Button>
    </div>
  );
}

/** Experience/Internship: structured fields + AI-drafted bullets + paste-to-parse (W-P2.2). */
export function ExperienceCard({
  section,
  onChange,
  onValidityChange,
}: FieldProps & { section: ResumeWizardSection }) {
  const [mode, setMode] = React.useState<'manual' | 'paste'>('manual');
  const [title, setTitle] = React.useState('');
  const [company, setCompany] = React.useState('');
  const [location, setLocation] = React.useState('');
  const [start, setStart] = React.useState('');
  const [end, setEnd] = React.useState('');
  const [current, setCurrent] = React.useState(false);
  const [bullets, setBullets] = React.useState<string[]>([]);
  const [describe, setDescribe] = React.useState('');
  const [drafting, setDrafting] = React.useState(false);
  const [parsed, setParsed] = React.useState<ResumeWizardParsedEntry[]>([]);
  const [error, setError] = React.useState('');

  React.useEffect(() => {
    if (mode === 'paste') {
      const entries: ExperienceInput[] = parsed.map((e) => ({
        title: e.title ?? '',
        company: e.company ?? '',
        location: e.location ?? '',
        years: e.years ?? '',
        description: e.description ?? [],
      }));
      onValidityChange(entries.length > 0);
      onChange({ experiences: entries, next_section: null });
    } else {
      const entry: ExperienceInput = {
        title,
        company,
        location,
        years: composeYears(start, end, current),
        current,
        description: bullets.map((b) => b.trim()).filter(Boolean),
      };
      onValidityChange(title.trim().length > 0 || company.trim().length > 0);
      onChange({ experiences: [entry], next_section: null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, parsed, title, company, location, start, end, current, bullets]);

  async function draftBullets() {
    setDrafting(true);
    setError('');
    try {
      const res = await assistResumeWizard({
        kind: 'draft_bullets',
        section,
        title,
        company,
        text: describe.trim(),
      });
      if (res.bullets.length) setBullets((b) => [...b.filter((x) => x.trim()), ...res.bullets]);
    } catch {
      setError('Could not draft highlights.');
    } finally {
      setDrafting(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-2 text-xs">
        <button
          type="button"
          onClick={() => setMode('manual')}
          className={
            mode === 'manual'
              ? 'font-semibold text-[var(--foreground)]'
              : 'text-[var(--muted-foreground)]'
          }
        >
          Enter manually
        </button>
        <span aria-hidden className="text-[var(--muted-foreground)]">
          ·
        </span>
        <button
          type="button"
          onClick={() => setMode('paste')}
          className={
            mode === 'paste'
              ? 'font-semibold text-[var(--foreground)]'
              : 'text-[var(--muted-foreground)]'
          }
        >
          Paste &amp; auto-fill
        </button>
      </div>

      {mode === 'paste' ? (
        <div className="space-y-2">
          <PasteExtractor section={section} onExtracted={setParsed} />
          {parsed.length > 0 && (
            <ul className="space-y-1 rounded-[var(--radius-at-md)] border border-[var(--border)] p-2 text-xs">
              {parsed.map((e, i) => (
                <li key={i}>
                  <strong>{e.title || 'Role'}</strong>
                  {e.company ? ` · ${e.company}` : ''}
                  {e.years ? ` · ${e.years}` : ''} — {(e.description ?? []).length} highlight(s)
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="exp-title">Title</Label>
              <Input
                id="exp-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Full Stack Engineer"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="exp-company">Company</Label>
              <Input
                id="exp-company"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="TechStax"
              />
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="exp-location">Location</Label>
              <Input
                id="exp-location"
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="Remote"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="exp-start">Start</Label>
              <Input
                id="exp-start"
                value={start}
                onChange={(e) => setStart(e.target.value)}
                placeholder="Jul 2025"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="exp-end">End</Label>
              <Input
                id="exp-end"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
                placeholder="Jan 2026"
                disabled={current}
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={current}
              onChange={(e) => setCurrent(e.target.checked)}
            />
            I currently work here
          </label>
          <div className="space-y-1.5">
            <Label htmlFor="exp-describe">What did you do? (AI turns this into highlights)</Label>
            <Textarea
              id="exp-describe"
              value={describe}
              onChange={(e) => setDescribe(e.target.value)}
              placeholder="Built backend APIs with FastAPI; automated workflows with LLMs; cut manual time ~25%."
              className="min-h-20"
            />
          </div>
          <BulletsEditor
            bullets={bullets}
            onBulletsChange={setBullets}
            onDraft={draftBullets}
            drafting={drafting}
            canDraft={describe.trim().length > 0}
          />
        </>
      )}
      {error && <p className="text-xs text-[var(--at-error,var(--destructive))]">{error}</p>}
    </div>
  );
}

/** Projects: structured fields + AI-drafted bullets + paste-to-parse (W-P2.2). */
export function ProjectCard({ onChange, onValidityChange }: FieldProps) {
  const [mode, setMode] = React.useState<'manual' | 'paste'>('manual');
  const [name, setName] = React.useState('');
  const [role, setRole] = React.useState('');
  const [years, setYears] = React.useState('');
  const [github, setGithub] = React.useState('');
  const [website, setWebsite] = React.useState('');
  const [bullets, setBullets] = React.useState<string[]>([]);
  const [describe, setDescribe] = React.useState('');
  const [drafting, setDrafting] = React.useState(false);
  const [parsed, setParsed] = React.useState<ResumeWizardParsedEntry[]>([]);
  const [error, setError] = React.useState('');

  React.useEffect(() => {
    if (mode === 'paste') {
      const entries: ProjectInput[] = parsed.map((e) => ({
        name: e.name || e.title || '',
        role: e.role ?? '',
        years: e.years ?? '',
        description: e.description ?? [],
      }));
      onValidityChange(entries.some((e) => (e.name ?? '').trim().length > 0));
      onChange({ projects: entries, next_section: null });
    } else {
      onValidityChange(name.trim().length > 0);
      onChange({
        projects: [
          {
            name,
            role,
            years,
            github,
            website,
            description: bullets.map((b) => b.trim()).filter(Boolean),
          },
        ],
        next_section: null,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, parsed, name, role, years, github, website, bullets]);

  async function draftBullets() {
    setDrafting(true);
    setError('');
    try {
      const res = await assistResumeWizard({
        kind: 'draft_bullets',
        section: 'personalProjects',
        title: name,
        company: role,
        text: describe.trim(),
      });
      if (res.bullets.length) setBullets((b) => [...b.filter((x) => x.trim()), ...res.bullets]);
    } catch {
      setError('Could not draft highlights.');
    } finally {
      setDrafting(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-2 text-xs">
        <button
          type="button"
          onClick={() => setMode('manual')}
          className={
            mode === 'manual'
              ? 'font-semibold text-[var(--foreground)]'
              : 'text-[var(--muted-foreground)]'
          }
        >
          Enter manually
        </button>
        <span aria-hidden className="text-[var(--muted-foreground)]">
          ·
        </span>
        <button
          type="button"
          onClick={() => setMode('paste')}
          className={
            mode === 'paste'
              ? 'font-semibold text-[var(--foreground)]'
              : 'text-[var(--muted-foreground)]'
          }
        >
          Paste &amp; auto-fill
        </button>
      </div>

      {mode === 'paste' ? (
        <PasteExtractor section="personalProjects" onExtracted={setParsed} />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="proj-name">Project name</Label>
              <Input
                id="proj-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Sidecar"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="proj-role">Your role</Label>
              <Input
                id="proj-role"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                placeholder="Author"
              />
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="proj-years">Year(s)</Label>
              <Input
                id="proj-years"
                value={years}
                onChange={(e) => setYears(e.target.value)}
                placeholder="2022"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="proj-github">GitHub</Label>
              <Input
                id="proj-github"
                value={github}
                onChange={(e) => setGithub(e.target.value)}
                placeholder="github.com/you/sidecar"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="proj-website">Link</Label>
              <Input
                id="proj-website"
                value={website}
                onChange={(e) => setWebsite(e.target.value)}
                placeholder="sidecar.dev"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="proj-describe">
              What did you build? (AI turns this into highlights)
            </Label>
            <Textarea
              id="proj-describe"
              value={describe}
              onChange={(e) => setDescribe(e.target.value)}
              placeholder="Built a MERN platform scaling to 300+ users; event-driven chat with Socket.io."
              className="min-h-20"
            />
          </div>
          <BulletsEditor
            bullets={bullets}
            onBulletsChange={setBullets}
            onDraft={draftBullets}
            drafting={drafting}
            canDraft={describe.trim().length > 0}
          />
        </>
      )}
      {error && <p className="text-xs text-[var(--at-error,var(--destructive))]">{error}</p>}
    </div>
  );
}
