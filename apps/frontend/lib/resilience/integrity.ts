/**
 * Local record integrity (P4 R8.1, Property 7).
 *
 * Every durable local record carries a schema version and a content hash of its
 * plaintext payload. On read the hash is recomputed and the schema version
 * checked; a mismatch (corruption, tamper, partial write, or an
 * incompatible-schema record from an old build) triggers quarantine rather than
 * loading poison into the live editor.
 */

/** Bump when the durable record shape changes incompatibly. */
export const SCHEMA_VERSION = 1;

function stableStringify(value: unknown): string {
  return JSON.stringify(value, (_k, v) => {
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      return Object.keys(v as Record<string, unknown>)
        .sort()
        .reduce(
          (acc, k) => {
            acc[k] = (v as Record<string, unknown>)[k];
            return acc;
          },
          {} as Record<string, unknown>
        );
    }
    return v;
  });
}

/** SHA-256 hex hash of a value's stable JSON serialisation. */
export async function contentHash(value: unknown): Promise<string> {
  const data = new TextEncoder().encode(stableStringify(value));
  const digest = await globalThis.crypto.subtle.digest('SHA-256', data);
  const bytes = new Uint8Array(digest);
  let hex = '';
  for (let i = 0; i < bytes.length; i++) hex += bytes[i].toString(16).padStart(2, '0');
  return hex;
}

export interface DurableRecordMeta {
  schemaVersion: number;
  contentHash: string;
  savedAt: number;
  baseVersion: number | null;
}

export type IntegrityFailure =
  | 'schema_mismatch'
  | 'hash_mismatch'
  | 'malformed'
  | 'decrypt_failure';

export interface IntegrityResult {
  ok: boolean;
  reason?: IntegrityFailure;
}

/** Validate a record's schema version + recomputed hash against its stored meta. */
export function validateIntegrity(
  meta: Partial<DurableRecordMeta> | null | undefined,
  recomputedHash: string
): IntegrityResult {
  if (!meta || typeof meta !== 'object') return { ok: false, reason: 'malformed' };
  if (meta.schemaVersion !== SCHEMA_VERSION) return { ok: false, reason: 'schema_mismatch' };
  if (typeof meta.contentHash !== 'string' || !meta.contentHash) {
    return { ok: false, reason: 'malformed' };
  }
  if (meta.contentHash !== recomputedHash) return { ok: false, reason: 'hash_mismatch' };
  return { ok: true };
}
