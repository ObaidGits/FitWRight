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
import { ProfileConflictError, type ProfileData } from '@/lib/api/professional-profile';

type EligibilityKey =
  | 'workAuthorization'
  | 'visaStatus'
  | 'noticePeriod'
  | 'salaryExpectation'
  | 'remotePreference'
  | 'availability';

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
          {FIELDS.map((field) => (
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
            </div>
          ))}
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
