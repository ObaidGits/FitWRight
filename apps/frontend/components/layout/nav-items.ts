/** Shared workflow-first navigation definition (Home · Resumes · Applications). */
import Home from 'lucide-react/dist/esm/icons/house';
import FileText from 'lucide-react/dist/esm/icons/file-text';
import Layers from 'lucide-react/dist/esm/icons/layers';
import CalendarClock from 'lucide-react/dist/esm/icons/calendar-clock';

export interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

export const PRIMARY_NAV: NavItem[] = [
  { href: '/home', label: 'Home', icon: Home },
  { href: '/resumes', label: 'Resumes', icon: FileText },
  { href: '/applications', label: 'Applications', icon: Layers },
  { href: '/agenda', label: 'Agenda', icon: CalendarClock },
];

export const TAILOR_HREF = '/tailor';
