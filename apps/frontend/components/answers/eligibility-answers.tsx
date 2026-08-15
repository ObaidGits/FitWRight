'use client';

/**
 * Eligibility answers - the 7 screening questions almost every job form asks,
 * edited right where they are used.
 *
 * These fields exist on the Profile record (``identity.workAuthorization`` etc.)
 * because the extension reads them from there, but they do not belong on the
 * Profile *page*: a resume never shows "notice period" or "salary expectation",
 * and per FitWright's own docs a wrong answer here silently auto-rejects an
 * application. That is job-application judgment, not career history, so it is
 * edited on Answers - the surface that already owns "what forms ask you" - and
 * Profile stays focused on what actually appears on a resume.
 *
 * Same version-CAS save as the Profile page: baseVersion travels with the
 * fetched draft, and a conflict (edited on the Profile page in another tab)
 * reloads rather than silently overwriting.
 */
import * as React from 'react';

import { Button } from '@/components/atelier/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/atelier/card';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { LoadingSkeleton } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { useProfile, useSaveProfile } from '@/features/profile/hooks';
import {
  ProfileConflictError,
  type ConditionalEligibilityRule,
  type ProfileData,
} from '@/lib/api/professional-profile';

type EligibilityKey =
  | 'workAuthorization'
  | 'visaStatus'
  | 'noticePeriod'
  | 'salaryExpectation'
  | 'remotePreference'
  | 'availability';

/** The four fields a wrong flat answer can auto-reject on for the wrong reason
 * - the ones country-conditional rules apply to (auto-apply-brain Phase 1). */
type ConditionalKey = 'workAuthorization' | 'visaStatus' | 'salaryExpectation' | 'relocation';

const CONDITIONAL_LABEL: Record<ConditionalKey, string> = {
  workAuthorization: 'work authorization',
  visaStatus: 'visa or sponsorship status',
  salaryExpectation: 'salary expectation',
  relocation: 'willingness to relocate',
};

/** Which of the FIELDS rows gets an inline conditional-rule toggle. */
const CONDITIONAL_FOR: Partial<Record<EligibilityKey, ConditionalKey>> = {
  workAuthorization: 'workAuthorization',
  visaStatus: 'visaStatus',
  salaryExpectation: 'salaryExpectation',
};

const YES_NO = ['Yes', 'No'];
const REMOTE_OPTIONS = ['Remote', 'Hybrid', 'On-site', 'No preference'];

const FIELDS: {
  key: EligibilityKey;
  label: string;
  placeholder?: string;
  options?: string[];
}[] = [
  { key: 'workAuthorization', label: 'Work authorization', placeholder: 'e.g. Indian citizen' },
  {
    key: 'visaStatus',
    label: 'Visa or sponsorship status',
    placeholder: 'e.g. Requires sponsorship',
  },
  { key: 'noticePeriod', label: 'Notice period', placeholder: 'e.g. 30 days' },
  { key: 'salaryExpectation', label: 'Salary expectation', placeholder: 'e.g. 12-15 LPA' },
  { key: 'remotePreference', label: 'Remote preference', options: REMOTE_OPTIONS },
  { key: 'availability', label: 'Earliest start date', placeholder: 'e.g. Immediately' },
];

