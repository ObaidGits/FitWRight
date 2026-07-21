/**
 * Field-level diff + merge for explicit conflict resolution (P4 R3.2, R3.6).
 *
 * When a version-CAS write is rejected (409), the client must present an
 * informed choice: **keep mine**, **take latest**, or **field-merge**. Field
 * merge is only offered when the two edits touched *disjoint* field sets;
 * otherwise the safe options are keep/latest (no automatic merge of overlapping
 * edits - this spec explicitly does not do CRDT).
 *
 * "Fields" here are the top-level keys of the structured resume object plus a
 * shallow notion of change: a key is "changed" if its JSON serialisation
 * differs from the common base. This is deterministic and keyboard/SR-friendly
 * to render as a readable diff.
 */

export interface FieldChange {
  field: string;
  base: unknown;
  value: unknown;
}

export interface ConflictDiff {
  /** Fields the local edit changed relative to the base. */
  mineChanged: FieldChange[];
  /** Fields the server (latest) changed relative to the base. */
  latestChanged: FieldChange[];
  /** Fields both sides changed (the overlap). */
  overlapping: string[];
  /** True when field-merge is safe to offer (no overlapping changes). */
  mergeable: boolean;
}

function stable(value: unknown): string {
  // Stable stringify: sort object keys so key-order noise isn't a false diff.
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

function changedFields(
  base: Record<string, unknown>,
  other: Record<string, unknown>
): FieldChange[] {
  const keys = new Set([...Object.keys(base), ...Object.keys(other)]);
  const changes: FieldChange[] = [];
  for (const field of keys) {
    if (stable(base[field]) !== stable(other[field])) {
      changes.push({ field, base: base[field], value: other[field] });
    }
  }
  return changes;
}

/**
 * Compute a field-level conflict diff between the common `base`, the local
 * edit (`mine`) and the current server state (`latest`).
 *
 * When `base` is unknown (e.g. the client never held the exact base), pass
 * `latest` as the base for `mine` so every locally-changed field surfaces; the
 * caller then treats any overlap conservatively.
 */
export function computeConflictDiff(
  base: Record<string, unknown>,
  mine: Record<string, unknown>,
  latest: Record<string, unknown>
): ConflictDiff {
  const mineChanged = changedFields(base, mine);
  const latestChanged = changedFields(base, latest);
  const mineSet = new Set(mineChanged.map((c) => c.field));
  const overlapping = latestChanged.map((c) => c.field).filter((f) => mineSet.has(f));
  return {
    mineChanged,
    latestChanged,
    overlapping,
    mergeable: overlapping.length === 0 && mineChanged.length > 0,
  };
}

/**
 * Field-merge: start from `latest` (the newer server state) and apply only the
 * fields the local edit changed. Safe **only** when {@link computeConflictDiff}
 * reported `mergeable` (disjoint changes); the caller must enforce that.
 */
export function fieldMerge(
  latest: Record<string, unknown>,
  mineChanged: FieldChange[]
): Record<string, unknown> {
  const merged = { ...latest };
  for (const change of mineChanged) {
    merged[change.field] = change.value;
  }
  return merged;
}
