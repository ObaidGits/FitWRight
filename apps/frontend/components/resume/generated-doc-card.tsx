'use client';

/**
 * GeneratedDocCard — AI-generated companion documents (cover letter, outreach
 * message) edited inline in the atelier resume editor. Previously these lived
 * only in the legacy /builder advanced editor; this ports them into the primary
 * editor as one kind-parameterized, single-surface card.
 *
 * Behaviour (shared): edit inline, generate/regenerate with streaming AI
 * (cancel + transparent fallback via {@link useStream}), and save explicitly —
 * streamed text is a PREVIEW, persisted only on Save (cost-consent, no silent
 * writes). Documents are meaningful only for tailored resumes (the backend
 * generate endpoints need job context), so generation is gated on `isTailored`.
 *
 * Per-kind differences: a cover letter offers PDF export (print pipeline); an
 * outreach message offers copy-to-clipboard (it's a short paste-anywhere note).
 */
import * as React from 'react';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import Mail from 'lucide-react/dist/esm/icons/mail';
import Copy from 'lucide-react/dist/esm/icons/copy';
import Check from 'lucide-react/dist/esm/icons/check';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { Badge } from '@/components/atelier/badge';
import { Textarea } from '@/components/atelier/input';
import { useToast } from '@/components/atelier/toast';
import { ExportButton } from '@/components/resume/export-button';
import { useStream } from '@/lib/hooks/use-stream';
import { updateCoverLetter, updateOutreachMessage } from '@/lib/api/resume';
import type { StreamKind } from '@/lib/api/resume';

type DocKind = StreamKind; // 'cover-letter' | 'outreach'

interface KindConfig {
  title: string;
  Icon: typeof FileText;
  label: string; // lowercase noun for messages
  save: (resumeId: string, content: string) => Promise<void>;
  supportsPdf: boolean;
  supportsCopy: boolean;
  placeholder: string;
}

const CONFIG: Record<DocKind, KindConfig> = {
  'cover-letter': {
    title: 'Cover letter',
    Icon: FileText,
    label: 'cover letter',
    save: updateCoverLetter,
    supportsPdf: true,
    supportsCopy: false,
    placeholder: 'Generate a cover letter, or write your own here…',
  },
  outreach: {
    title: 'Outreach message',
    Icon: Mail,
    label: 'outreach message',
    save: updateOutreachMessage,
    supportsPdf: false,
    supportsCopy: true,
    placeholder: 'Generate a recruiter outreach message, or write your own here…',
  },
};

export function GeneratedDocCard({
  kind,
  resumeId,
  initialContent,
  isTailored,
  onSaved,
}: {
  kind: DocKind;
  resumeId: string;
  initialContent: string | null | undefined;
  isTailored: boolean;
  onSaved?: () => void;
}) {
  const cfg = CONFIG[kind];
  const { toast } = useToast();
  const stream = useStream(resumeId);
  const [content, setContent] = React.useState(initialContent ?? '');
  const [dirty, setDirty] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const cancelledRef = React.useRef(false);

  React.useEffect(() => {
    setContent(initialContent ?? '');
    setDirty(false);
  }, [initialContent, resumeId]);

  const hasSaved = Boolean((initialContent ?? '').trim());

  async function onGenerate() {
    cancelledRef.current = false;
    const full = await stream.start(kind);
    if (cancelledRef.current) return; // discard a cancelled run's partial text
    if (full && full.trim()) {
      setContent(full);
      setDirty(true);
    } else if (stream.error) {
      toast({ title: `Could not generate a ${cfg.label}`, variant: 'error' });
    }
  }

  function onCancel() {
    cancelledRef.current = true;
    stream.cancel();
  }

  async function onSave() {
    setSaving(true);
    try {
      await cfg.save(resumeId, content);
      setDirty(false);
      toast({ title: `${cfg.title} saved`, variant: 'success' });
      onSaved?.();
    } catch (e) {
      toast({ title: e instanceof Error ? e.message : 'Could not save', variant: 'error' });
    } finally {
      setSaving(false);
    }
  }

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast({ title: 'Could not copy to clipboard', variant: 'error' });
    }
  }

  const displayValue = stream.isStreaming ? stream.text : content;

  return (
    <Card className="space-y-3 p-5">
      <div className="flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--muted-foreground)]">
          <cfg.Icon className="h-4 w-4" /> {cfg.title}
        </h2>
        {dirty && <Badge variant="warning">Unsaved</Badge>}
      </div>

      {!isTailored ? (
        <p className="text-sm text-[var(--muted-foreground)]">
          {cfg.title} is generated from a job description. Tailor this resume to a job first, then
          generate a matching {cfg.label} here.
        </p>
      ) : (
        <>
          <div role="status" aria-live="polite" className="sr-only">
            {stream.isStreaming
              ? `Generating your ${cfg.label}…`
              : dirty
                ? `${cfg.title} draft ready to review.`
                : ''}
          </div>

          <Textarea
            value={displayValue}
            onChange={(e) => {
              setContent(e.target.value);
              setDirty(true);
            }}
            readOnly={stream.isStreaming}
            placeholder={cfg.placeholder}
            className="min-h-48"
            aria-label={`${cfg.title} content`}
          />

          <div className="flex flex-wrap items-center gap-2">
            {stream.isStreaming ? (
              <Button variant="outline" size="sm" onClick={onCancel}>
                Cancel
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                className="text-[var(--at-ai)]"
                onClick={onGenerate}
              >
                <Sparkles className="h-4 w-4" />{' '}
                {hasSaved || content.trim() ? 'Regenerate' : 'Generate'}
              </Button>
            )}
            <Button
              size="sm"
              onClick={onSave}
              loading={saving}
              disabled={!dirty || stream.isStreaming}
            >
              Save
            </Button>
            {cfg.supportsCopy && content.trim() && !stream.isStreaming && (
              <Button variant="ghost" size="sm" onClick={onCopy}>
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                {copied ? 'Copied' : 'Copy'}
              </Button>
            )}
            {cfg.supportsPdf && hasSaved && !dirty && (
              <ExportButton kind="cover-letter" resumeId={resumeId} label="Export PDF" />
            )}
            {cfg.supportsPdf && dirty && !stream.isStreaming && (
              <span className="text-xs text-[var(--muted-foreground)]">Save to export.</span>
            )}
          </div>
          <p className="inline-flex items-center gap-1 text-xs text-[var(--muted-foreground)]">
            <Sparkles className="h-3 w-3" /> AI drafts are a preview — nothing is saved until you
            click Save.
          </p>
        </>
      )}
    </Card>
  );
}
