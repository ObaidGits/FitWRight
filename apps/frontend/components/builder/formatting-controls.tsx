'use client';

import React, { useState } from 'react';
import { Button } from '@/components/atelier/button';
import { Switch } from '@/components/atelier/misc';
import { ChevronDown, ChevronUp, RotateCcw } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  type TemplateSettings,
  type TemplateType,
  type PageSize,
  type SpacingLevel,
  type HeaderFontFamily,
  type BodyFontFamily,
  type AccentColor,
  DEFAULT_TEMPLATE_SETTINGS,
  applyTemplatePreset,
  SECTION_SPACING_MAP,
  ITEM_SPACING_MAP,
  LINE_HEIGHT_MAP,
  FONT_SIZE_MAP,
  HEADER_SCALE_MAP,
  COMPACT_MULTIPLIER,
  COMPACT_LINE_HEIGHT_MULTIPLIER,
  TEMPLATE_OPTIONS,
  PAGE_SIZE_INFO,
  ACCENT_COLOR_MAP,
} from '@/lib/types/template-settings';
import { TemplateThumbnail } from './template-selector';
import { useTranslations } from '@/lib/i18n';

interface FormattingControlsProps {
  settings: TemplateSettings;
  onChange: (settings: TemplateSettings) => void;
}

const headingCls =
  'mb-3 text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]';

const chipBase =
  'flex items-center gap-2 rounded-[var(--radius-at-md)] border px-3 py-2 text-xs transition-colors';
const chipActive = 'border-[var(--primary)] bg-[var(--accent)] text-[var(--primary)]';
const chipIdle =
  'border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] hover:bg-[var(--accent)]';

/**
 * Formatting Controls Panel
 *
 * Provides user controls for adjusting resume layout: template, page size,
 * margins, spacing, fonts and options. Atelier design tokens throughout.
 */
