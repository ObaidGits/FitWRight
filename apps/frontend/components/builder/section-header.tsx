'use client';

import React, { useState } from 'react';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { ConfirmDialog } from '@/components/atelier/confirm-dialog';
import { ChevronUp, ChevronDown, Trash2, Eye, EyeOff, Pencil, Check, X } from 'lucide-react';
import type { SectionMeta } from '@/components/dashboard/resume-component';
import { useTranslations } from '@/lib/i18n';

interface SectionHeaderProps {
  section: SectionMeta;
  onRename: (newName: string) => void;
  onDelete: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onToggleVisibility: () => void;
  isFirst: boolean;
  isLast: boolean;
  canDelete: boolean;
  children?: React.ReactNode;
}

/**
 * SectionHeader Component
 *
 * Provides controls for section management:
 * - Editable display name
 * - Move up/down buttons for reordering
 * - Delete button with confirmation
 * - Visibility toggle
 */
export const SectionHeader: React.FC<SectionHeaderProps> = ({
  section,
  onRename,
  onDelete,
  onMoveUp,
  onMoveDown,
  onToggleVisibility,
  isFirst,
  isLast,
  canDelete,
  children,
}) => {
  const { t } = useTranslations();
  const [isEditing, setIsEditing] = useState(false);
  const [editedName, setEditedName] = useState(section.displayName);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const handleStartEdit = () => {
    setEditedName(section.displayName);
    setIsEditing(true);
  };

  const handleSaveEdit = () => {
    if (editedName.trim()) {
      onRename(editedName.trim());
    }
    setIsEditing(false);
  };

  const handleCancelEdit = () => {
    setEditedName(section.displayName);
    setIsEditing(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSaveEdit();
    } else if (e.key === 'Escape') {
      handleCancelEdit();
    }
  };

  const handleDeleteClick = () => {
    if (section.isDefault) {
      // For default sections, just toggle visibility
      onToggleVisibility();
    } else {
      // For custom sections, show confirmation
      setShowDeleteConfirm(true);
    }
  };

  const isPersonalInfo = section.id === 'personalInfo';
  const isHidden = !section.isVisible;

  return (
    <div
      className={`space-y-0 rounded-[var(--radius-at-lg)] border bg-[var(--card)] p-6 shadow-[var(--shadow-at-e1)] ${
        isHidden
          ? 'border-dashed border-[var(--muted-foreground)] opacity-60'
          : 'border-[var(--border)]'
      }`}
    >
      {/* Section Header */}
      <div className="mb-4 flex items-center justify-between border-b border-[var(--border)] pb-2">
        {/* Section Name (editable) */}
        <div className="flex items-center gap-2">
          {isEditing ? (
            <div className="flex items-center gap-1">
              <Input
                value={editedName}
                onChange={(e) => setEditedName(e.target.value)}
                onKeyDown={handleKeyDown}
                className="h-8 w-48 text-lg font-semibold"
                autoFocus
              />
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-[var(--at-success)] hover:bg-[var(--at-success)]/10"
                onClick={handleSaveEdit}
                aria-label={t('common.save')}
                title={t('common.save')}
              >
                <Check className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                onClick={handleCancelEdit}
                aria-label={t('common.cancel')}
                title={t('common.cancel')}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          ) : (
            <>
              <h3 className="text-xl font-semibold text-[var(--foreground)]">
                {section.displayName}
              </h3>
              {!isPersonalInfo && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-[var(--muted-foreground)] hover:text-[var(--foreground)] before:-inset-[10px]"
                  onClick={handleStartEdit}
                  aria-label={t('builder.sectionHeader.renameSection')}
                  title={t('builder.sectionHeader.renameSection')}
                >
                  <Pencil className="h-3 w-3" />
                </Button>
              )}
              {!section.isDefault && (
                <span className="rounded-[var(--radius-at-sm)] bg-[var(--secondary)] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">
                  {t('builder.sectionHeader.customTag')}
                </span>
              )}
              {isHidden && (
                <span className="rounded-[var(--radius-at-sm)] bg-[var(--at-warning)]/12 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[var(--at-warning)]">
                  {t('builder.sectionHeader.hiddenFromPdfTag')}
                </span>
              )}
            </>
          )}
        </div>

        {/* Section Controls */}
        <div className="flex items-center gap-1">
          {!isPersonalInfo && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-[var(--muted-foreground)]"
              onClick={onToggleVisibility}
              aria-label={
                section.isVisible
                  ? t('builder.sectionHeader.hideSection')
                  : t('builder.sectionHeader.showSection')
              }
              aria-pressed={!section.isVisible}
              title={
                section.isVisible
                  ? t('builder.sectionHeader.hideSection')
                  : t('builder.sectionHeader.showSection')
              }
            >
              {section.isVisible ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
            </Button>
          )}

          {!isPersonalInfo && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-[var(--muted-foreground)] hover:text-[var(--foreground)] disabled:opacity-30"
              onClick={onMoveUp}
              disabled={isFirst}
              aria-label={t('builder.sectionHeader.moveUp')}
              title={t('builder.sectionHeader.moveUp')}
            >
              <ChevronUp className="h-4 w-4" />
            </Button>
          )}

          {!isPersonalInfo && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-[var(--muted-foreground)] hover:text-[var(--foreground)] disabled:opacity-30"
              onClick={onMoveDown}
              disabled={isLast}
              aria-label={t('builder.sectionHeader.moveDown')}
              title={t('builder.sectionHeader.moveDown')}
            >
              <ChevronDown className="h-4 w-4" />
            </Button>
          )}

          {canDelete && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-[var(--destructive)] hover:bg-[var(--destructive)]/10"
              onClick={handleDeleteClick}
              aria-label={
                section.isDefault
                  ? section.isVisible
                    ? t('builder.sectionHeader.hideSection')
                    : t('builder.sectionHeader.showSection')
                  : t('builder.sectionHeader.deleteSection')
              }
              title={
                section.isDefault
                  ? section.isVisible
                    ? t('builder.sectionHeader.hideSection')
                    : t('builder.sectionHeader.showSection')
                  : t('builder.sectionHeader.deleteSection')
              }
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {/* Section Content */}
      {children}

      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        open={showDeleteConfirm}
        onOpenChange={setShowDeleteConfirm}
        title={t('builder.sectionHeader.deleteTitle')}
        description={t('builder.sectionHeader.deleteDescription', { name: section.displayName })}
        confirmLabel={t('common.delete')}
        cancelLabel={t('common.cancel')}
        variant="danger"
        onConfirm={onDelete}
      />
    </div>
  );
};
