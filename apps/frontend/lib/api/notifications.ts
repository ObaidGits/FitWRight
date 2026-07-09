/**
 * Notifications interface (Req 33, Task 3.8/21).
 * Transient/local notifications now (via the toast system + an in-memory
 * center); persistent + scheduled notifications (interview tomorrow, key
 * expired, follow-up due) are FUTURE BACKEND behind this interface.
 */
export type NotificationKind = 'transient' | 'persistent';

export interface AppNotification {
  id: string;
  kind: NotificationKind;
  type: string; // e.g. "export_finished", "generation_failed", "parsing_complete"
  message: string;
  nodeRef?: { type: 'resume' | 'application'; id: string };
  read: boolean;
  createdAt: string;
}

export interface NotificationsApi {
  list(): Promise<AppNotification[]>;
  dismiss(id: string): Promise<void>;
}

// Stub: no persistent notifications yet (future backend). Transient events use
// the toast system directly.
export const notificationsApi: NotificationsApi = {
  async list() {
    return [];
  },
  async dismiss() {
    /* no-op until persistent backend exists */
  },
};
