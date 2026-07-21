'use client';

/** Import a resume (Task 7.2 / Req 8) - drag-drop upload + parse status + wizard entry. */
import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import UploadCloud from 'lucide-react/dist/esm/icons/cloud-upload';
import Wand from 'lucide-react/dist/esm/icons/wand-sparkles';
import TriangleAlert from 'lucide-react/dist/esm/icons/triangle-alert';

import Key from 'lucide-react/dist/esm/icons/key-round';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { useToast } from '@/components/atelier/toast';
import { useSystemStatus } from '@/features/home/hooks';
import { invalidateResumeLists } from '@/lib/query/client';
import {
  uploadResumeFile,
  streamUploadResumeFile,
  validateResumeFile,
  STREAM_UNAVAILABLE,
  type ParseStageEvent,
} from '@/features/resumes/upload';
import type { ResumeUploadResponse } from '@/lib/api/resume';
import { AiProgress, ResumeSkeletonPreview } from '@/components/ai/ai-progress';
import {
  PARSE_STAGES,
  PARSE_STREAM_STAGES,
  PARSE_MESSAGES,
  ESTIMATE_PARSE,
} from '@/lib/ai-progress-copy';

type Phase = 'idle' | 'uploading' | 'error';

export default function ImportPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { toast } = useToast();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [phase, setPhase] = React.useState<Phase>('idle');
  const [error, setError] = React.useState<string | null>(null);
  const [dragging, setDragging] = React.useState(false);
  // LIVE parse progress driven by real SSE stage events (falls back to a
  // deterministic timeline when the streaming endpoint is unavailable).
  const [usingStream, setUsingStream] = React.useState(false);
  const [streamStage, setStreamStage] = React.useState<{ active: string; done: string[] }>({
    active: 'received',
    done: [],
  });
  const statusQuery = useSystemStatus();
  const aiUnconfigured = statusQuery.data && !statusQuery.data.llm_configured;
  // Key present but the live health probe failed (invalid key, wrong model, or
  // rate-limited). Import will read the file but fail to structure it, so warn
  // up front instead of letting the user discover it via a failed upload.
  const aiUnhealthy =
    statusQuery.data && statusQuery.data.llm_configured && !statusQuery.data.llm_healthy;

  const finishUpload = React.useCallback(
    (res: ResumeUploadResponse): boolean => {
      invalidateResumeLists(qc);
      if (res.processing_status === 'failed') {
        // The file read fine (a genuinely unreadable/scanned file is rejected
        // upstream with its own message) - this state means AI *structuring*
        // failed. That is almost always a provider issue (missing/invalid key
        // or hit rate limit), so point the user there instead of wrongly
        // blaming the file.
        setError(
          'Your file was uploaded and its text was read, but the AI could not turn it into a structured resume. ' +
            'This usually means your AI provider key is missing, invalid, or has hit its rate limit - ' +
            'check your key in Settings and try again.'
        );
        setPhase('error');
        return false;
      }
      toast({ title: 'Resume uploaded', variant: 'success' });
      router.push(`/resumes/${res.resume_id}`);
      return true;
    },
    [qc, router, toast]
  );

  const handleFile = React.useCallback(
    async (file: File) => {
      const validation = validateResumeFile(file);
      if (validation) {
        setError(validation);
        setPhase('error');
        return;
      }
      setError(null);
      setPhase('uploading');
      setUsingStream(false);
      setStreamStage({ active: 'received', done: [] });

      // Prefer the live streaming endpoint so the user sees honest, real
      // per-stage progress. If it's unavailable (flag off / unsupported), fall
      // back transparently to the non-stream upload + deterministic timeline.
      try {
        const onStage = (ev: ParseStageEvent) => {
          setUsingStream(true);
          setStreamStage((prev) => {
            const done =
              ev.status === 'done' && !prev.done.includes(ev.stage)
                ? [...prev.done, ev.stage]
                : prev.done;
            // Keep `active` sticky - only advance it on a real 'active' event,
            // so a completed stage doesn't blank out the spinner.
            const active = ev.status === 'active' ? ev.stage : prev.active;
            return { active, done };
          });
        };
        const res = await streamUploadResumeFile(file, { onStage });
        finishUpload(res);
        return;
      } catch (e) {
        if (!(e instanceof Error) || e.message !== STREAM_UNAVAILABLE) {
          // A real, user-facing parse/upload error from the stream.
          setError(e instanceof Error ? e.message : 'Upload failed. Please try again.');
          setPhase('error');
          return;
        }
        // Streaming unavailable -> fall through to the non-stream path below.
        setUsingStream(false);
      }

      try {
        const res = await uploadResumeFile(file);
        finishUpload(res);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Upload failed. Please try again.');
        setPhase('error');
      }
    },
    [finishUpload]
  );

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void handleFile(file);
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Add a resume</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Upload an existing resume, or build one from scratch with the wizard.
        </p>
      </div>

      {aiUnconfigured && (
        <Card className="flex items-start gap-3 border-[var(--at-warning)]/40 bg-[var(--at-warning)]/8 p-4">
          <Key className="mt-0.5 h-5 w-5 shrink-0 text-[var(--at-warning)]" />
          <div className="flex-1">
            <p className="text-sm font-medium">Add an AI provider key for best results</p>
            <p className="text-xs text-[var(--muted-foreground)]">
              Parsing an upload and the wizard both use AI. Add a key in settings so import can
              structure your resume.
            </p>
          </div>
          <Button asChild size="sm" variant="outline">
            <Link href="/settings">Open settings</Link>
          </Button>
        </Card>
      )}

      {aiUnhealthy && !aiUnconfigured && (
        <Card className="flex items-start gap-3 border-[var(--destructive)]/40 bg-[var(--destructive)]/8 p-4">
          <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-[var(--destructive)]" />
          <div className="flex-1">
            <p className="text-sm font-medium">Your AI provider key isn&apos;t responding</p>
            <p className="text-xs text-[var(--muted-foreground)]">
              A key is set but the last check failed (it may be invalid, use the wrong model, or be
              rate-limited). Import can read your file but won&apos;t be able to structure it until
              the key works.
            </p>
          </div>
          <Button asChild size="sm" variant="outline">
            <Link href="/settings">Check key</Link>
          </Button>
        </Card>
      )}

      {/* Upload zone - swapped for a premium, honest progress experience while
          the file uploads and the AI structures it (Loading Experience audit). */}
      {phase === 'uploading' ? (
        <Card className="space-y-5 p-6">
          <div className="flex items-center gap-2 text-sm font-medium text-[var(--foreground)]">
            <Wand className="h-4 w-4 text-[var(--at-ai)]" /> Turning your resume into an editable
            draft
          </div>
          {usingStream ? (
            <AiProgress
              stages={PARSE_STREAM_STAGES}
              activeKey={streamStage.active}
              doneKeys={streamStage.done}
              messages={PARSE_MESSAGES}
              estimate={ESTIMATE_PARSE}
              preview={<ResumeSkeletonPreview />}
            />
          ) : (
            <AiProgress
              stages={PARSE_STAGES}
              active
              messages={PARSE_MESSAGES}
              estimate={ESTIMATE_PARSE}
              preview={<ResumeSkeletonPreview />}
            />
          )}
        </Card>
      ) : (
        <div
          role="button"
          tabIndex={0}
          aria-label="Upload a resume file"
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`flex cursor-pointer flex-col items-center justify-center gap-3 rounded-[var(--radius-at-xl)] border-2 border-dashed p-12 text-center transition-colors ${
            dragging
              ? 'border-[var(--primary)] bg-[var(--at-ai-surface)]'
              : 'border-[var(--border)] hover:border-[var(--primary)]'
          }`}
        >
          <UploadCloud className="h-8 w-8 text-[var(--muted-foreground)]" />
          <p className="text-sm font-medium">Drop your resume here, or click to browse</p>
          <p className="text-xs text-[var(--muted-foreground)]">PDF, DOC, or DOCX - up to 4MB</p>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleFile(file);
              e.target.value = '';
            }}
          />
        </div>
      )}

      {phase === 'error' && error && (
        <Card className="flex items-start gap-3 border-[var(--destructive)]/40 p-4">
          <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-[var(--destructive)]" />
          <div className="flex-1">
            <p className="text-sm font-medium">Could not import that file</p>
            <p className="text-sm text-[var(--muted-foreground)]">{error}</p>
          </div>
          <Button variant="outline" size="sm" onClick={() => inputRef.current?.click()}>
            Try again
          </Button>
        </Card>
      )}

      <div className="flex items-center gap-3 text-sm text-[var(--muted-foreground)]">
        <span className="h-px flex-1 bg-[var(--border)]" /> or{' '}
        <span className="h-px flex-1 bg-[var(--border)]" />
      </div>

      <Card className="flex items-center gap-4 p-5">
        <Wand className="h-6 w-6 text-[var(--at-ai)]" />
        <div className="flex-1">
          <p className="font-medium">Build with the wizard</p>
          <p className="text-sm text-[var(--muted-foreground)]">
            Answer a few questions and let AI draft it.
          </p>
        </div>
        {aiUnconfigured ? (
          <Button variant="outline" disabled title="Add an AI key first">
            Add an AI key
          </Button>
        ) : (
          <Button asChild variant="outline">
            <Link href="/wizard">Start wizard</Link>
          </Button>
        )}
      </Card>

      <Card className="flex items-center gap-4 p-5">
        <Wand className="h-6 w-6 text-[var(--at-ai)]" />
        <div className="flex-1">
          <p className="font-medium">Browse the template library</p>
          <p className="text-sm text-[var(--muted-foreground)]">
            Explore professionally designed, ATS-aware templates and pick your look first.
          </p>
        </div>
        <div className="flex gap-2">
          <Button asChild variant="outline">
            <Link href="/templates">Templates</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/samples">Samples</Link>
          </Button>
        </div>
      </Card>
    </div>
  );
}
