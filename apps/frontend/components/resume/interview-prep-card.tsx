'use client';

/**
 * InterviewPrepCard - AI interview preparation ported into the atelier resume
 * editor (previously only in the legacy /builder advanced editor). Self-contained
 * (no dependency on components/builder) so the legacy tree can be retired without
 * touching the atelier editor.
 *
 * Interview prep is a single, structured generation (not token-streamed), so it
 * uses an explicit loading state on the button. It's meaningful only for tailored
 * resumes (the backend needs job context), so it's gated on `isTailored`.
 */
import * as React from 'react';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import Target from 'lucide-react/dist/esm/icons/target';
import MessageSquare from 'lucide-react/dist/esm/icons/message-square-text';
import ListChecks from 'lucide-react/dist/esm/icons/list-checks';
import AlertTriangle from 'lucide-react/dist/esm/icons/triangle-alert';
import Lightbulb from 'lucide-react/dist/esm/icons/lightbulb';
import ChevronDown from 'lucide-react/dist/esm/icons/chevron-down';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { useToast } from '@/components/atelier/toast';
import { AiProgress } from '@/components/ai/ai-progress';
import {
  INTERVIEW_PREP_STAGES,
  INTERVIEW_PREP_MESSAGES,
  ESTIMATE_MEDIUM,
} from '@/lib/ai-progress-copy';
import { generateInterviewPrep } from '@/lib/api/resume';
import type {
  InterviewPrepData,
  InterviewPrepQuestion,
  InterviewPrepSkillGap,
} from '@/components/common/resume_previewer_context';

function BulletList({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <ul className="space-y-1.5">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2 text-sm text-[var(--muted-foreground)]">
          <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--primary)]" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

function QuestionList({ items }: { items: InterviewPrepQuestion[] }) {
  if (!items.length) return null;
  return (
    <div className="space-y-2.5">
      {items.map((q, i) => (
        <div
          key={i}
          className="rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--at-surface-2)] p-3"
        >
          <p className="text-sm font-medium text-[var(--foreground)]">{q.question}</p>
          {q.focus_area && (
            <p className="mt-1 text-[11px] uppercase tracking-wide text-[var(--primary)]">
              {q.focus_area}
            </p>
          )}
          {q.suggested_answer_points.length > 0 && (
            <div className="mt-2">
              <BulletList items={q.suggested_answer_points} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function SkillGapList({ items }: { items: InterviewPrepSkillGap[] }) {
  if (!items.length) return null;
  return (
    <div className="space-y-2.5">
      {items.map((g, i) => (
        <div
          key={i}
          className="rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--at-surface-2)] p-3"
        >
          <p className="text-sm font-semibold text-[var(--foreground)]">{g.skill}</p>
          <p className="mt-1.5 text-sm text-[var(--muted-foreground)]">
            <span className="text-[11px] font-semibold uppercase">Why it matters: </span>
            {g.why_it_matters}
          </p>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            <span className="text-[11px] font-semibold uppercase">How to prepare: </span>
            {g.preparation_suggestion}
          </p>
        </div>
      ))}
    </div>
  );
}

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
        <Icon className="h-3.5 w-3.5 text-[var(--primary)]" /> {title}
      </h3>
      {children}
    </section>
  );
}

function hasContent(prep: InterviewPrepData): boolean {
  return (
    prep.role_fit_analysis.length > 0 ||
    prep.resume_questions.length > 0 ||
    prep.project_follow_ups.length > 0 ||
    prep.skill_gaps.length > 0 ||
    prep.talking_points.length > 0
  );
}

export function InterviewPrepCard({
  resumeId,
  initialPrep,
  isTailored,
  onGenerated,
}: {
  resumeId: string;
  initialPrep: InterviewPrepData | null | undefined;
  isTailored: boolean;
  onGenerated?: () => void;
}) {
  const { toast } = useToast();
  const [prep, setPrep] = React.useState<InterviewPrepData | null>(initialPrep ?? null);
  const [generating, setGenerating] = React.useState(false);
  const [expanded, setExpanded] = React.useState(false);

  React.useEffect(() => {
    setPrep(initialPrep ?? null);
  }, [initialPrep, resumeId]);

  async function onGenerate() {
    setGenerating(true);
    try {
      const result = await generateInterviewPrep(resumeId);
      setPrep(result);
      setExpanded(true);
      onGenerated?.();
    } catch (e) {
      toast({
        title: e instanceof Error ? e.message : 'Could not generate interview prep',
        variant: 'error',
      });
    } finally {
      setGenerating(false);
    }
  }

  const populated = prep && hasContent(prep);

  return (
    <Card className="space-y-3 p-5">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--muted-foreground)]">
          <MessageSquare className="h-4 w-4" /> Interview prep
        </h2>
        {populated && (
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
          Interview prep is generated from a job description. Tailor this resume to a job first,
          then generate likely questions, talking points, and skill-gap coaching here.
        </p>
      ) : (
        <>
          {!populated && (
            <p className="text-sm text-[var(--muted-foreground)]">
              Generate likely interview questions, suggested answer points, skill-gap coaching, and
              talking points tailored to this role.
            </p>
          )}

          {!generating && (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                className="text-[var(--at-ai)]"
                onClick={onGenerate}
                loading={generating}
              >
                <Sparkles className="h-4 w-4" />{' '}
                {populated ? 'Regenerate' : 'Generate interview prep'}
              </Button>
            </div>
          )}

          {generating && (
            <div className="rounded-[var(--radius-at-md)] border border-[var(--border)] p-4">
              <AiProgress
                stages={INTERVIEW_PREP_STAGES}
                active
                messages={INTERVIEW_PREP_MESSAGES}
                estimate={ESTIMATE_MEDIUM}
              />
            </div>
          )}

          {populated && expanded && (
            <div className="space-y-4 pt-1">
              {prep!.role_fit_analysis.length > 0 && (
                <Section title="Role fit" icon={Target}>
                  <BulletList items={prep!.role_fit_analysis} />
                </Section>
              )}
              {prep!.resume_questions.length > 0 && (
                <Section title="Likely questions" icon={MessageSquare}>
                  <QuestionList items={prep!.resume_questions} />
                </Section>
              )}
              {prep!.project_follow_ups.length > 0 && (
                <Section title="Project follow-ups" icon={ListChecks}>
                  <QuestionList items={prep!.project_follow_ups} />
                </Section>
              )}
              {prep!.skill_gaps.length > 0 && (
                <Section title="Skill gaps" icon={AlertTriangle}>
                  <SkillGapList items={prep!.skill_gaps} />
                </Section>
              )}
              {prep!.talking_points.length > 0 && (
                <Section title="Talking points" icon={Lightbulb}>
                  <BulletList items={prep!.talking_points} />
                </Section>
              )}
            </div>
          )}
        </>
      )}
    </Card>
  );
}
