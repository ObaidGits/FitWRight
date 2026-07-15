/**
 * WebCrypto encryption-at-rest for local drafts/outbox (P4 R8.2).
 *
 * Sensitive resume content persisted to IndexedDB is encrypted with AES-GCM
 * under a per-user, **non-extractable** key. The key is generated in the
 * browser, cached in memory, and stored (as a non-extractable `CryptoKey`, via
 * structured clone) in a user-namespaced IndexedDB keystore so it survives a
 * reload (crash recovery needs to decrypt the draft) yet is wiped on logout with
 * the rest of the user's local data. Non-extractable means the raw key bytes
 * never leave the WebCrypto boundary.
 *
 * Everything here is pure/instance-based and takes an injected keystore, so it
 * unit-tests without IndexedDB.
 */

const ALGO = 'AES-GCM';
const IV_BYTES = 12;

function toBase64(bytes: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

function fromBase64(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export interface EncryptedPayload {
  iv: string;
  ct: string;
}

/** Whether WebCrypto AES-GCM is available (private-mode/old-browser guard). */
export function cryptoAvailable(): boolean {
  return (
    typeof globalThis.crypto !== 'undefined' && typeof globalThis.crypto.subtle !== 'undefined'
  );
}

/** Generate a fresh non-extractable AES-GCM key. */
export async function generateKey(): Promise<CryptoKey> {
  return globalThis.crypto.subtle.generateKey(
    { name: ALGO, length: 256 },
    /* extractable */ false,
    ['encrypt', 'decrypt']
  );
}

/** Encrypt a JSON-serialisable value; returns base64 iv + ciphertext. */
export async function encryptJSON(key: CryptoKey, value: unknown): Promise<EncryptedPayload> {
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const data = new TextEncoder().encode(JSON.stringify(value));
  const ct = await globalThis.crypto.subtle.encrypt({ name: ALGO, iv }, key, data);
  return { iv: toBase64(iv), ct: toBase64(new Uint8Array(ct)) };
}

/** Decrypt a payload produced by {@link encryptJSON}. Throws on tamper/bad key. */
export async function decryptJSON(key: CryptoKey, payload: EncryptedPayload): Promise<unknown> {
  const iv = fromBase64(payload.iv);
  const ct = fromBase64(payload.ct);
  const plain = await globalThis.crypto.subtle.decrypt({ name: ALGO, iv }, key, ct);
  return JSON.parse(new TextDecoder().decode(plain));
}
