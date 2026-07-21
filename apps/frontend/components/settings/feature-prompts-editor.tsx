'use client';

/**
 * Custom feature-prompt templates (Req 19) - lets users tailor the cover-letter
 * and outreach-message generation prompts. Backend validates required
 * placeholders (e.g. {job_description}); on a 422 it returns the missing tokens,
 * which we surface inline so the user knows exactly what to add. Empty save
 * resets a prompt to its default.
 */
import * as React from 'react';
import RotateCcw from 'lucide-react/dist/esm/icons/rotate-ccw';

import { Card } from '@/components/atelier/card';
import { Button } from '@/components/atelier/button';
import { Label } from '@/components/atelier/label';
import { LoadingSkeleton } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { useFeaturePrompts, useUpdateFeaturePrompts } from '@/features/settings/hooks';
import { FeaturePromptsError } from '@/lib/api/config';

type Field = 'cover_letter_prompt' | 'outreach_message_prompt';

function PromptField({
  id,
  label,
  value,
  defaultValue,
  onChange,
  onReset,
  error,
}: {
  id: string;
  label: string;
  value: string;
  defaultValue: string;
  onChange: (v: string) => void;
  onReset: () => void;
  error?: string[];
}) {
  const isDefault = value.trim() === defaultValue.trim();
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label htmlFor={id}>{label}</Label>
        {!isDefault && (
          <button
            type="button"
            onClick={onReset}
            className="flex items-center gap-1 text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
          >
            <RotateCcw className="h-3 w-3" /> Reset to default
          </button>
        )}
      </div>
      <textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={6}
        spellCheck={false}
        className="w-full resize-y rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--background)] p-3 font-mono text-xs text-[var(--foreground)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
      />
      {error && error.length > 0 && (
        <p className="text-xs text-[var(--destructive)]">
          Missing required placeholder{error.length > 1 ? 's' : ''}: {error.join(', ')}
        </p>
      )}
    </div>
  );
}

export function FeaturePromptsEditor() {
  const prompts = useFeaturePrompts();
  const update = useUpdateFeaturePrompts();
  const { toast } = useToast();

  const [cover, setCover] = React.useState('');
  const [outreach, setOutreach] = React.useState('');
  const [errors, setErrors] = React.useState<Partial<Record<Field, string[]>>>({});

  React.useEffect(() => {
    if (prompts.data) {
      setCover(prompts.data.cover_letter_prompt || prompts.data.cover_letter_default);
      setOutreach(prompts.data.outreach_message_prompt || prompts.data.outreach_message_default);
    }
  }, [prompts.data]);

  if (prompts.isLoading) return <LoadingSkeleton rows={2} />;
  if (prompts.isError || !prompts.data) return null;

  const data = prompts.data;

  async function onSave() {
    setErrors({});
    try {
      await update.mutateAsync({
        cover_letter_prompt: cover.trim(),
        outreach_message_prompt: outreach.trim(),
      });
      toast({ title: 'Prompts saved', variant: 'success' });
    } catch (err) {
      if (err instanceof FeaturePromptsError) {
        setErrors({ [err.detail.field]: err.detail.missing });
        toast({ title: 'Prompt is missing required placeholders', variant: 'error' });
      } else {
        toast({ title: (err as Error)?.message || 'Could not save prompts', variant: 'error' });
      }
    }
  }

  return (
    <Card className="space-y-4 p-6">
      <div>
        <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">
          Custom generation prompts
        </h2>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">
          Tailor how the AI writes your cover letters and outreach messages. Keep the required
          placeholders (in braces) so the AI receives your resume and the job details.
        </p>
      </div>
      <PromptField
        id="cover-prompt"
        label="Cover letter prompt"
        value={cover}
        defaultValue={data.cover_letter_default}
        onChange={setCover}
        onReset={() => setCover(data.cover_letter_default)}
        error={errors.cover_letter_prompt}
      />
      <PromptField
        id="outreach-prompt"
        label="Outreach message prompt"
        value={outreach}
        defaultValue={data.outreach_message_default}
        onChange={setOutreach}
        onReset={() => setOutreach(data.outreach_message_default)}
        error={errors.outreach_message_prompt}
      />
      <Button onClick={onSave} loading={update.isPending}>
        Save prompts
      </Button>
    </Card>
  );
}
