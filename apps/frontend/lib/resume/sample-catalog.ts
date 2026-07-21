/**
 * Resume Sample Library - realistic, ready-to-use example resumes.
 *
 * Distinct from the *template* catalog (which is appearance/layout): a sample is
 * fully-authored **content** (`ResumeData`) for a profession, that a user can
 * preview, use (creates a new resume via `POST /resumes/from-data`), or copy the
 * structure of. Samples reuse the shared renderer and pair with a recommended
 * template. Adding a sample = one entry here (metadata + `data`) - no core code.
 */
import type { ResumeData } from '@/components/dashboard/resume-component';
import type { ExperienceLevel, TemplateCategory } from '@/lib/resume/template-catalog';

export interface ResumeSample {
  id: string; // URL-safe slug
  name: string;
  role: string;
  category: TemplateCategory;
  industry: string;
  experienceLevel: ExperienceLevel;
  atsScore: 1 | 2 | 3 | 4 | 5;
  /** Recommended template id (from the template catalog) for this profession. */
  recommendedTemplateId: string;
  hasPhoto: boolean;
  tags: string[];
  description: string;
  data: ResumeData;
}

function base(
  personalInfo: ResumeData['personalInfo'],
  summary: string,
  workExperience: NonNullable<ResumeData['workExperience']>,
  education: NonNullable<ResumeData['education']>,
  additional: ResumeData['additional'],
  personalProjects: NonNullable<ResumeData['personalProjects']> = []
): ResumeData {
  return {
    personalInfo,
    summary,
    workExperience,
    education,
    personalProjects,
    additional,
    customSections: {},
    sectionMeta: [],
  };
}

