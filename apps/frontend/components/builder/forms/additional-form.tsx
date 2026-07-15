'use client';

import React from 'react';
import { Label } from '@/components/atelier/label';
import { Textarea } from '@/components/atelier/input';
import { AdditionalInfo } from '@/components/dashboard/resume-component';
import { useTranslations } from '@/lib/i18n';

interface AdditionalFormProps {
  data: AdditionalInfo;
  onChange: (data: AdditionalInfo) => void;
}

const labelCls = 'text-xs font-medium text-[var(--muted-foreground)]';

export const AdditionalForm: React.FC<AdditionalFormProps> = ({ data, onChange }) => {
  const { t } = useTranslations();

  // Helper to handle array conversions (text -> string[])
  const handleArrayChange = (field: keyof AdditionalInfo, value: string) => {
    // Split by newlines only. Blank/whitespace lines are preserved while editing
    // so pressing Enter creates a new line (issue #763); consumers filter empty
    // entries at render time, and the backend drops them on save.
    const items = value.split('\n');
    onChange({
      ...data,
      [field]: items,
    });
  };

  const formatArray = (arr?: string[]) => {
    return arr?.join('\n') || '';
  };

  // Explicitly allow Enter key to create newlines (prevent form submission interference)
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter') {
      // Allow default behavior (newline insertion)
      e.stopPropagation();
    }
  };

  return (
    <div className="space-y-6">
      <p className="text-xs text-[var(--muted-foreground)]">
        {t('builder.additionalForm.instructions')}
      </p>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="technicalSkills" className={labelCls}>
            {t('resume.additional.technicalSkills')}
          </Label>
          <Textarea
            id="technicalSkills"
            value={formatArray(data.technicalSkills)}
            onChange={(e) => handleArrayChange('technicalSkills', e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('builder.additionalForm.placeholders.technicalSkills')}
            className="min-h-[120px]"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="languages" className={labelCls}>
            {t('resume.sections.languages')}
          </Label>
          <Textarea
            id="languages"
            value={formatArray(data.languages)}
            onChange={(e) => handleArrayChange('languages', e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('builder.additionalForm.placeholders.languages')}
            className="min-h-[120px]"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="certifications" className={labelCls}>
            {t('resume.sections.certifications')}
          </Label>
          <Textarea
            id="certifications"
            value={formatArray(data.certificationsTraining)}
            onChange={(e) => handleArrayChange('certificationsTraining', e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('builder.additionalForm.placeholders.certifications')}
            className="min-h-[120px]"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="awards" className={labelCls}>
            {t('resume.sections.awards')}
          </Label>
          <Textarea
            id="awards"
            value={formatArray(data.awards)}
            onChange={(e) => handleArrayChange('awards', e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('builder.additionalForm.placeholders.awards')}
            className="min-h-[120px]"
          />
        </div>
      </div>
    </div>
  );
};
