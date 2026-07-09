'use client';

/**
 * ExportButton (Task 11 / Req 16).
 *
 * Wires resume + cover-letter PDF export to the EXISTING `/print/*` pipeline
 * (via `downloadResumePdf` / `downloadCoverLetterPdf`) with explicit progress
 * and error states. The engine is reused unchanged, so the generated PDF is
 * byte-for-byte the pre-revamp output — this component only adds the download
 * UX (loading spinner, success/error toast, blob save).
 */
import * as React from 'react';
import Download from 'lucide-react/dist/esm/icons/download';
import { Button, type ButtonProps } from '@/components/atelier/button';
import { useToast } from '@/components/atelier/toast';
import { downloadResumePdf, downloadCoverLetterPdf } from '@/lib/api/resume';
import type { TemplateSettings } from '@/lib/types/template-settings';

type ExportKind =
  | { kind: 'resume'; resumeId: string; settings?: TemplateSettings; filename?: string }
  | { kind: 'cover-letter'; resumeId: string; pageSize?: 'A4' | 'LETTER'; filename?: string };

type ExportButtonProps = ExportKind & {
  label?: string;
  variant?: ButtonProps['variant'];
  size?: ButtonProps['size'];
  className?: string;
};

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next tick so the download has started.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ExportButton(props: ExportButtonProps) {
  const { label = 'Export PDF', variant = 'outline', size = 'sm', className } = props;
  const { toast } = useToast();
  const [loading, setLoading] = React.useState(false);

  async function onExport() {
    setLoading(true);
    try {
      let blob: Blob;
      let filename: string;
      if (props.kind === 'resume') {
        blob = await downloadResumePdf(props.resumeId, props.settings);
        filename = props.filename ?? `resume-${props.resumeId}.pdf`;
      } else {
        blob = await downloadCoverLetterPdf(props.resumeId, props.pageSize ?? 'A4');
        filename = props.filename ?? `cover-letter-${props.resumeId}.pdf`;
      }
      saveBlob(blob, filename);
      toast({
        title: 'Export ready',
        description: 'Your PDF has been downloaded.',
        variant: 'success',
      });
    } catch {
      toast({
        title: 'Export failed',
        description: 'Could not generate the PDF. Please try again.',
        variant: 'error',
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <Button
      variant={variant}
      size={size}
      className={className}
      loading={loading}
      onClick={onExport}
    >
      <Download className="h-4 w-4" /> {label}
    </Button>
  );
}