export const RESUME_SAMPLES: ResumeSample[] = [
  {
    id: 'software-engineer',
    name: 'Software Engineer',
    role: 'Software Engineer',
    category: 'technology',
    industry: 'Software',
    experienceLevel: 'senior',
    atsScore: 5,
    recommendedTemplateId: 'software-engineer',
    hasPhoto: false,
    tags: ['software', 'backend', 'apis', 'distributed systems'],
    description:
      'A results-focused software engineer resume emphasizing scale, reliability, and leadership.',
    data: base(
      {
        name: 'Jordan Rivera',
        title: 'Senior Software Engineer',
        email: 'jordan.rivera@example.com',
        phone: '+1 555 0110',
        location: 'Austin, TX',
        github: 'github.com/jordanrivera',
        linkedin: 'linkedin.com/in/jordanrivera',
      },
      'Senior software engineer with 8+ years building high-scale backend platforms. Drives reliability, clean architecture, and mentorship across teams.',
      [
        {
          id: 1,
          title: 'Senior Software Engineer',
          company: 'Skyline Systems',
          location: 'Austin, TX',
          years: 'Feb 2021 - Present',
          description: [
            'Re-architected the order pipeline to an event-driven design handling 30M events/day, cutting p99 latency 42%.',
            'Led a 6-engineer team and introduced service SLOs, reducing production incidents 55%.',
            'Mentored 5 engineers; established the internal design-review guild.',
          ],
        },
        {
          id: 2,
          title: 'Software Engineer',
          company: 'Loop Retail',
          location: 'Remote',
          years: 'Jul 2016 - Jan 2021',
          description: [
            'Built the payments service processing $80M/year with 99.98% uptime.',
            'Cut API costs 30% via caching and query optimization.',
          ],
        },
      ],
      [
        {
          id: 1,
          institution: 'University of Texas',
          degree: 'B.S. Computer Science',
          years: '2012 - 2016',
        },
      ],
      {
        technicalSkills: ['Go', 'Python', 'PostgreSQL', 'Kafka', 'AWS', 'Kubernetes', 'gRPC'],
        certificationsTraining: ['AWS Solutions Architect - Professional'],
        awards: ['Engineering Excellence Award, 2023'],
      },
      [
        {
          id: 1,
          name: 'ratelimit-go',
          role: 'Author',
          years: '2022 - Present',
          github: 'github.com/jordanrivera/ratelimit-go',
          description: ['Distributed rate limiter library, 900+ stars.'],
        },
      ]
    ),
  },
  {
    id: 'frontend-developer',
    name: 'Frontend Developer',
    role: 'Frontend Developer',
    category: 'technology',
    industry: 'Software',
    experienceLevel: 'mid',
    atsScore: 4,
    recommendedTemplateId: 'frontend-developer',
    hasPhoto: false,
    tags: ['frontend', 'react', 'typescript', 'ui'],
    description:
      'A frontend developer resume highlighting UI craft, performance, and accessibility.',
    data: base(
      {
        name: 'Mia Chen',
        title: 'Frontend Developer',
        email: 'mia.chen@example.com',
        location: 'Seattle, WA',
        github: 'github.com/miachen',
        website: 'miachen.dev',
      },
      'Frontend developer with 5 years crafting fast, accessible React interfaces used by millions.',
      [
        {
          id: 1,
          title: 'Frontend Developer',
          company: 'Brightwave',
          location: 'Seattle, WA',
          years: 'Mar 2020 - Present',
          description: [
            'Rebuilt the design-system in React + TypeScript, adopted by 8 product teams.',
            'Improved Lighthouse performance from 61 to 96 and cut bundle size 38%.',
            'Drove WCAG 2.1 AA compliance across the checkout flow.',
          ],
        },
      ],
      [
        {
          id: 1,
          institution: 'University of Washington',
          degree: 'B.S. Informatics',
          years: '2015 - 2019',
        },
      ],
      {
        technicalSkills: [
          'TypeScript',
          'React',
          'Next.js',
          'CSS',
          'Testing Library',
          'Vite',
          'Accessibility',
        ],
      }
    ),
  },
  {
    id: 'backend-developer',
    name: 'Backend Developer',
    role: 'Backend Developer',
    category: 'technology',
    industry: 'Software',
    experienceLevel: 'mid',
    atsScore: 4,
    recommendedTemplateId: 'backend-developer',
    hasPhoto: false,
    tags: ['backend', 'apis', 'databases', 'microservices'],
    description: 'A backend developer resume focused on APIs, data, and service reliability.',
    data: base(
      {
        name: 'Diego Santos',
        title: 'Backend Developer',
        email: 'diego.santos@example.com',
        location: 'Miami, FL',
        github: 'github.com/diegosantos',
      },
      'Backend developer specializing in resilient APIs and data pipelines for high-throughput products.',
      [
        {
          id: 1,
          title: 'Backend Developer',
          company: 'Cargo Labs',
          location: 'Miami, FL',
          years: 'Jun 2019 - Present',
          description: [
            'Designed REST + gRPC services powering a logistics platform with 200K daily users.',
            'Introduced idempotent job processing, eliminating duplicate-charge incidents.',
          ],
        },
      ],
      [
        {
          id: 1,
          institution: 'Florida International University',
          degree: 'B.S. Computer Engineering',
          years: '2014 - 2018',
        },
      ],
      {
        technicalSkills: ['Java', 'Spring Boot', 'PostgreSQL', 'Redis', 'RabbitMQ', 'Docker'],
      }
    ),
  },
  {
    id: 'devops-engineer',
    name: 'DevOps Engineer',
    role: 'DevOps Engineer',
    category: 'technology',
    industry: 'Cloud & Infrastructure',
    experienceLevel: 'senior',
    atsScore: 4,
    recommendedTemplateId: 'devops-cloud',
    hasPhoto: false,
    tags: ['devops', 'sre', 'cloud', 'ci/cd'],
    description: 'A DevOps/SRE resume emphasizing automation, reliability, and cost control.',
    data: base(
      {
        name: 'Priya Nair',
        title: 'DevOps Engineer',
        email: 'priya.nair@example.com',
        location: 'Denver, CO',
        linkedin: 'linkedin.com/in/priyanair',
      },
      'DevOps engineer automating cloud infrastructure and safeguarding uptime for large-scale services.',
      [
        {
          id: 1,
          title: 'Senior DevOps Engineer',
          company: 'Nimbus Cloud',
          location: 'Denver, CO',
          years: 'Jan 2020 - Present',
          description: [
            'Migrated 120 services to Kubernetes with zero-downtime blue/green deploys.',
            'Cut cloud spend 28% via autoscaling and rightsizing.',
            'Built the incident-response runbooks; MTTR down from 90 to 22 minutes.',
          ],
        },
      ],
      [
        {
          id: 1,
          institution: 'Colorado State University',
          degree: 'B.S. Computer Science',
          years: '2011 - 2015',
        },
      ],
      {
        technicalSkills: [
          'Terraform',
          'Kubernetes',
          'AWS',
          'GitHub Actions',
          'Prometheus',
          'Ansible',
        ],
        certificationsTraining: ['Certified Kubernetes Administrator (CKA)'],
      }
    ),
  },
  {
    id: 'data-scientist',
    name: 'Data Scientist',
    role: 'Data Scientist',
    category: 'technology',
    industry: 'Data & Analytics',
    experienceLevel: 'mid',
    atsScore: 4,
    recommendedTemplateId: 'data-scientist',
    hasPhoto: false,
    tags: ['data science', 'machine learning', 'python', 'analytics'],
    description: 'A data scientist resume showcasing measurable model impact and experimentation.',
    data: base(
      {
        name: 'Aisha Khan',
        title: 'Data Scientist',
        email: 'aisha.khan@example.com',
        location: 'Boston, MA',
        github: 'github.com/aishakhan',
      },
      'Data scientist turning messy data into shipped models and measurable business lift.',
      [
        {
          id: 1,
          title: 'Data Scientist',
          company: 'Northlight Analytics',
          location: 'Boston, MA',
          years: 'Aug 2020 - Present',
          description: [
            'Built a churn model that reduced monthly churn 18% ($4.2M ARR retained).',
            'Ran 40+ A/B experiments; established the causal-inference review process.',
          ],
        },
      ],
      [
        {
          id: 1,
          institution: 'Boston University',
          degree: 'M.S. Statistics',
          years: '2017 - 2019',
        },
      ],
      {
        technicalSkills: ['Python', 'pandas', 'scikit-learn', 'SQL', 'PyTorch', 'Airflow'],
      }
    ),
  },
  {
    id: 'product-manager',
    name: 'Product Manager',
    role: 'Product Manager',
    category: 'professional',
    industry: 'Product',
    experienceLevel: 'senior',
    atsScore: 4,
    recommendedTemplateId: 'corporate-professional',
    hasPhoto: false,
    tags: ['product', 'strategy', 'roadmap', 'leadership'],
    description:
      'A product manager resume centered on outcomes, strategy, and cross-functional leadership.',
    data: base(
      {
        name: 'Ethan Brooks',
        title: 'Senior Product Manager',
        email: 'ethan.brooks@example.com',
        location: 'New York, NY',
        linkedin: 'linkedin.com/in/ethanbrooks',
      },
      'Product manager who ships outcomes: owns strategy, discovery, and delivery for B2B SaaS at scale.',
      [
        {
          id: 1,
          title: 'Senior Product Manager',
          company: 'Vantage SaaS',
          location: 'New York, NY',
          years: 'Apr 2019 - Present',
          description: [
            'Led a platform relaunch that grew activation 34% and NRR to 121%.',
            'Defined the AI roadmap; shipped 3 features driving 22% of new revenue.',
          ],
        },
      ],
      [
        {
          id: 1,
          institution: 'Cornell University',
          degree: 'B.A. Economics',
          years: '2010 - 2014',
        },
      ],
      {
        technicalSkills: ['Roadmapping', 'Discovery', 'SQL', 'A/B Testing', 'Figma', 'Analytics'],
      }
    ),
  },
  {
    id: 'ux-designer',
    name: 'UX / Product Designer',
    role: 'Product Designer',
    category: 'creative',
    industry: 'Design',
    experienceLevel: 'mid',
    atsScore: 3,
    recommendedTemplateId: 'ux-product-designer',
    hasPhoto: true,
    tags: ['ux', 'ui', 'product design', 'research'],
    description:
      'A product designer resume foregrounding process, research, and measurable UX impact.',
    data: base(
      {
        name: 'Sofia Marino',
        title: 'Product Designer',
        email: 'sofia.marino@example.com',
        location: 'San Francisco, CA',
        website: 'sofiamarino.design',
      },
      'Product designer blending research, interaction, and visual craft to ship loved experiences.',
      [
        {
          id: 1,
          title: 'Product Designer',
          company: 'Kite',
          location: 'San Francisco, CA',
          years: 'May 2020 - Present',
          description: [
            'Owned end-to-end design of onboarding; raised completion 47%.',
            'Ran generative and evaluative research to steer the roadmap.',
          ],
        },
      ],
      [{ id: 1, institution: 'RISD', degree: 'BFA Graphic Design', years: '2013 - 2017' }],
      {
        technicalSkills: [
          'Figma',
          'Prototyping',
          'User Research',
          'Design Systems',
          'Accessibility',
        ],
      }
    ),
  },
  {
    id: 'marketing-manager',
    name: 'Marketing Manager',
    role: 'Marketing Manager',
    category: 'creative',
    industry: 'Marketing',
    experienceLevel: 'mid',
    atsScore: 3,
    recommendedTemplateId: 'marketing-content',
    hasPhoto: false,
    tags: ['marketing', 'growth', 'content', 'demand gen'],
    description: 'A marketing manager resume built around growth metrics and campaign outcomes.',
    data: base(
      {
        name: 'Olivia Bennett',
        title: 'Marketing Manager',
        email: 'olivia.bennett@example.com',
        location: 'Chicago, IL',
        linkedin: 'linkedin.com/in/oliviabennett',
      },
      'Growth-minded marketing manager who turns content and campaigns into pipeline.',
      [
        {
          id: 1,
          title: 'Marketing Manager',
          company: 'Harbor Media',
          location: 'Chicago, IL',
          years: 'Feb 2019 - Present',
          description: [
            'Grew organic traffic 3.1x and MQLs 62% in 18 months.',
            'Launched a lifecycle program that lifted trial-to-paid 24%.',
          ],
        },
      ],
      [
        {
          id: 1,
          institution: 'University of Illinois',
          degree: 'B.S. Marketing',
          years: '2012 - 2016',
        },
      ],
      {
        technicalSkills: ['SEO', 'HubSpot', 'Google Analytics', 'Content Strategy', 'Paid Social'],
      }
    ),
  },
  {
    id: 'financial-analyst',
    name: 'Financial Analyst',
    role: 'Financial Analyst',
    category: 'professional',
    industry: 'Finance',
    experienceLevel: 'mid',
    atsScore: 5,
    recommendedTemplateId: 'finance-banking',
    hasPhoto: false,
    tags: ['finance', 'analysis', 'modeling', 'forecasting'],
    description: 'A finance resume tuned to conventions: conservative layout, quantified impact.',
    data: base(
      {
        name: 'Daniel Weber',
        title: 'Financial Analyst',
        email: 'daniel.weber@example.com',
        location: 'New York, NY',
        linkedin: 'linkedin.com/in/danielweber',
      },
      'Financial analyst delivering rigorous models and insights that guide capital decisions.',
      [
        {
          id: 1,
          title: 'Financial Analyst',
          company: 'Meridian Capital',
          location: 'New York, NY',
          years: 'Jul 2019 - Present',
          description: [
            'Built the three-statement model informing a $120M acquisition.',
            'Automated monthly reporting, saving 20 analyst-hours per cycle.',
          ],
        },
      ],
      [{ id: 1, institution: 'NYU Stern', degree: 'B.S. Finance', years: '2015 - 2019' }],
      {
        technicalSkills: [
          'Financial Modeling',
          'Excel',
          'SQL',
          'Valuation',
          'Forecasting',
          'Bloomberg',
        ],
        certificationsTraining: ['CFA Level II Candidate'],
      }
    ),
  },
  {
    id: 'registered-nurse',
    name: 'Registered Nurse',
    role: 'Registered Nurse',
    category: 'professional',
    industry: 'Healthcare',
    experienceLevel: 'mid',
    atsScore: 5,
    recommendedTemplateId: 'ats-classic',
    hasPhoto: false,
    tags: ['nursing', 'healthcare', 'patient care', 'clinical'],
    description:
      'A healthcare resume with clear credentials, clinical experience, and certifications.',
    data: base(
      {
        name: 'Grace Okafor',
        title: 'Registered Nurse, BSN',
        email: 'grace.okafor@example.com',
        location: 'Houston, TX',
        phone: '+1 555 0173',
      },
      'Compassionate RN with 6 years in acute care, committed to safety and evidence-based practice.',
      [
        {
          id: 1,
          title: 'Registered Nurse - Medical/Surgical',
          company: 'Houston General Hospital',
          location: 'Houston, TX',
          years: 'Aug 2018 - Present',
          description: [
            'Managed care for up to 6 acute patients per shift with a 98% satisfaction score.',
            'Precepted 12 new-graduate nurses; reduced onboarding time 20%.',
          ],
        },
      ],
      [
        {
          id: 1,
          institution: 'University of Texas Health',
          degree: 'B.S. Nursing (BSN)',
          years: '2014 - 2018',
        },
      ],
      {
        technicalSkills: ['Acute Care', 'IV Therapy', 'EHR (Epic)', 'Patient Education'],
        certificationsTraining: ['RN License (TX)', 'BLS', 'ACLS'],
      }
    ),
  },
  {
    id: 'student-graduate',
    name: 'Student / New Graduate',
    role: 'Computer Science Student',
    category: 'career-stage',
    industry: 'Software',
    experienceLevel: 'student',
    atsScore: 5,
    recommendedTemplateId: 'student-fresher',
    hasPhoto: false,
    tags: ['student', 'new grad', 'internship', 'entry level'],
    description: 'A new-graduate resume that leads with projects, coursework, and internships.',
    data: base(
      {
        name: 'Liam Patel',
        title: 'Computer Science Student',
        email: 'liam.patel@example.com',
        location: 'San Jose, CA',
        github: 'github.com/liampatel',
      },
      'Final-year CS student seeking a software engineering role; strong in algorithms and full-stack projects.',
      [
        {
          id: 1,
          title: 'Software Engineering Intern',
          company: 'Cloudpeak',
          location: 'San Jose, CA',
          years: 'Jun 2024 - Aug 2024',
          description: [
            'Shipped a dashboard feature used by 5K internal users.',
            'Wrote integration tests raising coverage from 55% to 82%.',
          ],
        },
      ],
      [
        {
          id: 1,
          institution: 'San Jose State University',
          degree: 'B.S. Computer Science (expected 2025)',
          years: '2021 - 2025',
          description: 'GPA 3.8; Dean\u2019s List',
        },
      ],
      {
        technicalSkills: ['Java', 'Python', 'React', 'Data Structures', 'Git'],
      },
      [
        {
          id: 1,
          name: 'StudyBuddy',
          role: 'Creator',
          years: '2023',
          github: 'github.com/liampatel/studybuddy',
          description: ['Full-stack study-group app with 1.2K users.'],
        },
      ]
    ),
  },
  {
    id: 'career-change',
    name: 'Career Changer',
    role: 'Aspiring UX Designer',
    category: 'career-stage',
    industry: 'Design',
    experienceLevel: 'entry',
    atsScore: 4,
    recommendedTemplateId: 'ats-modern',
    hasPhoto: false,
    tags: ['career change', 'transferable skills', 'transition'],
    description: 'A career-change resume that reframes transferable skills toward a new field.',
    data: base(
      {
        name: 'Nina Alvarez',
        title: 'Aspiring UX Designer',
        email: 'nina.alvarez@example.com',
        location: 'Portland, OR',
        website: 'ninaalvarez.design',
      },
      'Former project coordinator transitioning to UX design, pairing stakeholder skills with a UX certificate and a shipped portfolio.',
      [
        {
          id: 1,
          title: 'Project Coordinator',
          company: 'Cedar Group',
          location: 'Portland, OR',
          years: 'Jan 2019 - Present',
          description: [
            'Coordinated cross-functional launches - the same discovery + facilitation UX depends on.',
            'Ran user interviews for an internal tool, redesigning its flow to cut task time 30%.',
          ],
        },
      ],
      [
        {
          id: 1,
          institution: 'Google UX Design Certificate',
          degree: 'Professional Certificate',
          years: '2024',
        },
      ],
      {
        technicalSkills: ['User Research', 'Figma', 'Wireframing', 'Stakeholder Management'],
      }
    ),
  },
];

const BY_ID = new Map(RESUME_SAMPLES.map((s) => [s.id, s]));

export function getSampleById(id: string): ResumeSample | undefined {
  return BY_ID.get(id);
}

export interface SampleFilter {
  query?: string;
  category?: TemplateCategory | 'all';
  experienceLevel?: ExperienceLevel;
}

export function filterSamples(samples: ResumeSample[], filter: SampleFilter): ResumeSample[] {
  return samples.filter((s) => {
    if (filter.category && filter.category !== 'all' && s.category !== filter.category) {
      return false;
    }
    if (filter.experienceLevel && s.experienceLevel !== filter.experienceLevel) return false;
    if (filter.query) {
      const hay = [s.name, s.role, s.industry, s.description, ...s.tags].join(' ').toLowerCase();
      const ok = filter.query
        .toLowerCase()
        .split(/\s+/)
        .filter(Boolean)
        .every((t) => hay.includes(t));
      if (!ok) return false;
    }
    return true;
  });
}

/** Samples in the same category (excluding the given one) - for "related". */
export function relatedSamples(sample: ResumeSample, limit = 3): ResumeSample[] {
  return RESUME_SAMPLES.filter((s) => s.id !== sample.id && s.category === sample.category).slice(
    0,
    limit
  );
}
