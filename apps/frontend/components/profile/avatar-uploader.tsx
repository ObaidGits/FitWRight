'use client';

import React, { useCallback, useEffect, useId, useRef, useState } from 'react';
import Upload from 'lucide-react/dist/esm/icons/upload';
import Trash2 from 'lucide-react/dist/esm/icons/trash-2';
import UserRound from 'lucide-react/dist/esm/icons/user-round';
import { cn } from '@/lib/utils';
import { uploadAvatar, deleteAvatar, type AvatarResult } from '@/lib/api/profile';

/**
 * AvatarUploader — the ONE canonical profile-photo upload experience.
 *
 * Shared by Profile Settings and the resume builder's PhotoControls so there is
 * a single upload/replace/remove flow across the whole app (no duplicate logic).
 * It manages the *canonical master* only (the bytes); per-resume presentation
 * lives in PhotoControls. Accessible: labelled controls, keyboard-activatable
 * dropzone, live status region, drag/drop + paste + click.
 *
 * The backend does all hardening (magic-byte sniff, EXIF/GPS strip, canonical
 * WebP re-encode, checksum dedup); the client only validates size/type for a
 * fast, friendly rejection and shows progress.
 */
const MAX_BYTES = 5 * 1024 * 1024;
const ACCEPT = 'image/jpeg,image/png,image/webp,image/avif,image/heic,image/heif';

export interface AvatarUploaderProps {
  avatarUrl: string | null | undefined;
  onUploaded: (result: AvatarResult) => void;
  onRemoved: () => void;
  onError?: (message: string) => void;
  /** Optional dominant-colour placeholder while the current photo loads. */
  dominantColor?: string | null;
  /** Enable clipboard paste-to-upload (default true). */
  enablePaste?: boolean;
  className?: string;
}

export function AvatarUploader({
  avatarUrl,
  onUploaded,
  onRemoved,
  onError,
  dominantColor,
  enablePaste = true,
  className,
}: AvatarUploaderProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [busy, setBusy] = useState<false | 'uploading' | 'removing'>(false);
  const [dragOver, setDragOver] = useState(false);

  const doUpload = useCallback(
    async (file: File) => {
      if (!file.type.startsWith('image/')) {
        onError?.('Please choose an image file (JPEG, PNG, WebP, AVIF, or HEIC).');
        return;
      }
      if (file.size > MAX_BYTES) {
        onError?.('Image is too large (max 5 MB).');
        return;
      }
      setBusy('uploading');
      try {
        onUploaded(await uploadAvatar(file));
      } catch (err) {
        onError?.(err instanceof Error ? err.message : 'Could not upload photo.');
      } finally {
        setBusy(false);
      }
    },
    [onError, onUploaded]
  );

  const remove = useCallback(async () => {
    setBusy('removing');
    try {
      await deleteAvatar();
      onRemoved();
    } catch (err) {
      onError?.(err instanceof Error ? err.message : 'Could not remove photo.');
    } finally {
      setBusy(false);
    }
  }, [onError, onRemoved]);

  useEffect(() => {
    if (!enablePaste) return;
    const onPaste = (e: ClipboardEvent) => {
      const item = Array.from(e.clipboardData?.items ?? []).find((i) =>
        i.type.startsWith('image/')
      );
      const file = item?.getAsFile();
      if (file) void doUpload(file);
    };
    window.addEventListener('paste', onPaste);
    return () => window.removeEventListener('paste', onPaste);
  }, [doUpload, enablePaste]);

  const openPicker = () => inputRef.current?.click();

  return (
    <div className={cn('flex items-center gap-4', className)}>
      <div
        role="button"
        tabIndex={0}
        aria-label={avatarUrl ? 'Replace profile photo' : 'Upload profile photo'}
        onClick={openPicker}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openPicker();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const file = e.dataTransfer.files?.[0];
          if (file) void doUpload(file);
        }}
        className={cn(
          'flex h-16 w-16 shrink-0 cursor-pointer items-center justify-center overflow-hidden rounded-full border transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)]',
          dragOver
            ? 'border-[var(--primary)] bg-[var(--accent)]'
            : 'border-[var(--border)] bg-[var(--secondary)] text-[var(--muted-foreground)]'
        )}
        style={{ background: !avatarUrl && dominantColor ? dominantColor : undefined }}
      >
        {avatarUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- external CDN master; Next/Image adds no value for a tiny avatar and complicates the print route.
          <img
            src={avatarUrl}
            alt="Current profile photo"
            width={64}
            height={64}
            decoding="async"
            className="h-full w-full object-cover"
            style={{ background: dominantColor || undefined }}
          />
        ) : (
          <UserRound className="h-7 w-7" aria-hidden />
        )}
      </div>

      <div className="min-w-0">
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) void doUpload(file);
          }}
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={openPicker}
            disabled={!!busy}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] px-3 py-1.5 text-xs transition-colors hover:bg-[var(--accent)] disabled:opacity-50'
            )}
          >
            <Upload className="h-3.5 w-3.5" /> {avatarUrl ? 'Replace' : 'Upload'}
          </button>
          {avatarUrl && (
            <button
              type="button"
              onClick={() => void remove()}
              disabled={!!busy}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius-at-md)] border border-[var(--border)] bg-[var(--card)] px-3 py-1.5 text-xs transition-colors hover:bg-[var(--accent)] disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" /> Remove
            </button>
          )}
        </div>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]" aria-live="polite">
          {busy === 'uploading'
            ? 'Uploading…'
            : busy === 'removing'
              ? 'Removing…'
              : 'Drag & drop, paste, or upload. JPEG/PNG/WebP/AVIF/HEIC, up to 5 MB.'}
        </p>
      </div>
    </div>
  );
}

export default AvatarUploader;
