/**
 * Version-history interface (Req 31, Task 3.8/19).
 * Available-data operations (restore original, undo last AI) are wired to the
 * existing backend; full snapshot/branching is FUTURE BACKEND. The UI reads
 * from this typed interface so real history can replace it with no UI change.
 */
import type { ResumeVersion } from '@/lib/types/domain';

export interface HistoryApi {
  listVersions(resumeId: string): Promise<ResumeVersion[]>;
  restoreOriginal(resumeId: string): Promise<void>;
  undoLastAi(resumeId: string): Promise<void>;
}

// Stub: returns empty history now. Real snapshots are future backend.
export const historyApi: HistoryApi = {
  async listVersions() {
    return [];
  },
  async restoreOriginal() {
    // Wired via existing resume endpoints when the editor integrates it.
  },
  async undoLastAi() {
    // Wired via existing diff/improve data when the editor integrates it.
  },
};
