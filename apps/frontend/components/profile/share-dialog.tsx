'use client';

/**
 * Share dialog (P7) — publish the profile to a public page and manage visibility.
 *
 * Publish as **public** (indexable) or **unlisted** (link-only, noindex), copy
 * the share link, open the live page, or unpublish (back to private). The slug
 * is stable across re-publishes. Purely a control surface over the publish API;
 * the public page itself renders from the projection.
 */
import * as React from 'react';
import Globe from 'lucide-react/dist/esm/icons/globe';
import Copy from 'lucide-react/dist/esm/icons/copy';
import ExternalLink from 'lucide-react/dist/esm/icons/external-link';
import Check from 'lucide-react/dist/esm/icons/check';

import { Button } from '@/components/atelier/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogTrigger,
} from '@/components/atelier/dialog';
import { Badge } from '@/components/atelier/badge';
import { useToast } from '@/components/atelier/toast';
import {
  usePublicationState,
  usePublishProfile,
  useUnpublishProfile,
} from '@/features/profile/hooks';

export function ShareDialog() {
  const { toast } = useToast();
  const [open, setOpen] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const state = usePublicationState();
  const publish = usePublishProfile();
  const unpublish = useUnpublishProfile();

  const visibility = state.data?.visibility ?? 'private';
  const slug = state.data?.public_slug ?? null;
  const theme = state.data?.public_theme ?? 'minimal';
  const THEMES: { id: 'minimal' | 'modern' | 'developer'; label: string }[] = [
    { id: 'minimal', label: 'Minimal' },
    { id: 'modern', label: 'Modern' },
    { id: 'developer', label: 'Developer' },
  ];
  const shareUrl =
    slug && typeof window !== 'undefined'
      ? `${window.location.origin}/p/${slug}`
      : slug
        ? `/p/${slug}`
        : '';
  const isLive = visibility !== 'private' && !!slug;

  async function onPublish(v: 'public' | 'unlisted', t?: 'minimal' | 'modern' | 'developer') {
    try {
      await publish.mutateAsync({ visibility: v, theme: t });
      toast({
        title: v === 'public' ? 'Profile is public' : 'Unlisted link ready',
        variant: 'success',
      });
    } catch (err) {
      toast({
        title: 'Could not publish',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function onTheme(t: 'minimal' | 'modern' | 'developer') {
    try {
      await publish.mutateAsync({
        visibility: visibility === 'private' ? 'public' : visibility,
        theme: t,
      });
    } catch (err) {
      toast({
        title: 'Could not change theme',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function onUnpublish() {
    try {
      await unpublish.mutateAsync();
      toast({ title: 'Profile is private', variant: 'success' });
    } catch (err) {
      toast({
        title: 'Could not unpublish',
        description: err instanceof Error ? err.message : undefined,
        variant: 'error',
      });
    }
  }

  async function onCopy() {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast({ title: 'Could not copy', variant: 'error' });
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost">
          <Globe className="h-4 w-4" /> Share
        </Button>
      </DialogTrigger>
      <DialogContent className="w-full max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            Share your profile
            {isLive && (
              <Badge variant={visibility === 'public' ? 'success' : 'neutral'}>
                {visibility === 'public' ? 'Public' : 'Unlisted'}
              </Badge>
            )}
          </DialogTitle>
          <DialogDescription>
            Publish a beautiful public page. Public pages are search-indexable; unlisted pages are
            reachable only by link.
          </DialogDescription>
        </DialogHeader>

        {isLive ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2 rounded-[var(--radius-at-md)] border border-[var(--border)] p-2">
              <input
                readOnly
                value={shareUrl}
                aria-label="Share link"
                className="min-w-0 flex-1 bg-transparent px-1 text-sm outline-none"
              />
              <Button variant="ghost" size="icon" onClick={onCopy} aria-label="Copy link">
                {copied ? (
                  <Check className="h-4 w-4 text-[var(--at-success)]" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </Button>
              <Button asChild variant="ghost" size="icon" aria-label="Open public page">
                <a href={shareUrl} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="h-4 w-4" />
                </a>
              </Button>
            </div>
            <div className="flex flex-wrap gap-2">
              {visibility !== 'public' && (
                <Button
                  variant="outline"
                  onClick={() => onPublish('public')}
                  loading={publish.isPending}
                >
                  Make public
                </Button>
              )}
              {visibility !== 'unlisted' && (
                <Button
                  variant="outline"
                  onClick={() => onPublish('unlisted')}
                  loading={publish.isPending}
                >
                  Make unlisted
                </Button>
              )}
              <Button variant="ghost" onClick={onUnpublish} loading={unpublish.isPending}>
                Unpublish
              </Button>
            </div>
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-[var(--muted-foreground)]">Theme</p>
              <div
                className="flex flex-wrap gap-1"
                role="radiogroup"
                aria-label="Public page theme"
              >
                {THEMES.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    role="radio"
                    aria-checked={theme === t.id}
                    onClick={() => onTheme(t.id)}
                    disabled={publish.isPending}
                    className={
                      'rounded-[var(--radius-at-sm)] px-2.5 py-1 text-xs font-medium transition-colors ' +
                      (theme === t.id
                        ? 'bg-[var(--primary)] text-[var(--primary-foreground)]'
                        : 'border border-[var(--border)] text-[var(--muted-foreground)] hover:bg-[var(--accent)]')
                    }
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => onPublish('public')} loading={publish.isPending}>
              <Globe className="h-4 w-4" /> Publish publicly
            </Button>
            <Button
              variant="outline"
              onClick={() => onPublish('unlisted')}
              loading={publish.isPending}
            >
              Create unlisted link
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
