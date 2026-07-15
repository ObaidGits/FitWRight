'use client';

/**
 * ProfileSettings (P3 §H / Requirements 13–14) — avatar + reusable profile.
 *
 * Avatar upload posts the raw file; the backend sniffs magic bytes, re-encodes
 * to WebP, strips EXIF, and returns the served/CDN URL (the client only shows a
 * preview + progress). Extended fields (headline / location / links) are
 * validated client- and server-side and reused to prefill resumes. Optimistic
 * cache update on save; explicit loading / error states.
 */
import * as React from 'react';
import Plus from 'lucide-react/dist/esm/icons/plus';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Card } from '@/components/atelier/card';
import { Button } from '@/components/atelier/button';
import { Input } from '@/components/atelier/input';
import { Label } from '@/components/atelier/label';
import { LoadingSkeleton, ErrorState } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import { AvatarUploader } from '@/components/profile/avatar-uploader';
import { useSession } from '@/lib/context/session';
import { getProfile, updateProfile, type Profile, type ProfileLink } from '@/lib/api/profile';
import { queryKeys } from '@/lib/query/client';

export function ProfileSettings() {
  const { toast } = useToast();
  const qc = useQueryClient();
  const { refresh: refreshSession } = useSession();

  // Keep the professional profile's live-resolved avatar + the top-bar badge in
  // sync after a photo change (both read the account master).
  const syncAvatarEverywhere = React.useCallback(async () => {
    await refreshSession();
    qc.invalidateQueries({ queryKey: queryKeys.professionalProfile });
  }, [refreshSession, qc]);

  const profileQuery = useQuery<Profile>({ queryKey: queryKeys.profile, queryFn: getProfile });

  const [headline, setHeadline] = React.useState('');
  const [location, setLocation] = React.useState('');
  const [links, setLinks] = React.useState<ProfileLink[]>([]);

  React.useEffect(() => {
    if (profileQuery.data) {
      setHeadline(profileQuery.data.headline ?? '');
      setLocation(profileQuery.data.location ?? '');
      setLinks(profileQuery.data.links ?? []);
    }
  }, [profileQuery.data]);

  const save = useMutation({
    mutationFn: () =>
      updateProfile({
        headline: headline.trim() || null,
        location: location.trim() || null,
        links: links.filter((l) => l.label.trim() && l.url.trim()),
      }),
    onSuccess: (updated) => {
      qc.setQueryData(queryKeys.profile, updated);
      toast({ title: 'Profile saved', variant: 'success' });
    },
    onError: (err) =>
      toast({ title: err instanceof Error ? err.message : 'Could not save', variant: 'error' }),
  });

  if (profileQuery.isLoading) return <LoadingSkeleton rows={3} />;
  if (profileQuery.isError)
    return (
      <ErrorState
        description="Could not load your profile."
        onRetry={() => profileQuery.refetch()}
      />
    );

  const avatarUrl = profileQuery.data?.avatar_url;

  return (
    <Card className="space-y-5 p-6">
      <div>
        <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Photo & details</h2>
        <p className="text-xs text-[var(--muted-foreground)]">Reused to prefill new resumes.</p>
      </div>

      {/* Avatar — shared canonical-photo uploader (identical to the resume builder). */}
      <AvatarUploader
        avatarUrl={avatarUrl}
        onUploaded={(result) => {
          qc.setQueryData<Profile>(queryKeys.profile, (old) =>
            old ? { ...old, avatar_url: result.avatar_url } : old
          );
          void syncAvatarEverywhere();
          toast({
            title: result.deduplicated ? 'Photo already up to date' : 'Photo updated',
            variant: 'success',
          });
        }}
        onRemoved={() => {
          qc.setQueryData<Profile>(queryKeys.profile, (old) =>
            old ? { ...old, avatar_url: null } : old
          );
          void syncAvatarEverywhere();
          toast({ title: 'Photo removed', variant: 'success' });
        }}
        onError={(m) => toast({ title: m, variant: 'error' })}
      />

      {/* Fields */}
      <div className="space-y-1.5">
        <Label htmlFor="headline">Headline</Label>
        <Input
          id="headline"
          value={headline}
          onChange={(e) => setHeadline(e.target.value)}
          maxLength={200}
          placeholder="Senior Backend Engineer"
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="location">Location</Label>
        <Input
          id="location"
          value={location}
          onChange={(e) => setLocation(e.target.value)}
          maxLength={120}
          placeholder="Berlin, Germany"
        />
      </div>

      <div className="space-y-2">
        <Label>Links</Label>
        {links.map((link, i) => (
          <div key={i} className="flex gap-2">
            <Input
              value={link.label}
              onChange={(e) =>
                setLinks((ls) => ls.map((l, j) => (j === i ? { ...l, label: e.target.value } : l)))
              }
              placeholder="GitHub"
              className="max-w-[10rem]"
              maxLength={60}
              aria-label={`Link ${i + 1} label`}
            />
            <Input
              value={link.url}
              onChange={(e) =>
                setLinks((ls) => ls.map((l, j) => (j === i ? { ...l, url: e.target.value } : l)))
              }
              placeholder="https://github.com/you"
              type="url"
              maxLength={500}
              aria-label={`Link ${i + 1} URL`}
            />
            <Button
              variant="ghost"
              size="icon"
              aria-label="Remove link"
              onClick={() => setLinks((ls) => ls.filter((_, j) => j !== i))}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
        {links.length < 10 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setLinks((ls) => [...ls, { label: '', url: '' }])}
          >
            <Plus className="h-4 w-4" /> Add link
          </Button>
        )}
      </div>

      <Button onClick={() => save.mutate()} loading={save.isPending}>
        Save profile
      </Button>
    </Card>
  );
}
