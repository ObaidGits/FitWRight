# Profile Photo System

A single, future-proof architecture for the user's profile photo across the
whole FitWright ecosystem: Professional Profile, Resume Builder, Templates,
Preview, PDF export, Tailored resumes, Public Profile, Portfolio, and any future
website/template generator.

## Absolute rules (and how they are enforced)

| Rule | Enforcement |
| --- | --- |
| Never duplicate image storage | One canonical master per user (`users.avatar_url` + `avatar_key`). Responsive variants are **CDN URL transforms**, never new uploads. |
| Never store multiple copies unnecessarily | Content-addressed **dedup** via SHA-256 (`users.avatar_checksum`): re-uploading the same file is a no-op. |
| Never mutate the original / never degrade quality | The master preserves aspect ratio (no crop); all crop/shape/reposition are **render-time** CSS/CDN transforms. |
| Never store images inside Resume JSON | Resume JSON stores a `PhotoConfig` (render + provenance) and a URL reference - never bytes. |
| Never tightly couple templates to one layout | Templates declare **capabilities** (`template-capabilities.ts`); a shared `<PhotoFrame>` renders. |
| Scale to millions | Only metadata in the DB; bytes on the CDN; immutable, content-unique URLs. |

## Storage & format

- Canonical output format: **WebP** (alpha, broad browser + PDF-engine support,
  smaller than PNG/JPEG at equal quality).
- Accepted inputs: JPEG, PNG, WebP, AVIF (native in Pillow ≥ 11), and HEIC/HEIF
  when the optional `pillow-heif` plugin is installed (otherwise rejected
  cleanly).
- Master is downscaled so its longest edge ≤ `IMAGE_MASTER_MAX_DIMENSION`
  (default 1024), aspect ratio preserved, quality `IMAGE_MASTER_QUALITY`
  (default 85).

## Pipeline (backend `app/storage/image.py`)

`process_profile_image(bytes) -> ProcessedImage`:
byte-cap -> magic-byte sniff (no SVG/polyglot) -> decompression-bomb guard ->
decode -> EXIF-orientation transpose -> RGB -> aspect-preserving downscale (never
upscale) -> strip metadata -> WebP re-encode -> metadata
(dims / aspect / checksum / source_format / byte_size / dominant_color).

CDN derivation (no re-upload): `derive_cdn_url(...)`, `responsive_srcset(...)`
inject a Cloudinary transformation segment; for local/external masters the URL
is returned unchanged.

## Photo configuration & provenance

`PhotoConfig` (backend `app/profile/photo.py`, frontend `lib/types/photo.ts`,
mirrored 1:1) lives in `resume.processed_data.personalInfo.photo`:

- **Presentation**: `show`, `shape` (circle/rounded/square/custom + `radius`),
  `size` (xs-xl/custom), `align`, `position`, `crop` (cover/contain/fill),
  `offsetX`/`offsetY` (reposition), `zoom`, `border`(+width/color), `shadow`,
  `background`, `opacity`, `margin`.
- **Provenance** (`ref`):
  - `canonical` - tracks the user's **live** profile photo; re-resolved on every
    resume read (`resumes._reresolve_canonical_photo`).
  - `snapshot` - pinned to the master captured at generation time; a later
    profile-photo change never mutates the resume.

`resolve_photo_url(config, profile_avatar_url)` is the single authority for which
URL renders (backend + frontend identical).

## Rendering (preview == PDF)

`components/resume/photo-frame.tsx` (`<PhotoFrame>`) is the one renderer used by
every template. Both the on-screen preview and the print route mount the same
template components, so the PDF is pixel-identical. The resume photo loads
**eagerly** with synchronous decode; the PDF renderer additionally waits for all
images to finish (`app/pdf.py`) so exports never capture a missing/blurry photo.

Templates declare capabilities in `lib/types/template-capabilities.ts`
(`supportsPhoto`, default/allowed positions, preferred size/shape). LaTeX is
photo-incapable by academic convention and disables the photo gracefully.

## Public profile / portfolio / SEO

- `components/common/profile-avatar.tsx` (`<ProfileAvatar>`) renders the public
  photo with responsive `srcSet` + `sizes`, explicit `width`/`height` (CLS
  reservation), dominant-colour placeholder, `decoding`, and an above-the-fold
  eager/`fetchpriority=high` strategy (lazy otherwise). Falls back to initials.
- The backend public projection exposes `avatarSrcset` (derived from the master)
  and enriches `avatarWidth/Height/DominantColor` from the user record.
- JSON-LD emits a schema.org `ImageObject` (with dimensions when known).
  OpenGraph/Twitter cards use the avatar URL.
- Portfolio reuses the public view (no duplicate rendering).

## Upload UX (one experience everywhere)

`components/profile/avatar-uploader.tsx` (`<AvatarUploader>`) is the single
upload/replace/remove experience, shared by **Profile Settings** and the resume
builder's **PhotoControls**. Drag-drop + click + paste, keyboard-activatable,
live status region, client-side size/type pre-checks. `PhotoControls`
(`components/builder/photo-controls.tsx`) composes it with the per-resume
presentation/provenance editor and a live `<PhotoFrame>` preview.

## Flows that preserve photo rules

- **Generate resume** - projection stamps the `PhotoConfig`; snapshots freeze the
  current master URL at creation.
- **Sync (profile -> resume)** - carries the resume's existing `PhotoConfig`
  through re-projection (never clobbers shape/position/provenance).
- **Tailor (JD)** - `personalInfo` (incl. photo) is a blocked path and is
  restored verbatim from the original; the LLM never rewrites it.
- **Version restore** - restores `processed_data` verbatim -> photo preserved.
- **Import/Merge** - operate on the profile document; resume-level photo is
  untouched.

## Database (migration `0018`)

Metadata-only columns on `users` (never binary): `avatar_width`,
`avatar_height`, `avatar_checksum`, `avatar_format`, `avatar_bytes`,
`avatar_dominant_color`, `avatar_updated_at`. Additive, nullable, reversible.

## API

- `POST /users/me/avatar` - upload -> canonical master + checksum dedup; returns
  URL + metadata (`AvatarResponse`, `deduplicated`).
- `DELETE /users/me/avatar` - remove photo + GC the master.
- `POST /profile/generate-resume` - accepts a full `photo` config.
- Resume reads re-resolve `canonical` photos to the live master.

## Security

Magic-byte sniff (no SVG/polyglot), MIME never trusted, byte + pixel caps,
decompression-bomb guard, EXIF/GPS strip, canonical re-encode, traversal-safe
server-generated keys, signed Cloudinary uploads with retry/backoff, orphan GC,
authenticated + owner-scoped endpoints, public read surface rate-limited.

## Config

`AVATAR_MAX_BYTES` (5 MB), `AVATAR_MAX_DIMENSION` (4096, bomb guard),
`IMAGE_MASTER_MAX_DIMENSION` (1024), `IMAGE_MASTER_QUALITY` (85),
`STORAGE_PROVIDER` + `CLOUDINARY_*`.
