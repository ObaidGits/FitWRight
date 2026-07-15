'use client';

import React from 'react';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { PersonalInfo } from '@/components/dashboard/resume-component';
import { useTranslations } from '@/lib/i18n';

interface PersonalInfoFormProps {
  data: PersonalInfo;
  onChange: (data: PersonalInfo) => void;
}

const labelCls = 'text-xs font-medium text-[var(--muted-foreground)]';

export const PersonalInfoForm: React.FC<PersonalInfoFormProps> = ({ data, onChange }) => {
  const { t } = useTranslations();

  const handleChange = (field: keyof PersonalInfo, value: string) => {
    onChange({
      ...data,
      [field]: value,
    });
  };

  return (
    <div className="space-y-4 rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-6 shadow-[var(--shadow-at-e1)]">
      <h3 className="mb-4 border-b border-[var(--border)] pb-2 text-lg font-semibold text-[var(--foreground)]">
        {t('builder.personalInfo')}
      </h3>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="name" className={labelCls}>
            {t('resume.personalInfo.name')}
          </Label>
          <Input
            id="name"
            value={data.name || ''}
            onChange={(e) => handleChange('name', e.target.value)}
            placeholder={t('builder.personalInfoForm.placeholders.name')}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="title" className={labelCls}>
            {t('resume.personalInfo.title')}
          </Label>
          <Input
            id="title"
            value={data.title || ''}
            onChange={(e) => handleChange('title', e.target.value)}
            placeholder={t('builder.personalInfoForm.placeholders.title')}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="email" className={labelCls}>
            {t('resume.personalInfo.email')}
          </Label>
          <Input
            id="email"
            type="email"
            value={data.email || ''}
            onChange={(e) => handleChange('email', e.target.value)}
            placeholder={t('builder.personalInfoForm.placeholders.email')}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="phone" className={labelCls}>
            {t('resume.personalInfo.phone')}
          </Label>
          <Input
            id="phone"
            type="tel"
            value={data.phone || ''}
            onChange={(e) => handleChange('phone', e.target.value)}
            placeholder={t('builder.personalInfoForm.placeholders.phone')}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="location" className={labelCls}>
            {t('resume.personalInfo.location')}
          </Label>
          <Input
            id="location"
            value={data.location || ''}
            onChange={(e) => handleChange('location', e.target.value)}
            placeholder={t('builder.personalInfoForm.placeholders.location')}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="website" className={labelCls}>
            {t('resume.personalInfo.website')}
          </Label>
          <Input
            id="website"
            value={data.website || ''}
            onChange={(e) => handleChange('website', e.target.value)}
            placeholder={t('builder.personalInfoForm.placeholders.website')}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="linkedin" className={labelCls}>
            {t('resume.personalInfo.linkedin')}
          </Label>
          <Input
            id="linkedin"
            value={data.linkedin || ''}
            onChange={(e) => handleChange('linkedin', e.target.value)}
            placeholder={t('builder.personalInfoForm.placeholders.linkedin')}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="github" className={labelCls}>
            {t('resume.personalInfo.github')}
          </Label>
          <Input
            id="github"
            value={data.github || ''}
            onChange={(e) => handleChange('github', e.target.value)}
            placeholder={t('builder.personalInfoForm.placeholders.github')}
          />
        </div>
      </div>
    </div>
  );
};
