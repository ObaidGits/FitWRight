'use client';

/**
 * Contextual "Ask AI" dialog (Task 10.2 / Req 27.1, 27.6).
 *
 * A reusable, cost-aware AI affordance that rewrites a resume item
 * (skills / experience / project) from a natural-language instruction and
 * shows a PREVIEW-BEFORE-APPLY diff. No AI call fires until the user picks an
 * intent or submits an instruction — nothing is unsolicited.
 */
import * as React from 'react';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import ArrowRight from 'lucide-react/dist/esm/icons/arrow-right';

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/atelier/dialog';
import { Button } from '@/components/atelier/button';
import { Textarea } from '@/components/atelier/input';
import { Badge } from '@/components/atelier/badge';
import { useToast } from '@/components/atelier/toast';
import { AiProgress } from '@/components/ai/ai-progress';
import { ASK_AI_STAGES, ASK_AI_MESSAGES, ESTIMATE_SHORT } from '@/lib/ai-progress-copy';
import { regenerateItems, type RegeneratedItem } from '@/lib/api/enrichment';

export type AskAiItemType = 'experience' | 'project' | 'skills';

export interface AskAiTarget {
  resumeId: string;
  itemId: string;
  itemType: AskAiItemType;
  title: string;
  subtitle?: string;
  currentContent: string[];
}

interface AskAiDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  target: AskAiTarget | null;
  /** Applies the accepted content back into the editor state. */
  onApply: (target: AskAiTarget, newContent: string[]) => void;
}

/** Quick-intent presets → each maps to a concrete instruction (Req 27.1). */
const INTENTS: { id: string; label: string; instruction: string }[] = [
  {
    id: 'quantify',
    label: 'Add metrics',
    instruction:
      'Rewrite to quantify impact with concrete numbers, percentages, or scale wherever the underlying facts support it. Do not invent figures.',
  },
  {
    id: 'shorten',
    label: 'Make it tighter',
    instruction: 'Rewrite to be more concise and punchy while preserving every factual detail.',
  },
  {
    id: 'seniority',
    label: 'Raise seniority',
    instruction:
      'Rewrite to emphasise leadership, ownership, and strategic impact appropriate for a senior candidate, without overstating the actual role.',
  },
  {
    id: 'clarity',
    label: 'Improve clarity',
    instruction: 'Rewrite for clarity and strong action verbs, removing filler and passive voice.',
  },
];

export function AskAiDialog({ open, onOpenChange, target, onApply }: AskAiDialogProps) {
  const { toast } = useToast();
  const [instruction, setInstruction] = React.useState('');
  const [loading, setLoading] = React.useState(false);
  const [result, setResult] = React.useState<RegeneratedItem | null>(null);

  // Reset transient state whenever the dialog opens for a new target.
  React.useEffect(() => {
    if (open) {
      setInstruction('');
      setResult(null);
      setLoading(false);
    }
  }, [open, target?.itemId]);

  async function run(rawInstruction: string) {
    if (!target) return;
    const trimmed = rawInstruction.trim();
    if (!trimmed) {
      toast({ title: 'Tell the AI what to change first', variant: 'error' });
      return;
    }
    setLoading(true);
    setResult(null);
    try {
      const res = await regenerateItems({
        resume_id: target.resumeId,
        instruction: trimmed,
        items: [
          {
            item_id: target.itemId,
            item_type: target.itemType,
            title: target.title,
            subtitle: target.subtitle,
            current_content: target.currentContent,
          },
        ],
      });
      const item = res.regenerated_items[0];
      if (!item) {
        toast({ title: 'The AI could not rewrite this item', variant: 'error' });
        return;
      }
      setResult(item);
    } catch {
      toast({ title: 'AI request failed. Please try again.', variant: 'error' });
    } finally {
      setLoading(false);
    }
  }

  function apply() {
    if (!target || !result) return;
    onApply(target, result.new_content);
    toast({ title: 'Applied AI suggestion', variant: 'success' });
    onOpenChange(false);
  }

  const isSkills = target?.itemType === 'skills';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-[var(--at-ai)]" /> Ask AI
          </DialogTitle>
          <DialogDescription>
            {target
              ? `Rewrite “${target.title}”. Preview the result before applying — nothing changes until you accept.`
              : ''}
          </DialogDescription>
        </DialogHeader>

        {/* Quick intents */}
        <div className="flex flex-wrap gap-2">
          {INTENTS.map((intent) => (
            <Button
              key={intent.id}
              variant="outline"
              size="sm"
              disabled={loading}
              onClick={() => {
                setInstruction(intent.instruction);
                void run(intent.instruction);
              }}
            >
              {intent.label}
            </Button>
          ))}
        </div>

        {/* Freeform instruction */}
        <div className="space-y-2">
          <Textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="Or describe exactly what to change…"
            className="min-h-20"
            disabled={loading}
          />
          <div className="flex items-center justify-between">
            <span className="text-xs text-[var(--muted-foreground)]">
              Uses your AI provider — one request per run.
            </span>
            <Button
              size="sm"
              onClick={() => void run(instruction)}
              loading={loading}
              disabled={loading}
            >
              <Sparkles className="h-4 w-4" /> Generate
            </Button>
          </div>
        </div>

        {/* Generating — compact honest timeline instead of a blank dialog. */}
        {loading && !result && (
          <div className="rounded-[var(--radius-at-lg)] border border-[var(--border)] p-4">
            <AiProgress
              stages={ASK_AI_STAGES}
              active
              messages={ASK_AI_MESSAGES}
              estimate={ESTIMATE_SHORT}
            />
          </div>
        )}

        {/* Preview */}
        {result && (
          <div className="space-y-3 rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--secondary)]/40 p-4">
            {result.diff_summary && (
              <p className="text-sm text-[var(--muted-foreground)]">{result.diff_summary}</p>
            )}
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <Badge variant="neutral" className="mb-2">
                  Current
                </Badge>
                <ul className="space-y-1 text-sm text-[var(--muted-foreground)]">
                  {target?.currentContent.length ? (
                    target.currentContent.map((c, i) => (
                      <li key={i} className={isSkills ? 'inline' : 'list-inside list-disc'}>
                        {c}
                        {isSkills && i < target.currentContent.length - 1 ? ', ' : ''}
                      </li>
                    ))
                  ) : (
                    <li className="italic">Empty</li>
                  )}
                </ul>
              </div>
              <div>
                <Badge variant="success" className="mb-2">
                  AI suggestion
                </Badge>
                <ul className="space-y-1 text-sm text-[var(--foreground)]">
                  {result.new_content.map((c, i) => (
                    <li key={i} className={isSkills ? 'inline' : 'list-inside list-disc'}>
                      {c}
                      {isSkills && i < result.new_content.length - 1 ? ', ' : ''}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            <p className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
              <ArrowRight className="h-3.5 w-3.5" /> Review for accuracy — keep only claims that are
              true.
            </p>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={apply} disabled={!result}>
            Apply suggestion
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
