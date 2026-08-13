/** Shared workflow-first navigation definition (Home - Resumes - Applications). */
import Home from 'lucide-react/dist/esm/icons/house';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import Layers from 'lucide-react/dist/esm/icons/layers';
import CalendarClock from 'lucide-react/dist/esm/icons/calendar-clock';
import Compass from 'lucide-react/dist/esm/icons/compass';
import MessageSquare from 'lucide-react/dist/esm/icons/message-square-text';

export interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

export const PRIMARY_NAV: NavItem[] = [
  { href: '/home', label: 'Home', icon: Home },
  { href: '/resumes', label: 'Resumes', icon: FileText },
  { href: '/discovery', label: 'Discover', icon: Compass },
  { href: '/applications', label: 'Applications', icon: Layers },
  // Questions forms asked that need an answer. Primary navigation rather than a
  // Settings tab: this is touched after most applications, and burying daily
  // work in Settings is how it stops getting done.
  { href: '/answers', label: 'Answers', icon: MessageSquare },
  { href: '/agenda', label: 'Agenda', icon: CalendarClock },
];

export const TAILOR_HREF = '/tailor';
