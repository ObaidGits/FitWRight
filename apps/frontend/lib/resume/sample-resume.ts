/**
 * A realistic sample resume used for template gallery thumbnails and previews.
 *
 * It exercises every major section (summary, experience, projects, education,
 * skills) so a template's real layout - not an empty shell - is what the user
 * evaluates in the gallery. Pure data; rendered through the shared renderer.
 */
import type { ResumeData } from '@/components/dashboard/resume-component';
import { DEFAULT_PHOTO_CONFIG } from '@/lib/types/photo';

/**
 * Bundled, neutral placeholder headshot (a silhouette, not a real person) served
 * from `public/`. Used only to visualize photo-capable templates in the gallery
 * so a user can see WHERE and HOW a profile photo renders before choosing one.
 */
export const SAMPLE_AVATAR_URL = '/sample-avatar.svg';

export const SAMPLE_RESUME: ResumeData = {
  personalInfo: {
    name: 'Alex Morgan',
    title: 'Senior Software Engineer',
    email: 'alex.morgan@example.com',
    phone: '+1 555 0142',
    location: 'San Francisco, CA',
    website: 'alexmorgan.dev',
    linkedin: 'linkedin.com/in/alexmorgan',
    github: 'github.com/alexmorgan',
  },
  summary:
    'Senior software engineer with 8 years building reliable, high-scale web platforms. Focused on clean architecture, developer experience, and measurable product impact.',
  workExperience: [
    {
      id: 1,
      title: 'Senior Software Engineer',
      company: 'Northwind Labs',
      location: 'San Francisco, CA',
      years: 'Mar 2021 - Present',
      description: [
        'Led a 5-engineer team rebuilding the billing platform, cutting invoice errors by 38%.',
        'Designed an event-driven ingestion pipeline handling 20M+ events/day.',
        'Mentored 4 engineers and introduced a design-review practice adopted org-wide.',
      ],
    },
    {
      id: 2,
      title: 'Software Engineer',
      company: 'BrightWave',
      location: 'Remote',
      years: 'Jun 2017 - Feb 2021',
      description: [
        'Shipped the customer dashboard used by 120K monthly active users.',
        'Reduced API p95 latency by 45% through query and caching improvements.',
      ],
    },
  ],
  education: [
    {
      id: 1,
      institution: 'University of Washington',
      degree: 'B.S. Computer Science',
      years: '2013 - 2017',
      description: 'Graduated with honors; ACM chapter lead.',
    },
  ],
  personalProjects: [
    {
      id: 1,
      name: 'OpenLedger',
      role: 'Creator',
      years: '2022 - Present',
      github: 'github.com/alexmorgan/openledger',
      description: ['Open-source double-entry accounting library with 1.2k stars.'],
    },
  ],
  additional: {
    technicalSkills: ['TypeScript', 'React', 'Node.js', 'Python', 'PostgreSQL', 'AWS', 'Docker'],
    languages: ['English (Native)', 'Spanish (Professional)'],
    certificationsTraining: ['AWS Solutions Architect'],
    awards: ['Engineering Excellence Award, 2023'],
  },
  customSections: {},
  sectionMeta: [],
};

/**
 * The same sample resume, but with the profile photo enabled and pointed at the
 * bundled placeholder headshot. The gallery renders THIS variant for
 * photo-capable templates (photoSupport !== 'none') so their photo slot is
 * actually demonstrated, while photo-incapable templates keep using the
 * photo-less {@link SAMPLE_RESUME}. `position: 'template-default'` lets each
 * template place the photo in its own default slot (header/sidebar).
 */
export const SAMPLE_RESUME_WITH_PHOTO: ResumeData = {
  ...SAMPLE_RESUME,
  personalInfo: {
    ...SAMPLE_RESUME.personalInfo,
    avatarUrl: SAMPLE_AVATAR_URL,
    photo: {
      ...DEFAULT_PHOTO_CONFIG,
      show: true,
      shape: 'circle',
      size: 'lg',
    },
  },
};