export const FormattingControls: React.FC<FormattingControlsProps> = ({ settings, onChange }) => {
  const { t } = useTranslations();
  const [isExpanded, setIsExpanded] = useState(true);
  const compactMultiplier = settings.compactMode ? COMPACT_MULTIPLIER : 1;
  const sectionGapRem =
    parseFloat(SECTION_SPACING_MAP[settings.spacing.section]) * compactMultiplier;
  const itemGapRem = parseFloat(ITEM_SPACING_MAP[settings.spacing.item]) * compactMultiplier;
  const lineHeightValue = settings.compactMode
    ? LINE_HEIGHT_MAP[settings.spacing.lineHeight] * COMPACT_LINE_HEIGHT_MULTIPLIER
    : LINE_HEIGHT_MAP[settings.spacing.lineHeight];

  const formatRem = (value: number) =>
    `${value.toFixed(2).replace(/\.00$/, '').replace(/0$/, '')}rem`;

  const handleTemplateChange = (template: TemplateType) => {
    onChange(applyTemplatePreset(settings, template));
  };

  const handlePageSizeChange = (pageSize: PageSize) => {
    onChange({ ...settings, pageSize });
  };

  const handleMarginChange = (key: keyof TemplateSettings['margins'], value: number) => {
    onChange({ ...settings, margins: { ...settings.margins, [key]: value } });
  };

  const handleSpacingChange = (key: keyof TemplateSettings['spacing'], value: SpacingLevel) => {
    onChange({ ...settings, spacing: { ...settings.spacing, [key]: value } });
  };

  const handleFontChange = (key: keyof TemplateSettings['fontSize'], value: SpacingLevel) => {
    onChange({ ...settings, fontSize: { ...settings.fontSize, [key]: value } });
  };

  const handleHeaderFontChange = (headerFont: HeaderFontFamily) => {
    onChange({ ...settings, fontSize: { ...settings.fontSize, headerFont } });
  };

  const handleBodyFontChange = (bodyFont: BodyFontFamily) => {
    onChange({ ...settings, fontSize: { ...settings.fontSize, bodyFont } });
  };

  const handleCompactModeToggle = () => {
    onChange({ ...settings, compactMode: !settings.compactMode });
  };

  const handleShowContactIconsToggle = () => {
    onChange({ ...settings, showContactIcons: !settings.showContactIcons });
  };

  const handleAccentColorChange = (accentColor: AccentColor) => {
    onChange({ ...settings, accentColor });
  };

  const handleReset = () => {
    onChange(DEFAULT_TEMPLATE_SETTINGS);
  };

  const templateLabels = React.useMemo(
    () => ({
      'swiss-single': {
        name: t('builder.formatting.templates.swissSingle.name'),
        description: t('builder.formatting.templates.swissSingle.description'),
      },
      'swiss-two-column': {
        name: t('builder.formatting.templates.swissTwoColumn.name'),
        description: t('builder.formatting.templates.swissTwoColumn.description'),
      },
      modern: {
        name: t('builder.formatting.templates.modern.name'),
        description: t('builder.formatting.templates.modern.description'),
      },
      'modern-two-column': {
        name: t('builder.formatting.templates.modernTwoColumn.name'),
        description: t('builder.formatting.templates.modernTwoColumn.description'),
      },
      latex: {
        name: t('builder.formatting.templates.latex.name'),
        description: t('builder.formatting.templates.latex.description'),
      },
      clean: {
        name: t('builder.formatting.templates.clean.name'),
        description: t('builder.formatting.templates.clean.description'),
      },
      vivid: {
        name: t('builder.formatting.templates.vivid.name'),
        description: t('builder.formatting.templates.vivid.description'),
      },
    }),
    [t]
  );

  const getFontLabel = (font: HeaderFontFamily | BodyFontFamily) => {
    if (font === 'sans-serif') return t('builder.formatting.fontNames.sans');
    if (font === 'serif') return t('builder.formatting.fontNames.serif');
    return t('builder.formatting.fontNames.mono');
  };

  return (
    <div className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] shadow-[var(--shadow-at-e1)]">
      {/* Header - Always Visible */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex w-full items-center justify-between rounded-t-[var(--radius-at-lg)] p-3 transition-colors hover:bg-[var(--accent)]"
      >
        <div className="flex items-center gap-2">
          <div className="h-2 w-2 rounded-full bg-[var(--primary)]"></div>
          <span className="text-sm font-semibold text-[var(--foreground)]">
            {t('builder.formatting.panelTitle')}
          </span>
        </div>
        {isExpanded ? (
          <ChevronUp className="h-4 w-4 text-[var(--muted-foreground)]" />
        ) : (
          <ChevronDown className="h-4 w-4 text-[var(--muted-foreground)]" />
        )}
      </button>

      {/* Expandable Content */}
      {isExpanded && (
        <div className="space-y-6 border-t border-[var(--border)] p-4">
          {/* Template Selection */}
          <div>
            <h4 className={headingCls}>{t('builder.formatting.template')}</h4>
            <div className="flex flex-wrap gap-3">
              {TEMPLATE_OPTIONS.map((template) => (
                <button
                  key={template.id}
                  onClick={() => handleTemplateChange(template.id)}
                  className={cn(
                    'group flex flex-col items-center rounded-[var(--radius-at-md)] border p-2 transition-colors',
                    settings.template === template.id
                      ? 'border-[var(--primary)] bg-[var(--accent)] ring-1 ring-[var(--primary)]'
                      : 'border-[var(--border)] bg-[var(--card)] hover:bg-[var(--accent)]'
                  )}
                  title={templateLabels[template.id].description}
                >
                  <div className="mb-1.5 flex h-16 w-12 items-center justify-center">
                    <TemplateThumbnail
                      type={template.id}
                      isActive={settings.template === template.id}
                    />
                  </div>
                  <span
                    className={cn(
                      'text-[9px] font-semibold uppercase tracking-wide',
                      settings.template === template.id
                        ? 'text-[var(--primary)]'
                        : 'text-[var(--muted-foreground)]'
                    )}
                  >
                    {templateLabels[template.id].name}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* Accent Color Selection - Visible for Modern templates */}
          {(settings.template === 'modern' ||
            settings.template === 'modern-two-column' ||
            settings.template === 'vivid') && (
            <div>
              <h4 className={headingCls}>{t('builder.formatting.accentColor')}</h4>
              <div className="flex gap-2">
                {(Object.keys(ACCENT_COLOR_MAP) as AccentColor[]).map((color) => (
                  <button
                    key={color}
                    onClick={() => handleAccentColorChange(color)}
                    className={cn(chipBase, settings.accentColor === color ? chipActive : chipIdle)}
                    title={t(`builder.formatting.accentColors.${color}`)}
                  >
                    <span
                      className="h-4 w-4 rounded-full border border-[var(--border)]"
                      style={{ backgroundColor: ACCENT_COLOR_MAP[color].primary }}
                    />
                    <span>{t(`builder.formatting.accentColors.${color}`)}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Page Size Selection */}
          <div>
            <h4 className={headingCls}>{t('builder.formatting.pageSize')}</h4>
            <div className="flex gap-2">
              {(Object.keys(PAGE_SIZE_INFO) as PageSize[]).map((size) => (
                <button
                  key={size}
                  onClick={() => handlePageSizeChange(size)}
                  className={cn(
                    'flex-1 rounded-[var(--radius-at-md)] border px-3 py-2 text-xs transition-colors',
                    settings.pageSize === size ? chipActive : chipIdle
                  )}
                  title={PAGE_SIZE_INFO[size].dimensions}
                >
                  <div className="font-semibold">
                    {size === 'A4' ? 'A4' : t('builder.pageSize.usLetter')}
                  </div>
                  <div className="text-[9px] opacity-70">{PAGE_SIZE_INFO[size].dimensions}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Margins Section */}
          <div>
            <h4 className={headingCls}>{t('builder.formatting.margins')}</h4>
            <div className="grid grid-cols-2 gap-4">
              <MarginSlider
                label={t('builder.formatting.margin.top')}
                value={settings.margins.top}
                onChange={(v) => handleMarginChange('top', v)}
              />
              <MarginSlider
                label={t('builder.formatting.margin.bottom')}
                value={settings.margins.bottom}
                onChange={(v) => handleMarginChange('bottom', v)}
              />
              <MarginSlider
                label={t('builder.formatting.margin.left')}
                value={settings.margins.left}
                onChange={(v) => handleMarginChange('left', v)}
              />
              <MarginSlider
                label={t('builder.formatting.margin.right')}
                value={settings.margins.right}
                onChange={(v) => handleMarginChange('right', v)}
              />
            </div>
          </div>

          {/* Spacing Section */}
          <div>
            <h4 className={headingCls}>{t('builder.formatting.spacing')}</h4>
            <div className="space-y-3">
              <SpacingSelector
                label={t('builder.formatting.spacingSection')}
                value={settings.spacing.section}
                onChange={(v) => handleSpacingChange('section', v)}
              />
              <SpacingSelector
                label={t('builder.formatting.spacingItems')}
                value={settings.spacing.item}
                onChange={(v) => handleSpacingChange('item', v)}
              />
              <SpacingSelector
                label={t('builder.formatting.spacingLines')}
                value={settings.spacing.lineHeight}
                onChange={(v) => handleSpacingChange('lineHeight', v)}
              />
            </div>
          </div>

          {/* Font Size Section */}
          <div>
            <h4 className={headingCls}>{t('builder.formatting.fontSize')}</h4>
            <div className="space-y-3">
              <SpacingSelector
                label={t('builder.formatting.baseFontSize')}
                value={settings.fontSize.base}
                onChange={(v) => handleFontChange('base', v)}
              />
              <SpacingSelector
                label={t('builder.formatting.headerScale')}
                value={settings.fontSize.headerScale}
                onChange={(v) => handleFontChange('headerScale', v)}
              />
              {/* Header Font Family */}
              <div className="flex items-center gap-2">
                <span className="w-16 text-xs text-[var(--muted-foreground)]">
                  {t('builder.formatting.headerFontFamily')}:
                </span>
                <div className="flex gap-1">
                  {(['serif', 'sans-serif', 'mono'] as HeaderFontFamily[]).map((font) => (
                    <button
                      key={font}
                      onClick={() => handleHeaderFontChange(font)}
                      className={cn(
                        'rounded-[var(--radius-at-md)] border px-2 py-1 text-xs transition-colors',
                        settings.fontSize.headerFont === font ? chipActive : chipIdle
                      )}
                      style={{
                        fontFamily:
                          font === 'serif'
                            ? 'Georgia, serif'
                            : font === 'mono'
                              ? 'monospace'
                              : 'system-ui, sans-serif',
                      }}
                    >
                      {getFontLabel(font)}
                    </button>
                  ))}
                </div>
              </div>
              {/* Body Font Family */}
              <div className="flex items-center gap-2">
                <span className="w-16 text-xs text-[var(--muted-foreground)]">
                  {t('builder.formatting.bodyFontFamily')}:
                </span>
                <div className="flex gap-1">
                  {(['serif', 'sans-serif', 'mono'] as BodyFontFamily[]).map((font) => (
                    <button
                      key={font}
                      onClick={() => handleBodyFontChange(font)}
                      className={cn(
                        'rounded-[var(--radius-at-md)] border px-2 py-1 text-xs transition-colors',
                        settings.fontSize.bodyFont === font ? chipActive : chipIdle
                      )}
                      style={{
                        fontFamily:
                          font === 'serif'
                            ? 'Georgia, serif'
                            : font === 'mono'
                              ? 'monospace'
                              : 'system-ui, sans-serif',
                      }}
                    >
                      {getFontLabel(font)}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Options Section */}
          <div>
            <h4 className={headingCls}>{t('builder.formatting.options')}</h4>
            <div className="space-y-3">
              {/* Compact Mode Toggle */}
              <label className="flex cursor-pointer items-center gap-3">
                <Switch
                  checked={settings.compactMode}
                  onCheckedChange={handleCompactModeToggle}
                  aria-label={t('builder.formatting.compactMode')}
                />
                <span className="text-xs text-[var(--foreground)]">
                  {t('builder.formatting.compactMode')}
                </span>
              </label>

              {/* Show Contact Icons Toggle */}
              <label className="flex cursor-pointer items-center gap-3">
                <Switch
                  checked={settings.showContactIcons}
                  onCheckedChange={handleShowContactIconsToggle}
                  aria-label={t('builder.formatting.contactIcons')}
                />
                <span className="text-xs text-[var(--foreground)]">
                  {t('builder.formatting.contactIcons')}
                </span>
              </label>
            </div>
          </div>

          {/* Reset + effective output */}
          <div className="space-y-3 border-t border-[var(--border)] pt-2">
            <div>
              <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                {t('builder.formatting.effectiveOutput')}
              </h4>
              <div className="space-y-1 text-[10px] text-[var(--muted-foreground)]">
                <div title={t('builder.formatting.margins')}>
                  {t('builder.formatting.effectiveMargins', {
                    top: settings.margins.top,
                    bottom: settings.margins.bottom,
                    left: settings.margins.left,
                    right: settings.margins.right,
                  })}
                </div>
                <div>
                  {t('builder.formatting.effectiveSectionGap')}: {formatRem(sectionGapRem)}
                </div>
                <div>
                  {t('builder.formatting.effectiveItemGap')}: {formatRem(itemGapRem)}
                </div>
                <div>
                  {t('builder.formatting.effectiveLineHeight')}: {lineHeightValue.toFixed(2)}
                </div>
                <div>
                  {t('builder.formatting.effectiveBaseFont')}:{' '}
                  {FONT_SIZE_MAP[settings.fontSize.base]}
                </div>
                <div>
                  {t('builder.formatting.effectiveHeaderScale')}:{' '}
                  {HEADER_SCALE_MAP[settings.fontSize.headerScale]}x
                </div>
                <div>
                  {t('builder.formatting.effectiveHeaderFont')}:{' '}
                  {getFontLabel(settings.fontSize.headerFont)}
                </div>
                <div>
                  {t('builder.formatting.effectiveBodyFont')}:{' '}
                  {getFontLabel(settings.fontSize.bodyFont)}
                </div>
              </div>
              {settings.compactMode && (
                <div className="mt-2 text-[10px] text-[var(--muted-foreground)]">
                  {t('builder.formatting.compactHint')}
                </div>
              )}
            </div>
            <Button variant="outline" size="sm" onClick={handleReset} className="w-full">
              <RotateCcw className="h-3 w-3" />
              {t('builder.formatting.resetDefaults')}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
};

interface MarginSliderProps {
  label: string;
  value: number;
  onChange: (value: number) => void;
}

const MarginSlider: React.FC<MarginSliderProps> = ({ label, value, onChange }) => {
  return (
    <div className="flex items-center gap-2">
      <span className="w-12 text-xs text-[var(--muted-foreground)]">{label}:</span>
      <input
        type="range"
        min={5}
        max={25}
        value={value}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
        className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-[var(--secondary)]
                   [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3
                   [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full
                   [&::-webkit-slider-thumb]:border-none [&::-webkit-slider-thumb]:bg-[var(--primary)]
                   [&::-webkit-slider-thumb]:cursor-pointer
                   [&::-moz-range-thumb]:h-3 [&::-moz-range-thumb]:w-3
                   [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-none
                   [&::-moz-range-thumb]:bg-[var(--primary)] [&::-moz-range-thumb]:cursor-pointer"
      />
      <span className="w-6 text-right text-xs text-[var(--muted-foreground)]">{value}</span>
    </div>
  );
};

interface SpacingSelectorProps {
  label: string;
  value: SpacingLevel;
  onChange: (value: SpacingLevel) => void;
}

const SpacingSelector: React.FC<SpacingSelectorProps> = ({ label, value, onChange }) => {
  const levels: SpacingLevel[] = [1, 2, 3, 4, 5];

  return (
    <div className="flex items-center gap-2">
      <span className="w-16 text-xs text-[var(--muted-foreground)]">{label}:</span>
      <div className="flex gap-1">
        {levels.map((level) => (
          <button
            key={level}
            onClick={() => onChange(level)}
            className={cn(
              'h-6 w-6 rounded-[var(--radius-at-sm)] border text-xs transition-colors',
              value === level ? chipActive : chipIdle
            )}
          >
            {level}
          </button>
        ))}
      </div>
    </div>
  );
};

export default FormattingControls;
