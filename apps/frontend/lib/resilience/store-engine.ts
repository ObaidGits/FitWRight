/**
 * Storage engine abstraction for the durable local store (P4 R8).
 *
 * The durability *logic* (integrity, encryption, quarantine, namespacing,
 * bounds) lives in {@link ./local-store}; this file provides the low-level
 * key/value engines it runs on:
 *
 * - {@link MemoryEngine} — in-process map. Used by unit tests and as the
 *   graceful-degradation fallback when IndexedDB is unavailable (private mode /
 *   disabled storage, R8.4).
 * - {@link IndexedDbEngine} — the real browser store, opened lazily.
 *
 * Stores are logical namespaces ("draft", "outbox", "quarantine", "keys").
 * Keys within a store are already user-namespaced by the caller (R8.3).
 */

export type StoreName = 'draft' | 'outbox' | 'quarantine' | 'keys' | 'meta';

export interface StoreEntry {
  key: string;
  value: unknown;
}

export interface StoreEngine {
  get(store: StoreName, key: string): Promise<unknown>;
  set(store: StoreName, key: string, value: unknown): Promise<void>;
  delete(store: StoreName, key: string): Promise<void>;
  /** All entries in a store, optionally filtered to keys with a prefix. */
  entries(store: StoreName, prefix?: string): Promise<StoreEntry[]>;
  /** Delete every entry in a store whose key starts with `prefix`. */
  deletePrefix(store: StoreName, prefix: string): Promise<void>;
}

export class MemoryEngine implements StoreEngine {
  private data: Record<StoreName, Map<string, unknown>> = {
    draft: new Map(),
    outbox: new Map(),
    quarantine: new Map(),
    keys: new Map(),
    meta: new Map(),
  };

  async get(store: StoreName, key: string): Promise<unknown> {
    return this.data[store].has(key) ? this.data[store].get(key) : undefined;
  }
  async set(store: StoreName, key: string, value: unknown): Promise<void> {
    this.data[store].set(key, value);
  }
  async delete(store: StoreName, key: string): Promise<void> {
    this.data[store].delete(key);
  }
  async entries(store: StoreName, prefix?: string): Promise<StoreEntry[]> {
    const out: StoreEntry[] = [];
    for (const [key, value] of this.data[store]) {
      if (!prefix || key.startsWith(prefix)) out.push({ key, value });
    }
    return out;
  }
  async deletePrefix(store: StoreName, prefix: string): Promise<void> {
    for (const key of [...this.data[store].keys()]) {
      if (key.startsWith(prefix)) this.data[store].delete(key);
    }
  }
}

const DB_NAME = 'fitwright-resilience';
const DB_VERSION = 1;
const STORES: StoreName[] = ['draft', 'outbox', 'quarantine', 'keys', 'meta'];

/** Detect whether IndexedDB is usable (guards private mode / disabled storage). */
export function indexedDbAvailable(): boolean {
  try {
    return typeof indexedDB !== 'undefined' && indexedDB !== null;
  } catch {
    return false;
  }
}

export class IndexedDbEngine implements StoreEngine {
  private dbPromise: Promise<IDBDatabase> | null = null;

  private open(): Promise<IDBDatabase> {
    if (this.dbPromise) return this.dbPromise;
    this.dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        for (const s of STORES) {
          if (!db.objectStoreNames.contains(s)) db.createObjectStore(s);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return this.dbPromise;
  }

  private async tx<R>(
    store: StoreName,
    mode: IDBTransactionMode,
    fn: (os: IDBObjectStore) => IDBRequest<R> | void
  ): Promise<R | undefined> {
    const db = await this.open();
    return new Promise<R | undefined>((resolve, reject) => {
      const transaction = db.transaction(store, mode);
      const os = transaction.objectStore(store);
      let result: R | undefined;
      const req = fn(os);
      if (req) req.onsuccess = () => (result = req.result);
      transaction.oncomplete = () => resolve(result);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  }

  async get(store: StoreName, key: string): Promise<unknown> {
    return this.tx<unknown>(store, 'readonly', (os) => os.get(key));
  }
  async set(store: StoreName, key: string, value: unknown): Promise<void> {
    await this.tx(store, 'readwrite', (os) => os.put(value, key));
  }
  async delete(store: StoreName, key: string): Promise<void> {
    await this.tx(store, 'readwrite', (os) => os.delete(key));
  }
  async entries(store: StoreName, prefix?: string): Promise<StoreEntry[]> {
    const db = await this.open();
    return new Promise<StoreEntry[]>((resolve, reject) => {
      const out: StoreEntry[] = [];
      const transaction = db.transaction(store, 'readonly');
      const os = transaction.objectStore(store);
      const cursorReq = os.openCursor();
      cursorReq.onsuccess = () => {
        const cursor = cursorReq.result;
        if (cursor) {
          const key = String(cursor.key);
          if (!prefix || key.startsWith(prefix)) out.push({ key, value: cursor.value });
          cursor.continue();
        }
      };
      transaction.oncomplete = () => resolve(out);
      transaction.onerror = () => reject(transaction.error);
    });
  }
  async deletePrefix(store: StoreName, prefix: string): Promise<void> {
    const db = await this.open();
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(store, 'readwrite');
      const os = transaction.objectStore(store);
      const cursorReq = os.openCursor();
      cursorReq.onsuccess = () => {
        const cursor = cursorReq.result;
        if (cursor) {
          if (String(cursor.key).startsWith(prefix)) cursor.delete();
          cursor.continue();
        }
      };
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
    });
  }
}