export function EligibilityAnswers() {
  const profileQuery = useProfile();
  const save = useSaveProfile();
  const { toast } = useToast();

  const [draft, setDraft] = React.useState<ProfileData | null>(null);
  const [baseVersion, setBaseVersion] = React.useState(1);

  const serverVersion = profileQuery.data?.version;
  React.useEffect(() => {
    if (profileQuery.data && (draft === null || baseVersion !== serverVersion)) {
      setDraft(structuredClone(profileQuery.data.data));
      setBaseVersion(profileQuery.data.version);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverVersion, profileQuery.data]);

  if (profileQuery.isLoading || !draft) {
    return <LoadingSkeleton rows={2} />;
  }
  if (profileQuery.isError) return null;

  const id = draft.identity;
  const dirty =
    profileQuery.data && JSON.stringify(draft) !== JSON.stringify(profileQuery.data.data);

  function set(key: EligibilityKey, value: string) {
    setDraft((prev) => (prev ? { ...prev, identity: { ...prev.identity, [key]: value } } : prev));
  }

  function setRelocate(value: boolean | null) {
    setDraft((prev) =>
      prev ? { ...prev, identity: { ...prev.identity, relocation: value } } : prev
    );
  }

  function setConditionalRule(key: ConditionalKey, patch: Partial<ConditionalEligibilityRule>) {
    setDraft((prev) => {
      if (!prev) return prev;
      const existing = prev.identity.conditionalEligibility[key] ?? {
        enabled: false,
        default: '',
        same_country_value: '',
      };
      return {
        ...prev,
        identity: {
          ...prev.identity,
          conditionalEligibility: {
            ...prev.identity.conditionalEligibility,
            [key]: { ...existing, ...patch },
          },
        },
      };
    });
  }

  async function onSave() {
    if (!draft) return;
    try {
      await save.mutateAsync({ data: draft, baseVersion });
      toast({ title: 'Eligibility answers saved', variant: 'success' });
    } catch (err) {
      if (err instanceof ProfileConflictError) {
        toast({
          title: 'Profile changed elsewhere',
          description: 'Reloading the latest version. Re-apply your edits.',
          variant: 'error',
        });
        await profileQuery.refetch();
        if (err.current) {
          setDraft(structuredClone(err.current.data));
          setBaseVersion(err.current.version);
        }
        return;
      }
      toast({
        title: 'Could not save',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  return (
    <Card id="eligibility-answers">
      <CardHeader>
        <CardTitle>Eligibility answers</CardTitle>
        <CardDescription>
          The screening questions almost every application asks. A wrong answer here can silently
          auto-reject you, so this is stored - never guessed - and reused on every form.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          {FIELDS.map((field) => {
            const conditionalKey = CONDITIONAL_FOR[field.key];
            return (
              <div key={field.key} className="space-y-1.5">
                <Label htmlFor={`elig-${field.key}`}>{field.label}</Label>
                {field.options ? (
                  <select
                    id={`elig-${field.key}`}
                    value={id[field.key]}
                    onChange={(e) => set(field.key, e.target.value)}
                    className="w-full rounded-[var(--radius-at-md)] border border-[var(--input)] bg-[var(--background)] px-3 py-2 text-sm text-[var(--foreground)]"
                  >
                    <option value="">Select…</option>
                    {field.options.map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Input
                    id={`elig-${field.key}`}
                    value={id[field.key]}
                    placeholder={field.placeholder}
                    onChange={(e) => set(field.key, e.target.value)}
                  />
                )}
                {conditionalKey && (
                  <ConditionalRuleToggle
                    fieldKey={conditionalKey}
                    rule={id.conditionalEligibility[conditionalKey]}
                    onChange={(patch) => setConditionalRule(conditionalKey, patch)}
                  />
                )}
              </div>
            );
          })}
          <div className="space-y-1.5">
            <Label htmlFor="elig-relocate">Willing to relocate</Label>
            <select
              id="elig-relocate"
              value={id.relocation === null ? '' : id.relocation ? 'Yes' : 'No'}
              onChange={(e) => setRelocate(e.target.value === '' ? null : e.target.value === 'Yes')}
              className="w-full rounded-[var(--radius-at-md)] border border-[var(--input)] bg-[var(--background)] px-3 py-2 text-sm text-[var(--foreground)]"
            >
              <option value="">Select…</option>
              {YES_NO.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
            <ConditionalRuleToggle
              fieldKey="relocation"
              rule={id.conditionalEligibility.relocation}
              onChange={(patch) => setConditionalRule('relocation', patch)}
              options={YES_NO}
            />
          </div>
        </div>
        <div className="flex justify-end">
          <Button onClick={onSave} disabled={!dirty} loading={save.isPending}>
            {dirty ? 'Save answers' : 'Saved'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * "This answer depends on the job's country" - one checkbox, revealing the two
 * values a country-conditional rule needs. Deliberately not a rule builder:
 * exactly this shape, for exactly the four fields in CONDITIONAL_LABEL.
 */
function ConditionalRuleToggle({
  fieldKey,
  rule,
  onChange,
  options,
}: {
  fieldKey: ConditionalKey;
  rule: ConditionalEligibilityRule | undefined;
  onChange: (patch: Partial<ConditionalEligibilityRule>) => void;
  /** When set, the two values are dropdowns over these options rather than free text. */
  options?: string[];
}) {
  const enabled = rule?.enabled ?? false;
  const inputId = `elig-conditional-${fieldKey}`;

  function valueField(key: 'default' | 'same_country_value', label: string, value: string) {
    if (options) {
      return (
        <select
          aria-label={label}
          value={value}
          onChange={(e) => onChange({ [key]: e.target.value })}
          className="w-full rounded-[var(--radius-at-sm)] border border-[var(--input)] bg-[var(--background)] px-2 py-1.5 text-xs text-[var(--foreground)]"
        >
          <option value="">Select…</option>
          {options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      );
    }
    return (
      <Input
        aria-label={label}
        value={value}
        onChange={(e) => onChange({ [key]: e.target.value })}
        className="text-xs"
      />
    );
  }

  return (
    <div className="space-y-1.5 rounded-[var(--radius-at-sm)] border border-dashed border-[var(--border)] p-2">
      <label className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]">
        <input
          id={inputId}
          type="checkbox"
          checked={enabled}
          onChange={(e) => onChange({ enabled: e.target.checked })}
          className="rounded-sm border-[var(--input)] accent-[var(--primary)]"
        />
        Answer depends on the job&apos;s country
      </label>
      {enabled && (
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor={`${inputId}-same`} className="text-[11px]">
              If the job is in your own country
            </Label>
            {valueField(
              'same_country_value',
              `Same-country ${CONDITIONAL_LABEL[fieldKey]}`,
              rule?.same_country_value ?? ''
            )}
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${inputId}-default`} className="text-[11px]">
              Otherwise
            </Label>
            {valueField('default', `Default ${CONDITIONAL_LABEL[fieldKey]}`, rule?.default ?? '')}
          </div>
        </div>
      )}
    </div>
  );
}
