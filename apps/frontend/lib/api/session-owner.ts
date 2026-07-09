/**
 * The synthetic bootstrap owner used in `SINGLE_USER_MODE` (Task 8.1).
 *
 * Shared by the client `SessionProvider` and the server session check so local
 * zero-config boot presents the same admin owner everywhere. Kept in its own
 * (isomorphic) module so the client never imports the server-only
 * `session-server` module.
 */
import type { SafeUser } from './auth';

export const OWNER_USER: SafeUser = {
  id: 'local-owner',
  name: 'You',
  email: '',
  role: 'admin',
  status: 'active',
  emailVerified: true,
  aal: 'aal1',
  avatarUrl: null,
};
