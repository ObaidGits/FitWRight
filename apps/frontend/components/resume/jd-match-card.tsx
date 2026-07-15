'use client';

/**
 * JdMatchCard — JD-vs-resume keyword match, ported into the atelier resume
 * editor (previously only in the legacy /builder advanced editor). Self-contained
 * (reuses only the pure keyword-matcher utils), so the legacy tree can be retired
 * without touching the atelier editor.
 *
 * It highlights the LIVE-edited resume (the caller passes the current preview
 * data), so the match reflects unsaved edits. Only meaningful for tailored
 * resumes (they have an associated job description), so it's gated on `isTailored`.
 */
import * as React from 'react';
import Target from 'lucide-react/dist/esm/icons/target';
import CircleCheck from 'lucide-react/dist/esm/icons/circle-check';
import ChevronDown from 'lucide-react/dist/esm/icons/chevron-down';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { useToast } from '@/components/atelier/toast';
import { fetchJobDescription } from '@/lib/api/resume';
import {
  extractKeywords,
  calculateMatchStats,
  segmentTextByKeywords,
} from '@/lib/utils/keyword-matcher';
import type { ResumeData } from '@/components/dashboard/resume-component';

function buildResumeText(data: ResumeData): string {
  const parts: string[] = [];
  if (data.summary) parts.push(data.summary);
  data.workExperience?.forEach((e) => {
    if (e.title) parts.push(e.title);
    if (e.company) parts.push(e.company);
    e.description?.forEach((d) => parts.push(d));
  });
  data.education?.forEach((e) => {
    if (e.degree) parts.push(e.degree);
    if (e.institution) parts.push(e.institution);
  });
  data.personalProjects?.forEach((p) => {
    if (p.name) parts.push(p.name);
    if (p.role) parts.push(p.role);
    p.description?.forEach((d) => parts.push(d));
  });
  data.additional?.technicalSkills?.forEach((s) => parts.push(s));
  data.additional?.languages?.forEach((l) => parts.push(l));
  data.additional?.certificationsTraining?.forEach((c) => parts.push(c));
  return parts.join(' ');
}

function HighlightedText({ text, keywords }: { text: string; keywords: Set<string> }) {
  const segments = React.useMemo(() => segmentTextByKeywords(text, keywords), [text, keywords]);
  return (
    <span>
      {segments.map((seg, i) =>
        seg.isMatch ? (
          <mark
            key={i}
            className="rounded-[var(--radius-at-sm)] bg-[var(--at-warning)]/25 px-0.5 text-[var(--foreground)]"
          >
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        )
      )}
    </span>
  );
}

function SkillTag({ text, keywords }: { text: string; keywords: Set<string> }) {
  const isMatch = keywords.has(text.toLowerCase());
  return (
    <span
      className={`inline-block rounded-[var(--radius-at-sm)] px-2 py-0.5 text-xs ${
        isMatch
          ? 'bg-[var(--at-warning)]/25 font-medium text-[var(--foreground)]'
          : 'bg-[var(--secondary)] text-[var(--muted-foreground)]'
      }`}
    >
      {text}
    </span>
  );
}

export function JdMatchCard({
  resumeId,
  resumeData,
  isTailored,
}: {
  resumeId: string;
  resumeData: ResumeData;
  isTailored: boolean;
}) {
  const { toast } = useToast();
  const [jd, setJd] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [expanded, setExpanded] = React.useState(false);

  const keywords = React.useMemo(() => (jd ? extractKeywords(jd) : new Set<string>()), [jd]);
  const resumeText = React.useMemo(() => buildResumeText(resumeData), [resumeData]);
  const stats = React.useMemo(
    () => calculateMatchStats(resumeText, keywords),
    [resumeText, keywords]
  );

  async function onCheck() {
    if (jd) {
      setExpanded((v) => !v);
      return;
    }
    setLoading(true);
    try {
      const res = await fetchJobDescription(resumeId);
      setJd(res.content ?? '');
      setExpanded(true);
    } catch (e) {
      toast({
        title: e instanceof Error ? e.message : 'Could not load the job description',
        variant: 'error',
      });
    } finally {
      setLoading(false);
    }
  }

  const pct = stats.matchPercentage;
  const pctTone =
    pct >= 50
      ? 'text-[var(--at-success)]'
      : pct >= 30
        ? 'text-[var(--at-warning)]'
        : 'text-[var(--destructive)]';

  const skills = (resumeData.additional?.technicalSkills ?? []).filter(
    (s): s is string => typeof s === 'string' && s.trim() !== ''
  );

  return (
    <Card className="space-y-3 p-5">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--muted-foreground)]">
          <Target className="h-4 w-4" /> Keyword match
        </h2>
        {jd && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="inline-flex items-center gap-1 text-xs text-[var(--primary)] hover:underline"
            aria-expanded={expanded}
          >
            {expanded ? 'Hide' : 'Show'}
            <ChevronDown
              className={`h-3.5 w-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`}
            />
          </button>
        )}
      </div>

      {!isTailored ? (
        <p className="text-sm text-[var(--muted-foreground)]">
          Keyword match compares this resume against the job you tailored it to. Tailor this resume
          to a job first, then check which of its keywords you already cover.
        </p>
      ) : (
        <>
          {jd ? (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
              <span className="inline-flex items-center gap-1.5">
                <Target className="h-4 w-4 text-[var(--primary)]" />
                {keywords.size} keywords
              </span>
              <span className="inline-flex items-center gap-1.5">
                <CircleCheck className="h-4 w-4 text-[var(--at-success)]" />
                {stats.matchCount} matched
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span className="text-[var(--muted-foreground)]">Match rate</span>
                <span className={`text-base font-bold ${pctTone}`}>{pct}%</span>
              </span>
            </div>
          ) : (
            <p className="text-sm text-[var(--muted-foreground)]">
              See which keywords from the target job appear in your resume — highlighted inline so
              you can spot gaps.
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={onCheck} loading={loading}>
              <Target className="h-4 w-4" />{' '}
              {jd ? (expanded ? 'Hide match' : 'Show match') : 'Check keyword match'}
            </Button>
          </div>

          {jd && expanded && (
            <div className="space-y-4 pt-1">
              <div className="space-y-3">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                  Your resume <span className="font-normal normal-case">(matches highlighted)</span>
                </h3>
                {resumeData.summary && (
                  <p className="text-sm text-[var(--foreground)]">
                    <HighlightedText text={resumeData.summary} keywords={keywords} />
                  </p>
                )}
                {(resumeData.workExperience ?? []).map((exp, i) => (
                  <div key={`exp-${i}`} className="text-sm">
                    <p className="font-medium text-[var(--foreground)]">
                      <HighlightedText text={exp.title || ''} keywords={keywords} />
                      {exp.company && (
                        <>
                          {' — '}
                          <HighlightedText text={exp.company} keywords={keywords} />
                        </>
                      )}
                    </p>
                    {exp.description && exp.description.length > 0 && (
                      <ul className="mt-1 list-inside list-disc space-y-0.5">
                        {exp.description.map((b, j) => (
                          <li key={j} className="text-[var(--foreground)]">
                            <HighlightedText text={b} keywords={keywords} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
                {skills.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs uppercase text-[var(--muted-foreground)]">
                      Skills
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {skills.map((s, i) => (
                        <SkillTag key={i} text={s} keywords={keywords} />
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="space-y-1.5">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                  Job description
                </h3>
                <div className="max-h-56 overflow-y-auto whitespace-pre-wrap rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--at-surface-2)] p-3 text-sm leading-relaxed text-[var(--foreground)]">
                  {jd}
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
