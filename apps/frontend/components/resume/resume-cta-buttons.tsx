'use client';

/**
 * Client CTAs for the Sample Library + Template detail pages.
 *
 * - <UseSampleButton> creates a brand-new resume from a sample's content (via
 *   `POST /resumes/from-data`), applying the sample's recommended template, then
 *   opens the editor.
 * - <UseTemplateButton> records the chosen template as preferred and sends the
 *   user into the wizard (which opens rendered in that template).
 */
import * as React from 'react';
import { useRouter } from 'next/navigation';

import { Button } from '@/components/atelier/button';
import { useToast } from '@/components/atelier/toast';
import { createResumeFromData } from '@/lib/api/resume';
import { getTemplateById, templateToSettings } from '@/lib/resume/template-catalog';
import { setPreferredTemplateId } from '@/lib/resume/preferred-template';
import type { ResumeSample } from '@/lib/resume/sample-catalog';

export function UseSampleButton({
  sample,
  className,
}: {
  sample: ResumeSample;
  className?: string;
}) {
  const router = useRouter();
  const { toast } = useToast();
  const [busy, setBusy] = React.useState(false);

  async function onUse() {
    setBusy(true);
    try {
      const template = getTemplateById(sample.recommendedTemplateId);
      const res = await createResumeFromData({
        processed_data: sample.data,
        title: `${sample.name} resume`,
        template_settings: template ? templateToSettings(template) : null,
        source: `sample:${sample.id}`,
      });
      toast({ title: 'Resume created from sample', variant: 'success' });
      router.push(`/resumes/${res.resume_id}`);
    } catch (e) {
      toast({
        title: e instanceof Error ? e.message : 'Could not create resume',
        variant: 'error',
      });
      setBusy(false);
    }
  }

  return (
    <Button onClick={onUse} loading={busy} className={className}>
      Use this sample
    </Button>
  );
}

export function UseTemplateButton({
  templateId,
  className,
}: {
  templateId: string;
  className?: string;
}) {
  const router = useRouter();
  function onUse() {
    setPreferredTemplateId(templateId);
    router.push('/wizard');
  }
  return (
    <Button onClick={onUse} className={className}>
      Use this template
    </Button>
  );
}
