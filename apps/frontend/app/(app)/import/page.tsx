'use client';

/** Import a resume (Task 7.2 / Req 8) — drag-drop upload + parse status + wizard entry. */
import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import UploadCloud from 'lucide-react/dist/esm/icons/cloud-upload';
import Wand from 'lucide-react/dist/esm/icons/wand-sparkles';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import TriangleAlert from 'lucide-react/dist/esm/icons/triangle-alert';

import { Button } from '@/components/atelier/button';
import { Card } from '@/components/atelier/card';
import { useToast } from '@/components/atelier/toast';
import { queryKeys } from '@/lib/query/client';
import { uploadResumeFile, validateResumeFile } from '@/features/resumes/upload';

type Phase = 'idle' | 'uploading' | 'error';

export default function ImportPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { toast } = useToast();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [phase, setPhase] = React.useState<Phase>('idle');
  const [error, setError] = React.useState<string | null>(null);
  const [dragging, setDragging] = React.useState(false);

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
      try {
        const res = await uploadResumeFile(file);
        qc.invalidateQueries({ queryKey: queryKeys.resumes });
        if (res.processing_status === 'failed') {
          setError(
            'We uploaded the file but could not parse it. It may be a scanned/image PDF. Try a file with selectable text.'
          );
          setPhase('error');
          return;
        }
        toast({ title: 'Resume uploaded', variant: 'success' });
        router.push(`/resumes/${res.resume_id}`);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Upload failed. Please try again.');
        setPhase('error');
      }
    },
    [qc, router, toast]
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

      {/* Upload zone */}
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload a resume file"
        onClick={() => phase !== 'uploading' && inputRef.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && phase !== 'uploading')
            inputRef.current?.click();
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
        {phase === 'uploading' ? (
          <>
            <Loader2 className="h-8 w-8 animate-spin text-[var(--primary)]" />
            <p className="text-sm font-medium">Uploading &amp; parsing…</p>
            <p className="text-xs text-[var(--muted-foreground)]">This can take a few seconds.</p>
          </>
        ) : (
          <>
            <UploadCloud className="h-8 w-8 text-[var(--muted-foreground)]" />
            <p className="text-sm font-medium">Drop your resume here, or click to browse</p>
            <p className="text-xs text-[var(--muted-foreground)]">PDF, DOC, or DOCX · up to 4MB</p>
          </>
        )}
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
        <Button asChild variant="outline">
          <Link href="/wizard">Start wizard</Link>
        </Button>
      </Card>
    </div>
  );
}
