import { ProfileWorkspace } from '@/components/profile/profile-workspace';

export const metadata = {
  title: 'Profile',
  description: 'Your canonical career profile — the source of truth for every resume.',
};

export default function ProfilePage() {
  return <ProfileWorkspace />;
}
