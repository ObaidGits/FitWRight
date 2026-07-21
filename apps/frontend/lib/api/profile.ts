/**
 * Extended profile + avatar API (P3 §H, Requirements 13-14).
 *
 * Reusable profile fields (headline/location/links) that prefill resumes, plus a
 * hardened avatar upload (server-side magic-byte sniff, re-encode, EXIF strip -
 * the client just posts the file). All user-scoped + session-authenticated.
 */
import { apiFetch, apiPatch, API_BASE, readCsrfToken } from './client';

export interface ProfileLink {
  label: string;
  url: string;
}

export interface Profile {
  headline: string | null;
  location: string | null;
  links: ProfileLink[];
  avatar_url: string | null;
}

async function asJson<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as {
      detail?: unknown;
      error?: { message?: string };
    };
    const detail = typeof data.detail === 'string' ? data.detail : (data.error?.message ?? null);
    throw new Error(detail || `${fallback} (status ${res.status}).`);
  }
  return res.json() as Promise<T>;
}

export async function getProfile(): Promise<Profile> {
  return asJson<Profile>(
    await apiFetch('/users/me/profile', { credentials: 'include' }),
    'Failed to load profile'
  );
}

export async function updateProfile(update: {
  headline?: string | null;
  location?: string | null;
  links?: ProfileLink[];
}): Promise<Profile> {
  return asJson<Profile>(await apiPatch('/users/me/profile', update), 'Failed to update profile');
}

/** Canonical profile-image master + metadata (mirrors backend AvatarResponse). */
export interface AvatarResult {
  avatar_url: string | null;
  width?: number | null;
  height?: number | null;
  aspect_ratio?: number | null;
  dominant_color?: string | null;
  format?: string | null;
  byte_size?: number | null;
  checksum?: string | null;
  deduplicated?: boolean;
}

/**
 * Upload a profile photo (multipart). The backend produces the canonical
 * aspect-ratio-preserving master, strips EXIF, dedups by checksum, and returns
 * the master URL + metadata. Never trusts the client - accepts JPEG/PNG/WebP/
 * AVIF/HEIC and re-encodes to one canonical WebP master.
 */
export async function uploadAvatar(file: File): Promise<AvatarResult> {
  const form = new FormData();
  form.append('file', file);
  // apiFetch injects the CSRF header for mutating requests; FormData sets its
  // own multipart Content-Type (do not override it).
  const res = await apiFetch('/users/me/avatar', {
    method: 'POST',
    body: form,
    credentials: 'include',
  });
  return asJson<AvatarResult>(res, 'Failed to upload photo');
}

/** Remove the current profile photo (and GC the stored master server-side). */
export async function deleteAvatar(): Promise<AvatarResult> {
  const res = await apiFetch('/users/me/avatar', {
    method: 'DELETE',
    credentials: 'include',
  });
  return asJson<AvatarResult>(res, 'Failed to remove photo');
}

/** Absolute base for building a served media URL if needed. */
export const MEDIA_BASE = API_BASE;
export { readCsrfToken };
