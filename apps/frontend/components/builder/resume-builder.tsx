'use client';

import React, { useState, useEffect, Suspense, useCallback, useMemo } from 'react';
import Image from 'next/image';
import { useSearchParams, useRouter } from 'next/navigation';
import { type ResumeData } from '@/components/dashboard/resume-component';
import { ResumeForm } from './resume-form';
import { FormattingControls } from './formatting-controls';
import { PhotoControls } from './photo-controls';
import { CoverLetterEditor } from './cover-letter-editor';
import { OutreachEditor } from './outreach-editor';
import { CoverLetterPreview } from './cover-letter-preview';
import { OutreachPreview } from './outreach-preview';
import { GeneratePrompt } from './generate-prompt';
import { InterviewPrepView } from './interview-prep-view';
import { Button } from '@/components/atelier/button';
import { TabStrip } from '@/components/atelier/tab-strip';
import { ConfirmDialog, type ConfirmDialogProps } from '@/components/atelier/confirm-dialog';
import {
  Download,
  Save,
  AlertTriangle,
  ArrowLeft,
  RotateCcw,
  Copy,
  Check,
  Sparkles,
  Loader2,
} from 'lucide-react';
import {
  useResumePreview,
  type InterviewPrepData,
} from '@/components/common/resume_previewer_context';
import { PaginatedPreview } from '@/components/preview';
import {
  downloadResumePdf,
  downloadCoverLetterPdf,
  getResumePdfUrl,
  getCoverLetterPdfUrl,
  fetchResume,
  updateResume,
  updateCoverLetter,
  updateOutreachMessage,
  generateCoverLetter,
  generateOutreachMessage,
  generateInterviewPrep,
  fetchJobDescription,
  buildResumeStreamTransport,
} from '@/lib/api/resume';
import { StreamController } from '@/lib/resilience/stream-client';
import { JDComparisonView } from './jd-comparison-view';
import { RegenerateWizard } from './regenerate-wizard';
import { useRegenerateWizard } from '@/hooks/use-regenerate-wizard';
import { useTranslations } from '@/lib/i18n';
import { type TemplateSettings, DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';
import { withLocalizedDefaultSections } from '@/lib/utils/section-helpers';
import { useLanguage } from '@/lib/context/language-context';
import { useSession } from '@/lib/context/session';
import { useResilienceFlags } from '@/lib/hooks/use-resilience-flags';
import { useAutosave } from '@/lib/hooks/use-autosave';
import { useRecovery } from '@/lib/hooks/use-recovery';
import { SaveStatusChip } from '@/components/resilience/save-status-chip';
import { ConflictDialog } from '@/components/resilience/conflict-dialog';
import { RecoveryBanner } from '@/components/resilience/recovery-banner';
import { RecoveryCenter } from '@/components/resilience/recovery-center';
import { buildResumeFilename, downloadBlobAsFile, openUrlInNewTab } from '@/lib/utils/download';
import type { RegenerateItemInput } from '@/lib/api/enrichment';

type TabId = 'resume' | 'cover-letter' | 'outreach' | 'interview-prep' | 'jd-match';
type JobContextStatus = 'idle' | 'loading' | 'available' | 'missing';

const STORAGE_KEY = 'resume_builder_draft';
const SETTINGS_STORAGE_KEY = 'resume_builder_settings';
const TAB_IDS: TabId[] = ['resume', 'cover-letter', 'outreach', 'interview-prep', 'jd-match'];

type Translate = (key: string, params?: Record<string, string | number>) => string;

const getTabFromSearchParams = (searchParams: Pick<URLSearchParams, 'get'>): TabId => {
  const tab = searchParams.get('tab');
  return TAB_IDS.includes(tab as TabId) ? (tab as TabId) : 'resume';
};

const buildInitialData = (t: Translate): ResumeData => ({
  personalInfo: {
    name: t('builder.personalInfoForm.placeholders.name'),
    title: t('builder.personalInfoForm.placeholders.title'),
    email: t('builder.personalInfoForm.placeholders.email'),
    phone: t('builder.personalInfoForm.placeholders.phone'),
    location: t('builder.personalInfoForm.placeholders.location'),
    website: t('builder.personalInfoForm.placeholders.website'),
    linkedin: t('builder.personalInfoForm.placeholders.linkedin'),
    github: t('builder.personalInfoForm.placeholders.github'),
  },
  summary: t('builder.placeholders.summary'),
  workExperience: [],
  education: [],
  personalProjects: [],
  additional: {
    technicalSkills: [],
    languages: [],
    certificationsTraining: [],
    awards: [],
  },
});

const ResumeBuilderContent = () => {
  const { t } = useTranslations();
  const { uiLanguage, contentLanguage } = useLanguage();
  const [notificationDialog, setNotificationDialog] = useState<{
    title: string;
    description: string;
    variant: NonNullable<ConfirmDialogProps['variant']>;
  } | null>(null);

  const showNotification = useCallback(
    (
      description: string,
      variant: NonNullable<ConfirmDialogProps['variant']> = 'default',
      title?: string
    ) => {
      const fallbackTitle = variant === 'success' ? t('common.success') : t('common.error');
      setNotificationDialog({
        title: title ?? fallbackTitle,
        description,
        variant,
      });
    },
    [t]
  );

  const initialData = useMemo(() => buildInitialData(t), [t]);
  const [resumeData, setResumeData] = useState<ResumeData>(() => initialData);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [lastSavedData, setLastSavedData] = useState<ResumeData>(() => initialData);
  const [isSaving, setIsSaving] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [, setLoadingState] = useState<'idle' | 'loading' | 'loaded' | 'error'>('idle');
  const [templateSettings, setTemplateSettings] =
    useState<TemplateSettings>(DEFAULT_TEMPLATE_SETTINGS);
  const { improvedData } = useResumePreview();
  const improvedPreview = improvedData?.data?.resume_preview;
  const improvedCoverLetter = improvedData?.data?.cover_letter;
  const improvedOutreach = improvedData?.data?.outreach_message;
  const improvedInterviewPrep = improvedData?.data?.interview_prep ?? null;
  const searchParams = useSearchParams();
  const router = useRouter();
  const resumeId = searchParams.get('id');

  useEffect(() => {
    if (resumeId || hasUnsavedChanges || improvedPreview) {
      return;
    }
    const savedDraft = localStorage.getItem(STORAGE_KEY);
    if (savedDraft) {
      return;
    }
    setResumeData(initialData);
    setLastSavedData(initialData);
  }, [initialData, resumeId, hasUnsavedChanges, improvedPreview]);

  // Tab state
  const [activeTab, setActiveTab] = useState<TabId>(() => getTabFromSearchParams(searchParams));

  useEffect(() => {
    setActiveTab(getTabFromSearchParams(searchParams));
  }, [searchParams]);

  // Cover letter & outreach state
  const [coverLetter, setCoverLetter] = useState('');
  const [outreachMessage, setOutreachMessage] = useState('');
  const [interviewPrep, setInterviewPrep] = useState<InterviewPrepData | null>(null);
  const [isCoverLetterSaving, setIsCoverLetterSaving] = useState(false);
  const [isOutreachSaving, setIsOutreachSaving] = useState(false);
  const [isCopied, setIsCopied] = useState(false);
  const [resumeTitle, setResumeTitle] = useState<string | null>(null);

  // On-demand generation state
  const [isTailoredResume, setIsTailoredResume] = useState(false);
  const [isGeneratingCoverLetter, setIsGeneratingCoverLetter] = useState(false);
  const [isGeneratingOutreach, setIsGeneratingOutreach] = useState(false);
  const [isGeneratingInterviewPrep, setIsGeneratingInterviewPrep] = useState(false);
  const [interviewPrepError, setInterviewPrepError] = useState<string | null>(null);
  const [showRegenerateDialog, setShowRegenerateDialog] = useState<
    'cover-letter' | 'outreach' | 'interview-prep' | null
  >(null);

  // JD comparison state
  const [jobDescription, setJobDescription] = useState<string | null>(null);
  const [jobContextStatus, setJobContextStatus] = useState<JobContextStatus>('idle');

  // ---------------------------------------------------------------------
  // P4 Resilience: durable autosave (version CAS + local draft + recovery +
  // conflict + multi-tab). Falls back to local-draft-only when the flag is off.
  // ---------------------------------------------------------------------
  const { user } = useSession();
  const { flags } = useResilienceFlags();
  const autosave = useAutosave<ResumeData & Record<string, unknown>>({
    resumeId: resumeId || '',
    userId: user?.id || '',
    initialVersion: null,
    enabled: flags.advanced_autosave && Boolean(resumeId),
    onServerData: (data) => {
      const d = data as { processed_resume?: ResumeData } | null;
      if (d?.processed_resume) {
        setResumeData(d.processed_resume);
        setLastSavedData(d.processed_resume);
        setHasUnsavedChanges(false);
      }
    },
  });
  const recovery = useRecovery(user?.id || '', { retrySync: autosave.retrySync });
  const [showRecoveryCenter, setShowRecoveryCenter] = useState(false);
  // Keep the recovery surface fresh when quarantine/outbox state changes.
  useEffect(() => {
    void recovery.refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autosave.quarantined, autosave.pendingOutbox]);

  // AI Regenerate wizard
  const regenerateWizard = useRegenerateWizard({
    resumeId: resumeId || '',
    outputLanguage: contentLanguage,
    onSuccess: async () => {
      // Reload resume data after applying changes
      if (!resumeId) {
        return;
      }

      try {
        const data = await fetchResume(resumeId);
        // Update resume title for downloads
        setResumeTitle(data.title ?? null);
        if (data.processed_resume) {
          setResumeData(data.processed_resume as ResumeData);
          setLastSavedData(data.processed_resume as ResumeData);
          setHasUnsavedChanges(false);
        }
      } catch (error) {
        console.error('Failed to reload resume after applying regenerated changes:', error);
        showNotification(t('builder.alerts.reloadFailed'), 'danger');
        throw error;
      }
    },
    onError: (errorMessage) => {
      console.error('Error during regeneration or applying regenerated changes:', errorMessage);

      if (/network|fetch/i.test(errorMessage) || errorMessage.includes('Failed to fetch')) {
        showNotification(t('builder.regenerate.errors.networkError'), 'danger');
        return;
      }

      if (/resume content changed|uniquely matched|please regenerate/i.test(errorMessage)) {
        showNotification(t('builder.regenerate.errors.resumeChanged'), 'danger');
        return;
      }

      if (/generate/i.test(errorMessage)) {
        showNotification(t('builder.regenerate.errors.generationFailed'), 'danger');
        return;
      }

      showNotification(t('builder.regenerate.errors.applyFailed'), 'danger');
    },
  });

  // Build regenerate items from resume data
  const experienceItemsForRegenerate: RegenerateItemInput[] = useMemo(() => {
    return (resumeData.workExperience || []).map((exp, idx) => ({
      item_id: `exp_${idx}`,
      item_type: 'experience' as const,
      title: exp.title ?? '',
      subtitle: exp.company || undefined,
      current_content: Array.isArray(exp.description) ? exp.description : [],
    }));
  }, [resumeData.workExperience]);

  const projectItemsForRegenerate: RegenerateItemInput[] = useMemo(() => {
    return (resumeData.personalProjects || []).map((proj, idx) => ({
      item_id: `proj_${idx}`,
      item_type: 'project' as const,
      title: proj.name ?? '',
      subtitle: proj.role || undefined,
      current_content: Array.isArray(proj.description) ? proj.description : [],
    }));
  }, [resumeData.personalProjects]);

  const skillsItemForRegenerate: RegenerateItemInput | null = useMemo(() => {
    const skills = resumeData.additional?.technicalSkills;
    if (skills && skills.length > 0) {
      return {
        item_id: 'skills',
        item_type: 'skills' as const,
        title: t('builder.regenerate.selectDialog.skills'),
        current_content: skills,
      };
    }
    return null;
  }, [resumeData.additional?.technicalSkills, t]);

  const localizedResumeDataForPreview = useMemo(
    () => withLocalizedDefaultSections(resumeData, t),
    [resumeData, t]
  );

  // Load template settings from localStorage on mount
  useEffect(() => {
    const savedSettings = localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (savedSettings) {
      try {
        const parsed = JSON.parse(savedSettings);
        setTemplateSettings({
          ...DEFAULT_TEMPLATE_SETTINGS,
          ...parsed,
          margins: { ...DEFAULT_TEMPLATE_SETTINGS.margins, ...parsed.margins },
          spacing: { ...DEFAULT_TEMPLATE_SETTINGS.spacing, ...parsed.spacing },
          fontSize: { ...DEFAULT_TEMPLATE_SETTINGS.fontSize, ...parsed.fontSize },
        });
      } catch {
        // Use defaults
      }
    }
  }, []);

  // Save template settings to localStorage when they change
  useEffect(() => {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(templateSettings));
  }, [templateSettings]);

  // Warn user before leaving with unsaved changes
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (hasUnsavedChanges) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasUnsavedChanges]);

  useEffect(() => {
    const loadResumeData = async () => {
      setLoadingState('loading');

      // Priority 1: Fetch from API if ID is in URL (most reliable)
      if (resumeId) {
        try {
          const data = await fetchResume(resumeId);
          // Track if this is a tailored resume (has parent_id)
          setIsTailoredResume(Boolean(data.parent_id));
          // Store resume title for downloads
          setResumeTitle(data.title ?? null);
          // Load cover letter and outreach message if available
          if (data.cover_letter) {
            setCoverLetter(data.cover_letter);
          }
          if (data.outreach_message) {
            setOutreachMessage(data.outreach_message);
          }
          setInterviewPrep(data.interview_prep ?? null);
          setInterviewPrepError(null);
          // Seed the optimistic-concurrency base version + the synced content
          // (the common ancestor used for correct conflict diff/merge - P4 R3.1/3.2).
          autosave.setBaseVersion(
            data.version ?? null,
            (data.processed_resume ?? undefined) as
              | (ResumeData & Record<string, unknown>)
              | undefined
          );
          // Prefer processed_resume if available
          if (data.processed_resume) {
            setResumeData(data.processed_resume as ResumeData);
            setLastSavedData(data.processed_resume as ResumeData);
            setLoadingState('loaded');
            return;
          }
          // Fallback to parsing raw content
          if (data.raw_resume?.content) {
            try {
              const parsed = JSON.parse(data.raw_resume.content);
              setResumeData(parsed);
              setLastSavedData(parsed);
              setLoadingState('loaded');
              return;
            } catch {
              // Raw content is markdown, not JSON
            }
          }
        } catch (err) {
          console.error('Failed to load resume from API:', err);
        }
      }

      // Priority 2: Improved Data from Context (Tailor Flow)
      if (improvedPreview) {
        setIsTailoredResume(Boolean(improvedData?.data?.resume_id && improvedData.data.job_id));
        setResumeData(improvedPreview);
        setLastSavedData(improvedPreview);
        // Also load cover letter and outreach if present
        if (improvedCoverLetter) {
          setCoverLetter(improvedCoverLetter);
        }
        if (improvedOutreach) {
          setOutreachMessage(improvedOutreach);
        }
        setInterviewPrep(improvedInterviewPrep);
        setInterviewPrepError(null);
        // Persist to localStorage as backup
        localStorage.setItem(STORAGE_KEY, JSON.stringify(improvedPreview));
        setLoadingState('loaded');
        return;
      }

      // Priority 3: Restore from localStorage (browser refresh recovery)
      const savedDraft = localStorage.getItem(STORAGE_KEY);
      if (savedDraft) {
        try {
          const parsed = JSON.parse(savedDraft);
          setResumeData(parsed);
          setLastSavedData(parsed);
          setHasUnsavedChanges(true); // Mark as unsaved since it's a draft
          setLoadingState('loaded');
          return;
        } catch {
          localStorage.removeItem(STORAGE_KEY);
        }
      }

      // Fallback: Use initial data
      setLoadingState('loaded');
    };

    loadResumeData();
    // `autosave` is intentionally omitted from deps: including it would re-run
    // the loader on every autosave state change; we only call the stable
    // `autosave.setBaseVersion` callback here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    improvedData?.data?.job_id,
    improvedData?.data?.resume_id,
    improvedPreview,
    improvedCoverLetter,
    improvedOutreach,
    improvedInterviewPrep,
    resumeId,
  ]);

  // Fetch job description when we have a tailored resume
  useEffect(() => {
    let cancelled = false;

    const loadJobDescription = async () => {
      if (isTailoredResume && resumeId) {
        setJobDescription(null);
        setJobContextStatus('loading');
        try {
          const data = await fetchJobDescription(resumeId);
          if (!cancelled) {
            setJobDescription(data.content);
            setJobContextStatus('available');
          }
        } catch (err) {
          // JD might not be available for older resumes
          if (!cancelled) {
            console.warn('Could not fetch job description:', err);
            setJobDescription(null);
            setJobContextStatus('missing');
          }
        }
      } else {
        // Clear job description when switching to non-tailored resume
        setJobDescription(null);
        setJobContextStatus('idle');
      }
    };

    loadJobDescription();
    return () => {
      cancelled = true;
    };
  }, [isTailoredResume, resumeId]);

  const handleUpdate = useCallback(
    (newData: ResumeData) => {
      setResumeData(newData);
      setHasUnsavedChanges(true);
      // Legacy localStorage draft (kept as a belt-and-suspenders fallback).
      localStorage.setItem(STORAGE_KEY, JSON.stringify(newData));
      // P4: durable autosave (server + encrypted IndexedDB draft, debounced +
      // coalesced + version-CAS + retry). No-op for an unsaved (id-less) resume.
      autosave.update(newData as ResumeData & Record<string, unknown>);
    },
    [autosave]
  );

  const handleSettingsChange = useCallback((newSettings: TemplateSettings) => {
    setTemplateSettings(newSettings);
  }, []);

  const handleSave = async () => {
    if (!resumeId) {
      showNotification(t('builder.alerts.saveNotAvailable'), 'warning');
      return;
    }
    try {
      setIsSaving(true);
      const updated = await updateResume(resumeId, resumeData);
      const nextData = (updated.processed_resume || resumeData) as ResumeData;
      setResumeData(nextData);
      setLastSavedData(nextData);
      setHasUnsavedChanges(false);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(nextData));
    } catch (error) {
      console.error('Failed to save resume:', error);
      showNotification(t('builder.alerts.saveFailed'), 'danger');
    } finally {
      setIsSaving(false);
    }
  };

  const handleReset = () => {
    setResumeData(lastSavedData);
    setHasUnsavedChanges(false);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(lastSavedData));
  };

  const getCompanyFromTitle = (title: string | null | undefined): string | null => {
    if (!title) return null;
    const atIdx = title.lastIndexOf(' @ ');
    return atIdx !== -1 ? title.substring(atIdx + 3).trim() : null;
  };

  const handleDownload = async () => {
    if (!resumeId) {
      showNotification(t('builder.alerts.downloadNotAvailable'), 'warning');
      return;
    }
    try {
      setIsDownloading(true);
      const blob = await downloadResumePdf(resumeId, templateSettings, uiLanguage);
      const company = getCompanyFromTitle(resumeTitle);
      const userName = resumeData.personalInfo?.name?.trim() || null;
      const filename = buildResumeFilename(userName, company, resumeId, 'resume');
      downloadBlobAsFile(blob, filename);
      showNotification(t('builder.alerts.downloadSuccess'), 'success');
    } catch (error) {
      console.error('Failed to download resume:', error);
      if (error instanceof TypeError && error.message.includes('Failed to fetch')) {
        const fallbackUrl = getResumePdfUrl(resumeId, templateSettings, uiLanguage);
        const didOpen = openUrlInNewTab(fallbackUrl);
        if (!didOpen) {
          showNotification(t('common.popupBlocked', { url: fallbackUrl }), 'warning');
        }
        return;
      }
      let errorMessage = t('builder.alerts.downloadFailed');
      if (error instanceof Error && error.message) {
        errorMessage = `${t('builder.alerts.downloadFailed')}: ${error.message}`;
      }
      showNotification(errorMessage, 'danger');
    } finally {
      setIsDownloading(false);
    }
  };

  // Cover letter handlers
  const handleSaveCoverLetter = async () => {
    if (!resumeId) return;
    try {
      setIsCoverLetterSaving(true);
      await updateCoverLetter(resumeId, coverLetter);
      showNotification(t('builder.alerts.coverLetterSaveSuccess'), 'success');
    } catch (error) {
      console.error('Failed to save cover letter:', error);
      showNotification(t('builder.alerts.coverLetterSaveFailed'), 'danger');
    } finally {
      setIsCoverLetterSaving(false);
    }
  };

  const handleDownloadCoverLetter = async () => {
    if (!resumeId) {
      showNotification(t('builder.alerts.coverLetterDownloadRequiresResume'), 'warning');
      return;
    }
    if (!coverLetter) {
      showNotification(t('builder.alerts.coverLetterMissing'), 'warning');
      return;
    }
    try {
      setIsDownloading(true);
      const blob = await downloadCoverLetterPdf(resumeId, templateSettings.pageSize, uiLanguage);
      const company = getCompanyFromTitle(resumeTitle);
      const userName = resumeData.personalInfo?.name?.trim() || null;
      const filename = buildResumeFilename(userName, company, resumeId, 'cover-letter');
      downloadBlobAsFile(blob, filename);
    } catch (error) {
      console.error('Failed to download cover letter:', error);
      if (error instanceof TypeError && error.message.includes('Failed to fetch')) {
        const fallbackUrl = getCoverLetterPdfUrl(resumeId, templateSettings.pageSize, uiLanguage);
        const didOpen = openUrlInNewTab(fallbackUrl);
        if (!didOpen) {
          showNotification(t('common.popupBlocked', { url: fallbackUrl }), 'warning');
        }
        return;
      }
      const errorMessage = error instanceof Error ? error.message : t('common.unknown');
      showNotification(
        t('builder.alerts.coverLetterDownloadFailed', { error: errorMessage }),
        'danger'
      );
    } finally {
      setIsDownloading(false);
    }
  };

  // Outreach handlers
  const handleSaveOutreach = async () => {
    if (!resumeId) return;
    try {
      setIsOutreachSaving(true);
      await updateOutreachMessage(resumeId, outreachMessage);
      showNotification(t('builder.alerts.outreachSaveSuccess'), 'success');
    } catch (error) {
      console.error('Failed to save outreach message:', error);
      showNotification(t('builder.alerts.outreachSaveFailed'), 'danger');
    } finally {
      setIsOutreachSaving(false);
    }
  };

  const handleCopyOutreach = async () => {
    try {
      await navigator.clipboard.writeText(outreachMessage);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch (error) {
      console.error('Failed to copy:', error);
    }
  };

  // On-demand generation handlers
  // Active streaming controller (P4 R1) so an in-flight generation is cancellable.
  const streamCtrlRef = React.useRef<StreamController | null>(null);
  const [streamingActive, setStreamingActive] = useState(false);
  const cancelActiveStream = useCallback(() => {
    void streamCtrlRef.current?.cancel();
  }, []);

  /**
   * Generate progressively via SSE when STREAMING_AI is on (R1.1); the
   * StreamController transparently falls back to the non-stream path on any
   * error (R1.3). Tokens stream into the field as they arrive.
   */
  const runStreamedGeneration = useCallback(
    async (kind: 'cover-letter' | 'outreach', setField: (text: string) => void): Promise<void> => {
      if (!resumeId) return;
      const requestId =
        typeof crypto?.randomUUID === 'function'
          ? crypto.randomUUID()
          : `req-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const transport = buildResumeStreamTransport(resumeId, kind, requestId);
      const ctrl = new StreamController(transport, { onToken: (full) => setField(full) });
      streamCtrlRef.current = ctrl;
      setStreamingActive(true);
      try {
        const text = await ctrl.run();
        setField(text);
      } finally {
        streamCtrlRef.current = null;
        setStreamingActive(false);
      }
    },
    [resumeId]
  );

  // R2.3: AI generation requires a connection. Guard the action explicitly with
  // a clear message rather than letting the request fail opaquely offline.
  const ensureOnlineForAI = useCallback((): boolean => {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      showNotification(t('builder.alerts.aiRequiresConnection'), 'warning');
      return false;
    }
    return true;
  }, [showNotification, t]);

  const doGenerateCoverLetter = async () => {
    if (!resumeId) return;
    if (!ensureOnlineForAI()) return;
    setIsGeneratingCoverLetter(true);
    setShowRegenerateDialog(null);
    try {
      if (flags.streaming_ai) {
        await runStreamedGeneration('cover-letter', setCoverLetter);
      } else {
        const content = await generateCoverLetter(resumeId);
        setCoverLetter(content);
      }
    } catch (error) {
      console.error('Failed to generate cover letter:', error);
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      showNotification(
        t('builder.alerts.coverLetterGenerateFailed', { error: errorMessage }),
        'danger'
      );
    } finally {
      setIsGeneratingCoverLetter(false);
    }
  };

  const handleGenerateCoverLetter = () => {
    if (!resumeId) return;
    // If content exists, show confirmation dialog
    if (coverLetter) {
      setShowRegenerateDialog('cover-letter');
      return;
    }
    doGenerateCoverLetter();
  };

  const doGenerateOutreach = async () => {
    if (!resumeId) return;
    if (!ensureOnlineForAI()) return;
    setIsGeneratingOutreach(true);
    setShowRegenerateDialog(null);
    try {
      if (flags.streaming_ai) {
        await runStreamedGeneration('outreach', setOutreachMessage);
      } else {
        const content = await generateOutreachMessage(resumeId);
        setOutreachMessage(content);
      }
    } catch (error) {
      console.error('Failed to generate outreach message:', error);
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      showNotification(
        t('builder.alerts.outreachGenerateFailed', { error: errorMessage }),
        'danger'
      );
    } finally {
      setIsGeneratingOutreach(false);
    }
  };

  const handleGenerateOutreach = () => {
    if (!resumeId) return;
    // If content exists, show confirmation dialog
    if (outreachMessage) {
      setShowRegenerateDialog('outreach');
      return;
    }
    doGenerateOutreach();
  };

  const canGenerateInterviewPrep =
    Boolean(resumeId) && isTailoredResume && jobContextStatus === 'available';

  const interviewPrepUnavailableMessage = !resumeId
    ? t('interviewPrep.saveRequiredDescription')
    : jobContextStatus === 'loading'
      ? t('interviewPrep.loadingContextDescription')
      : jobContextStatus === 'missing'
        ? t('interviewPrep.missingContextDescription')
        : null;

  const doGenerateInterviewPrep = async () => {
    if (!canGenerateInterviewPrep || !resumeId) return;
    if (!ensureOnlineForAI()) return;
    setIsGeneratingInterviewPrep(true);
    setInterviewPrepError(null);
    setShowRegenerateDialog(null);
    try {
      const content = await generateInterviewPrep(resumeId);
      setInterviewPrep(content);
    } catch (error) {
      console.error('Failed to generate interview preparation:', error);
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      setInterviewPrepError(
        t('builder.alerts.interviewPrepGenerateFailed', { error: errorMessage })
      );
      showNotification(
        t('builder.alerts.interviewPrepGenerateFailed', { error: errorMessage }),
        'danger'
      );
    } finally {
      setIsGeneratingInterviewPrep(false);
    }
  };

  const handleGenerateInterviewPrep = () => {
    if (!canGenerateInterviewPrep) return;
    if (interviewPrep) {
      setShowRegenerateDialog('interview-prep');
      return;
    }
    doGenerateInterviewPrep();
  };

  const regenerateDialogContentTitle =
    showRegenerateDialog === 'cover-letter'
      ? t('coverLetter.title')
      : showRegenerateDialog === 'outreach'
        ? t('outreach.title')
        : t('interviewPrep.title');

  const regenerateDialogConfirmLabel =
    showRegenerateDialog === 'cover-letter'
      ? t('coverLetter.regenerate')
      : showRegenerateDialog === 'outreach'
        ? t('outreach.regenerate')
        : t('interviewPrep.regenerate');

  const handleConfirmRegenerate = () => {
    if (showRegenerateDialog === 'cover-letter') {
      doGenerateCoverLetter();
    } else if (showRegenerateDialog === 'outreach') {
      doGenerateOutreach();
    } else if (showRegenerateDialog === 'interview-prep') {
      doGenerateInterviewPrep();
    }
  };

  return (
    <div className="flex h-screen w-full items-center justify-center bg-[var(--background)] p-4 md:p-8">
      {/* P4 R6.5: announce streaming AI progress to screen readers via aria-live. */}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {streamingActive ? t('builder.alerts.aiStreaming') : ''}
      </div>
      {/* P4 Resilience: explicit conflict resolution (version CAS 409). */}
      {autosave.conflict && (
        <ConflictDialog
          mine={resumeData as unknown as Record<string, unknown>}
          latest={(autosave.conflict.currentData as Record<string, unknown>) ?? {}}
          base={(autosave.conflictBase as Record<string, unknown> | null) ?? undefined}
          currentVersion={autosave.conflict.currentVersion}
          onKeepMine={() =>
            void autosave.resolveKeepMine(resumeData as ResumeData & Record<string, unknown>)
          }
          onTakeLatest={autosave.resolveTakeLatest}
          onMerge={(merged) => {
            setResumeData(merged as unknown as ResumeData);
            void autosave.resolveMerge(merged as ResumeData & Record<string, unknown>);
          }}
          onDismiss={autosave.resolveTakeLatest}
        />
      )}
      {/* Main Container */}
      <div className="flex h-full w-full max-w-[90%] flex-col overflow-hidden rounded-[var(--radius-at-xl)] border border-[var(--border)] bg-[var(--background)] shadow-[var(--shadow-at-e3)] md:max-w-[95%] xl:max-w-[1800px]">
        {/* P4 Resilience: non-destructive crash/refresh recovery (R5.1). */}
        {autosave.recovery && (
          <RecoveryBanner
            savedAt={autosave.recovery.savedAt}
            onRestore={() => {
              const recovered = autosave.recovery?.payload as ResumeData | undefined;
              if (recovered) {
                setResumeData(recovered);
                setHasUnsavedChanges(true);
              }
              autosave.acceptRecovery();
            }}
            onDiscard={autosave.dismissRecovery}
          />
        )}
        {/* P4 R5.3/R5.5: corrupt draft or queued edits -> open the recovery center. */}
        {(autosave.quarantined || recovery.hasAnything) && (
          <div
            role="alert"
            className="flex flex-wrap items-center gap-2 bg-[var(--at-warning)]/15 px-4 py-2 text-xs font-medium text-[var(--at-warning)]"
          >
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>{t('builder.alerts.draftQuarantined')}</span>
            <button
              type="button"
              className="ml-auto underline underline-offset-2"
              onClick={() => {
                void recovery.refresh();
                setShowRecoveryCenter(true);
              }}
            >
              {t('builder.alerts.openRecoveryCenter')}
            </button>
          </div>
        )}
        {showRecoveryCenter && (
          <RecoveryCenter
            quarantine={recovery.quarantine}
            outbox={recovery.outbox}
            onExportQuarantine={recovery.exportQuarantine}
            onDiscardQuarantine={(id) => void recovery.discardQuarantine(id)}
            onDiscardOutbox={(id) => void recovery.discardOutbox(id)}
            onRetrySync={() => void autosave.retrySync().then(() => recovery.refresh())}
            onClose={() => setShowRecoveryCenter(false)}
          />
        )}
        {/* P4 R8.4: durable local storage is unavailable - warn (memory-only). */}
        {autosave.storageDegraded && (
          <div
            role="status"
            aria-live="polite"
            className="flex items-center gap-2 bg-[var(--at-warning)]/15 px-4 py-2 text-xs font-medium text-[var(--at-warning)]"
          >
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {t('builder.alerts.storageDegraded')}
          </div>
        )}
        {/* Header Section */}
        <div className="no-print border-b border-[var(--border)] bg-[var(--background)] p-6 md:p-8">
          {/* Top Row: Back button and Actions */}
          <div className="mb-6 flex flex-col items-start justify-between md:flex-row md:items-center">
            <div>
              <Button
                variant="link"
                onClick={() => router.push('/dashboard')}
                className="mb-2 -ml-1"
              >
                <ArrowLeft className="w-4 h-4" />
                {t('nav.backToDashboard')}
              </Button>
              <h1 className="text-3xl font-semibold leading-tight tracking-tight text-[var(--foreground)] md:text-5xl">
                {t('nav.builder')}
              </h1>
              <div className="mt-3 flex items-center gap-3">
                <p className="text-sm font-medium text-[var(--primary)]">
                  {resumeId ? t('builder.editMode') : t('builder.createAndPreview')}
                </p>
                {hasUnsavedChanges && (
                  <span className="flex items-center gap-1 rounded-[var(--radius-at-sm)] border border-[var(--at-warning)]/40 bg-[var(--at-warning)]/12 px-2 py-1 text-xs font-medium text-[var(--at-warning)]">
                    <AlertTriangle className="w-3 h-3" />
                    {t('builder.unsavedDraft')}
                  </span>
                )}
              </div>
            </div>

            <div className="flex gap-3 mt-4 md:mt-0">
              {/* Resume tab actions */}
              {activeTab === 'resume' && (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => regenerateWizard.startRegenerate()}
                    disabled={!resumeId}
                  >
                    <Sparkles className="w-4 h-4" />
                    {t('builder.regenerate.buttonLabel')}
                  </Button>
                  <Button
                    variant="warning"
                    size="sm"
                    onClick={handleReset}
                    disabled={!hasUnsavedChanges}
                  >
                    <RotateCcw className="w-4 h-4" />
                    {t('common.reset')}
                  </Button>
                  {flags.advanced_autosave && resumeId && (
                    <SaveStatusChip
                      status={autosave.status}
                      lastSavedAt={autosave.lastSavedAt}
                      isFollower={!autosave.isLeader}
                    />
                  )}
                  {streamingActive && (
                    <Button
                      variant="warning"
                      size="sm"
                      onClick={cancelActiveStream}
                      aria-label={t('common.cancel')}
                    >
                      {t('common.cancel')}
                    </Button>
                  )}
                  <Button size="sm" onClick={handleSave} disabled={!resumeId || isSaving}>
                    <Save className="w-4 h-4" />
                    {isSaving ? t('common.saving') : t('common.save')}
                  </Button>
                  <Button
                    variant="success"
                    size="sm"
                    onClick={handleDownload}
                    disabled={!resumeId || isDownloading}
                  >
                    <Download className="w-4 h-4" />
                    {isDownloading ? t('common.generating') : t('common.download')}
                  </Button>
                </>
              )}

              {/* Cover letter tab actions */}
              {activeTab === 'cover-letter' && coverLetter && (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleGenerateCoverLetter}
                    disabled={isGeneratingCoverLetter}
                  >
                    {isGeneratingCoverLetter ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Sparkles className="w-4 h-4" />
                    )}
                    {t('coverLetter.regenerate')}
                  </Button>
                  <Button
                    variant="success"
                    size="sm"
                    onClick={handleDownloadCoverLetter}
                    disabled={!resumeId || isDownloading}
                  >
                    <Download className="w-4 h-4" />
                    {isDownloading ? t('common.generating') : t('common.download')}
                  </Button>
                </>
              )}

              {/* Outreach tab actions */}
              {activeTab === 'outreach' && outreachMessage && (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleGenerateOutreach}
                    disabled={isGeneratingOutreach}
                  >
                    {isGeneratingOutreach ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Sparkles className="w-4 h-4" />
                    )}
                    {t('outreach.regenerate')}
                  </Button>
                  <Button variant="success" size="sm" onClick={handleCopyOutreach}>
                    {isCopied ? (
                      <>
                        <Check className="w-4 h-4" />
                        {t('outreach.copied')}
                      </>
                    ) : (
                      <>
                        <Copy className="w-4 h-4" />
                        {t('outreach.copyToClipboard')}
                      </>
                    )}
                  </Button>
                </>
              )}

              {/* Interview prep tab actions */}
              {activeTab === 'interview-prep' && interviewPrep && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleGenerateInterviewPrep}
                  disabled={!canGenerateInterviewPrep || isGeneratingInterviewPrep}
                >
                  {isGeneratingInterviewPrep ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Sparkles className="w-4 h-4" />
                  )}
                  {t('interviewPrep.regenerate')}
                </Button>
              )}
            </div>
          </div>
        </div>

        {/* Content Grid */}
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-px bg-[var(--border)] lg:grid-cols-2">
          {/* Left Panel: Editor */}
          <div className="no-print overflow-y-auto bg-[var(--background)] p-6 md:p-8">
            <div className="mx-auto max-w-3xl space-y-6">
              <div className="flex items-center gap-2 border-b border-[var(--border)] pb-2">
                <div className="h-3 w-3 rounded-full bg-[var(--primary)]"></div>
                <h2 className="text-lg font-semibold text-[var(--foreground)]">
                  {activeTab === 'resume' && t('builder.leftPanel.editorPanel')}
                  {activeTab === 'cover-letter' && t('builder.leftPanel.coverLetterEditor')}
                  {activeTab === 'outreach' && t('builder.leftPanel.outreachEditor')}
                  {activeTab === 'interview-prep' && t('builder.leftPanel.interviewPrep')}
                  {activeTab === 'jd-match' && t('builder.leftPanel.jdMatchAnalysis')}
                </h2>
              </div>

              {/* Resume Editor */}
              {activeTab === 'resume' && (
                <>
                  <FormattingControls settings={templateSettings} onChange={handleSettingsChange} />
                  <div className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-4">
                    <h3 className="mb-3 text-sm font-semibold text-[var(--foreground)]">Photo</h3>
                    <PhotoControls
                      template={templateSettings.template}
                      value={resumeData.personalInfo?.photo}
                      profileAvatarUrl={resumeData.personalInfo?.avatarUrl}
                      onChange={(photo) =>
                        handleUpdate({
                          ...resumeData,
                          personalInfo: { ...resumeData.personalInfo, photo },
                        })
                      }
                      onProfileAvatarChange={(url) =>
                        handleUpdate({
                          ...resumeData,
                          personalInfo: { ...resumeData.personalInfo, avatarUrl: url },
                        })
                      }
                      onError={(message) => showNotification(message, 'danger')}
                    />
                  </div>
                  <ResumeForm resumeData={resumeData} onUpdate={handleUpdate} />
                </>
              )}

              {/* Cover Letter Editor */}
              {activeTab === 'cover-letter' &&
                (coverLetter ? (
                  <CoverLetterEditor
                    content={coverLetter}
                    onChange={setCoverLetter}
                    onSave={handleSaveCoverLetter}
                    isSaving={isCoverLetterSaving}
                  />
                ) : (
                  <GeneratePrompt
                    type="cover-letter"
                    isGenerating={isGeneratingCoverLetter}
                    onGenerate={handleGenerateCoverLetter}
                    isTailoredResume={isTailoredResume}
                  />
                ))}

              {/* Outreach Editor */}
              {activeTab === 'outreach' &&
                (outreachMessage ? (
                  <OutreachEditor
                    content={outreachMessage}
                    onChange={setOutreachMessage}
                    onSave={handleSaveOutreach}
                    isSaving={isOutreachSaving}
                  />
                ) : (
                  <GeneratePrompt
                    type="outreach"
                    isGenerating={isGeneratingOutreach}
                    onGenerate={handleGenerateOutreach}
                    isTailoredResume={isTailoredResume}
                  />
                ))}

              {/* Interview Prep Read-Only View */}
              {activeTab === 'interview-prep' && (
                <InterviewPrepView
                  interviewPrep={interviewPrep}
                  isGenerating={isGeneratingInterviewPrep}
                  error={interviewPrepError}
                  onGenerate={handleGenerateInterviewPrep}
                  isTailoredResume={isTailoredResume}
                  canGenerate={canGenerateInterviewPrep}
                  unavailableMessage={interviewPrepUnavailableMessage}
                  className="p-0"
                />
              )}

              {/* JD Match Info Panel */}
              {activeTab === 'jd-match' && (
                <div className="space-y-4">
                  <div className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-4">
                    <h3 className="mb-2 text-sm font-semibold text-[var(--foreground)]">
                      {t('builder.jdMatch.aboutTitle')}
                    </h3>
                    <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
                      {t('builder.jdMatch.aboutDescription')}
                    </p>
                  </div>

                  <div className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-4">
                    <h3 className="mb-2 text-sm font-semibold text-[var(--foreground)]">
                      {t('builder.jdMatch.highlightedKeywordsTitle')}
                    </h3>
                    <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
                      {(() => {
                        const template = t(
                          'builder.jdMatch.highlightedKeywordsDescriptionTemplate'
                        );
                        const parts = template.split('__COLOR__');
                        if (parts.length < 2) return template;
                        return (
                          <>
                            {parts[0]}
                            <mark className="rounded-[var(--radius-at-sm)] bg-[var(--at-warning)]/25 px-1 text-[var(--foreground)]">
                              {t('builder.jdMatch.highlightColor')}
                            </mark>
                            {parts.slice(1).join('__COLOR__')}
                          </>
                        );
                      })()}
                    </p>
                  </div>

                  <div className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--card)] p-4">
                    <h3 className="mb-2 text-sm font-semibold text-[var(--foreground)]">
                      {t('builder.jdMatch.tipsTitle')}
                    </h3>
                    <ul className="list-inside list-disc space-y-1 text-sm text-[var(--muted-foreground)]">
                      <li>{t('builder.jdMatch.tips.items.addMissingKeywords')}</li>
                      <li>{t('builder.jdMatch.tips.items.focusTechnicalSkills')}</li>
                      <li>{t('builder.jdMatch.tips.items.matchActionVerbs')}</li>
                    </ul>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Right Panel: Preview with Tabs */}
          <div className="no-print flex flex-col overflow-hidden bg-[var(--secondary)]">
            {/* Tabs Header */}
            <div className="shrink-0 bg-[var(--secondary)] px-6 pt-3">
              <TabStrip
                aria-label={t('builder.leftPanel.jdMatchAnalysis')}
                tabs={[
                  { id: 'resume', label: t('builder.previewTabs.resume') },
                  {
                    id: 'cover-letter',
                    label: t('builder.previewTabs.coverLetter'),
                    disabled: !coverLetter,
                  },
                  {
                    id: 'outreach',
                    label: t('builder.previewTabs.outreach'),
                    disabled: !outreachMessage,
                  },
                  {
                    id: 'interview-prep',
                    label: t('builder.previewTabs.interviewPrep'),
                    disabled: !isTailoredResume,
                  },
                  {
                    id: 'jd-match',
                    label: t('builder.previewTabs.jdMatch'),
                    disabled: !jobDescription,
                  },
                ]}
                activeTab={activeTab}
                onTabChange={(id) => setActiveTab(id as TabId)}
                className="flex-wrap"
              />
            </div>

            {/* Preview Content */}
            <div className="flex-1 overflow-y-auto">
              {/* Resume Preview */}
              {activeTab === 'resume' && (
                <PaginatedPreview
                  resumeData={localizedResumeDataForPreview}
                  settings={templateSettings}
                />
              )}

              {/* Cover Letter Preview */}
              {activeTab === 'cover-letter' &&
                (coverLetter && resumeData.personalInfo ? (
                  <div className="p-6">
                    <CoverLetterPreview
                      content={coverLetter}
                      personalInfo={resumeData.personalInfo}
                      pageSize={templateSettings.pageSize}
                    />
                  </div>
                ) : (
                  <GeneratePrompt
                    type="cover-letter"
                    isGenerating={isGeneratingCoverLetter}
                    onGenerate={handleGenerateCoverLetter}
                    isTailoredResume={isTailoredResume}
                  />
                ))}

              {/* Outreach Preview */}
              {activeTab === 'outreach' &&
                (outreachMessage ? (
                  <div className="p-6">
                    <OutreachPreview content={outreachMessage} />
                  </div>
                ) : (
                  <GeneratePrompt
                    type="outreach"
                    isGenerating={isGeneratingOutreach}
                    onGenerate={handleGenerateOutreach}
                    isTailoredResume={isTailoredResume}
                  />
                ))}

              {/* Interview Prep Preview */}
              {activeTab === 'interview-prep' && (
                <InterviewPrepView
                  interviewPrep={interviewPrep}
                  isGenerating={isGeneratingInterviewPrep}
                  error={interviewPrepError}
                  onGenerate={handleGenerateInterviewPrep}
                  isTailoredResume={isTailoredResume}
                  canGenerate={canGenerateInterviewPrep}
                  unavailableMessage={interviewPrepUnavailableMessage}
                />
              )}

              {/* JD Match Comparison */}
              {activeTab === 'jd-match' && jobDescription && (
                <JDComparisonView jobDescription={jobDescription} resumeData={resumeData} />
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="no-print flex items-center justify-between border-t border-[var(--border)] bg-[var(--background)] p-4 text-xs text-[var(--muted-foreground)]">
          <span className="flex items-center gap-2 font-medium">
            <Image src="/logo.svg" alt="FitWright" width={20} height={20} className="w-5 h-5" />
            {t('builder.footer.moduleLabel')}
          </span>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <div className="h-2 w-2 rounded-full bg-[var(--at-success)]"></div>
              <span>
                {templateSettings.template === 'swiss-single' ||
                templateSettings.template === 'modern' ||
                templateSettings.template === 'latex' ||
                templateSettings.template === 'clean'
                  ? t('builder.footer.singleColumn')
                  : t('builder.footer.twoColumn')}
              </span>
            </div>
            <span className="text-[var(--border)]">|</span>
            <span>
              {templateSettings.pageSize === 'A4' ? 'A4' : t('builder.pageSize.usLetter')}
            </span>
          </div>
        </div>
      </div>

      {/* Regenerate Confirmation Dialog */}
      <ConfirmDialog
        open={showRegenerateDialog !== null}
        onOpenChange={(open) => !open && setShowRegenerateDialog(null)}
        title={t('builder.regenerateDialog.title', {
          title: regenerateDialogContentTitle,
        })}
        description={t('builder.regenerateDialog.description', {
          title: regenerateDialogContentTitle,
        })}
        confirmLabel={regenerateDialogConfirmLabel}
        cancelLabel={t('common.cancel')}
        variant="warning"
        onConfirm={handleConfirmRegenerate}
      />

      {/* Notification Dialog (replaces native alert()) */}
      <ConfirmDialog
        open={notificationDialog !== null}
        onOpenChange={(open) => !open && setNotificationDialog(null)}
        title={notificationDialog?.title ?? ''}
        description={notificationDialog?.description ?? ''}
        confirmLabel={t('common.ok')}
        showCancelButton={false}
        variant={notificationDialog?.variant ?? 'default'}
        onConfirm={() => setNotificationDialog(null)}
      />

      {/* AI Regenerate Wizard */}
      <RegenerateWizard
        step={regenerateWizard.step}
        onStepChange={regenerateWizard.setStep}
        experienceItems={experienceItemsForRegenerate}
        projectItems={projectItemsForRegenerate}
        skillsItem={skillsItemForRegenerate}
        selectedItems={regenerateWizard.selectedItems}
        onSelectionChange={regenerateWizard.setSelectedItems}
        instruction={regenerateWizard.instruction}
        onInstructionChange={regenerateWizard.setInstruction}
        regeneratedItems={regenerateWizard.regeneratedItems}
        regenerateErrors={regenerateWizard.regenerateErrors}
        isGenerating={regenerateWizard.isGenerating}
        isApplying={regenerateWizard.isApplying}
        error={regenerateWizard.error}
        onGenerate={regenerateWizard.generate}
        onAccept={regenerateWizard.acceptChanges}
        onReject={regenerateWizard.rejectAndRegenerate}
        onClose={regenerateWizard.reset}
      />
    </div>
  );
};

export const ResumeBuilder = () => {
  const { t } = useTranslations();
  return (
    <Suspense fallback={<div>{t('common.loading')}</div>}>
      <ResumeBuilderContent />
    </Suspense>
  );
};
