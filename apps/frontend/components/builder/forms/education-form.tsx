'use client';

import React from 'react';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { Button } from '@/components/atelier/button';
import { Textarea } from '@/components/atelier/input';
import { Education } from '@/components/dashboard/resume-component';
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

interface EducationFormProps {
  data: Education[];
  onChange: (data: Education[]) => void;
}

const labelCls = 'text-xs font-medium text-[var(--muted-foreground)]';

export const EducationForm: React.FC<EducationFormProps> = ({ data, onChange }) => {
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
        institution: '',
        degree: '',
        years: '',
        description: '',
      },
    ]);
  };

  const handleRemove = (id: number) => {
    onChange(data.filter((item) => item.id !== id));
  };

  const handleChange = (id: number, field: keyof Education, value: string) => {
    onChange(
      data.map((item) => {
        if (item.id === id) {
          return { ...item, [field]: value };
        }
        return item;
      })
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={handleAdd}>
          <Plus className="mr-2 h-4 w-4" /> {t('builder.forms.education.addSchool')}
        </Button>
      </div>

      {data.length === 0 ? (
        <div className="rounded-[var(--radius-at-lg)] border border-dashed border-[var(--border)] bg-[var(--card)] py-12 text-center">
          <p className="mb-4 text-sm text-[var(--muted-foreground)]">
            {t('builder.genericItemForm.noEntries', { label: t('resume.sections.education') })}
          </p>
          <Button variant="outline" size="sm" onClick={handleAdd}>
            <Plus className="mr-2 h-4 w-4" /> {t('builder.forms.education.addFirstSchool')}
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
                          {t('builder.forms.education.fields.institution')}
                        </Label>
                        <Input
                          value={item.institution || ''}
                          onChange={(e) => handleChange(item.id, 'institution', e.target.value)}
                          placeholder={t('builder.forms.education.placeholders.institution')}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label className={labelCls}>
                          {t('builder.forms.education.fields.degree')}
                        </Label>
                        <Input
                          value={item.degree || ''}
                          onChange={(e) => handleChange(item.id, 'degree', e.target.value)}
                          placeholder={t('builder.forms.education.placeholders.degree')}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label className={labelCls}>
                          {t('builder.genericItemForm.fields.years')}
                        </Label>
                        <Input
                          value={item.years || ''}
                          onChange={(e) => handleChange(item.id, 'years', e.target.value)}
                          placeholder={t('builder.forms.education.placeholders.years')}
                        />
                      </div>
                    </div>

                    <div className="space-y-2">
                      <Label className={labelCls}>
                        {t('builder.forms.education.fields.descriptionOptional')}
                      </Label>
                      <Textarea
                        value={item.description || ''}
                        onChange={(e) => handleChange(item.id, 'description', e.target.value)}
                        className="min-h-[60px] text-sm"
                        placeholder={t('builder.forms.education.placeholders.description')}
                      />
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
