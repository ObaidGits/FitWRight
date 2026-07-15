'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Button } from '@/components/atelier/button';

// Lazy-load TipTap-based editor — keeps it out of the initial bundle.
// Loads only when an experience entry is actually being edited.
const RichTextEditor = dynamic(
  () => import('@/components/atelier/rich-text-editor').then((m) => m.RichTextEditor),
  {
    ssr: false,
    loading: () => (
      <div
        className="min-h-[100px] rounded-[var(--radius-at-md)] border border-[var(--border)]"
        aria-busy="true"
      />
    ),
  }
);
import { Experience } from '@/components/dashboard/resume-component';
import { Plus, Trash2 } from 'lucide-react';
import { useTranslations } from '@/lib/i18n';
import {
  DndContext,
  closestCenter,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { DraggableListItem } from '../draggable-list-item';

interface ExperienceFormProps {
  data: Experience[];
  onChange: (data: Experience[]) => void;
}

const labelCls = 'text-xs font-medium text-[var(--muted-foreground)]';

export const ExperienceForm: React.FC<ExperienceFormProps> = ({ data, onChange }) => {
  const { t } = useTranslations();

  // Configure drag-and-drop sensors
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  // Handler for drag end event
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;

    if (!over || active.id === over.id) return;

    const oldIndex = data.findIndex((item) => item.id === active.id);
    const newIndex = data.findIndex((item) => item.id === over.id);

    if (oldIndex === -1 || newIndex === -1) return;

    // Reorder the array using arrayMove from @dnd-kit
    const reordered = arrayMove(data, oldIndex, newIndex);
    onChange(reordered);
  };

  const handleAdd = () => {
    const newId = Math.max(...data.map((d) => d.id), 0) + 1;
    onChange([
      ...data,
      {
        id: newId,
        title: '',
        company: '',
        location: '',
        years: '',
        description: [''],
      },
    ]);
  };

  const handleRemove = (id: number) => {
    onChange(data.filter((item) => item.id !== id));
  };

  const handleChange = (id: number, field: keyof Experience, value: string | string[]) => {
    onChange(
      data.map((item) => {
        if (item.id === id) {
          return { ...item, [field]: value };
        }
        return item;
      })
    );
  };

  const handleDescriptionChange = (id: number, index: number, value: string) => {
    onChange(
      data.map((item) => {
        if (item.id === id) {
          const newDesc = [...(item.description || [])];
          newDesc[index] = value;
          return { ...item, description: newDesc };
        }
        return item;
      })
    );
  };

  const handleAddDescription = (id: number) => {
    onChange(
      data.map((item) => {
        if (item.id === id) {
          return { ...item, description: [...(item.description || []), ''] };
        }
        return item;
      })
    );
  };

  const handleRemoveDescription = (id: number, index: number) => {
    onChange(
      data.map((item) => {
        if (item.id === id) {
          const newDesc = [...(item.description || [])];
          newDesc.splice(index, 1);
          return { ...item, description: newDesc };
        }
        return item;
      })
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={handleAdd}>
          <Plus className="mr-2 h-4 w-4" /> {t('builder.forms.experience.addJob')}
        </Button>
      </div>

      {data.length === 0 ? (
        <div className="rounded-[var(--radius-at-lg)] border border-dashed border-[var(--border)] bg-[var(--card)] py-12 text-center">
          <p className="mb-4 text-sm text-[var(--muted-foreground)]">
            {t('builder.genericItemForm.noEntries', { label: t('resume.sections.experience') })}
          </p>
          <Button variant="outline" size="sm" onClick={handleAdd}>
            <Plus className="mr-2 h-4 w-4" /> {t('builder.forms.experience.addFirstJob')}
          </Button>
        </div>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext
            items={data.map((item) => item.id)}
            strategy={verticalListSortingStrategy}
          >
            <div className="space-y-8">
              {data.map((item) => (
                <DraggableListItem key={item.id} id={item.id}>
                  <div className="group relative rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-6">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="absolute right-2 top-2 text-[var(--destructive)] opacity-0 transition-opacity hover:bg-[var(--destructive)]/10 group-hover:opacity-100"
                      onClick={() => handleRemove(item.id)}
                      aria-label={t('a11y.removeItem')}
                      title={t('a11y.removeItem')}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>

                    <div className="mb-4 grid grid-cols-1 gap-4 pr-8 md:grid-cols-2">
                      <div className="space-y-2">
                        <Label className={labelCls}>
                          {t('builder.forms.experience.fields.jobTitle')}
                        </Label>
                        <Input
                          value={item.title || ''}
                          onChange={(e) => handleChange(item.id, 'title', e.target.value)}
                          placeholder={t('builder.forms.experience.placeholders.jobTitle')}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label className={labelCls}>
                          {t('builder.forms.experience.fields.company')}
                        </Label>
                        <Input
                          value={item.company || ''}
                          onChange={(e) => handleChange(item.id, 'company', e.target.value)}
                          placeholder={t('builder.forms.experience.placeholders.company')}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label className={labelCls}>
                          {t('builder.genericItemForm.fields.location')}
                        </Label>
                        <Input
                          value={item.location || ''}
                          onChange={(e) => handleChange(item.id, 'location', e.target.value)}
                          placeholder={t('builder.forms.experience.placeholders.location')}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label className={labelCls}>
                          {t('builder.genericItemForm.fields.years')}
                        </Label>
                        <Input
                          value={item.years || ''}
                          onChange={(e) => handleChange(item.id, 'years', e.target.value)}
                          placeholder={t('builder.forms.experience.placeholders.years')}
                        />
                      </div>
                    </div>

                    <div className="space-y-3">
                      <div className="flex items-center justify-between">
                        <Label className={labelCls}>
                          {t('builder.genericItemForm.fields.descriptionPoints')}
                        </Label>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleAddDescription(item.id)}
                          className="h-6 text-xs"
                        >
                          <Plus className="mr-1 h-3 w-3" />{' '}
                          {t('builder.genericItemForm.actions.addPoint')}
                        </Button>
                      </div>
                      {item.description?.map((desc, idx) => (
                        <div key={idx} className="flex gap-2">
                          <div className="flex-1">
                            <RichTextEditor
                              value={desc}
                              onChange={(html) => handleDescriptionChange(item.id, idx, html)}
                              placeholder={t('builder.forms.experience.placeholders.description')}
                              minHeight="60px"
                            />
                          </div>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleRemoveDescription(item.id, idx)}
                            className="h-[60px] w-8 self-end text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                            aria-label={t('a11y.removeDescription')}
                            title={t('a11y.removeDescription')}
                          >
                            <Trash2 className="h-3 w-3" />
                          </Button>
                        </div>
                      ))}
                    </div>
                  </div>
                </DraggableListItem>
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
    </div>
  );
};
