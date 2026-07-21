/**
 * Degradation levels (P4 R6.4, R9) - deterministic capability tiers.
 *
 * Every failure maps to a *named* level with a fixed capability set, so behavior
 * is predictable and communicable to the user via the DegradationBanner. The
 * level is a pure function of the observed signals.
 */

export type DegradationLevel =
  | 'full'
  | 'degraded-ai'
  | 'offline-read-write'
  | 'read-only'
  | 'safe-mode';

export interface Capabilities {
  read: boolean;
  editResume: boolean;
  /** Server autosave (vs local-draft-only). */
  serverSave: boolean;
  ai: boolean;
  streaming: boolean;
  /** Import from URL, server-rendered export, etc. */
  networkFeatures: boolean;
}

export interface DegradationSignals {
  /** Real reachability probe result (not navigator.onLine). */
  backendReachable: boolean;
  /** AI available (backend reachable AND provider configured). */
  aiAvailable: boolean;
  /** Streaming flag + provider capability. */
  streamingAvailable: boolean;
  /** Local durable storage usable (not private-mode/quota-blocked). */
  storageOk: boolean;
  /** Client/server API version mismatch detected (deploy skew). */
  apiVersionSkew: boolean;
}

const CAPABILITIES: Record<DegradationLevel, Capabilities> = {
  full: {
    read: true,
    editResume: true,
    serverSave: true,
    ai: true,
    streaming: true,
    networkFeatures: true,
  },
  'degraded-ai': {
    read: true,
    editResume: true,
    serverSave: true,
    ai: false,
    streaming: false,
    networkFeatures: true,
  },
  'offline-read-write': {
    read: true,
    editResume: true,
    serverSave: false, // queued to outbox
    ai: false,
    streaming: false,
    networkFeatures: false,
  },
  'read-only': {
    read: true,
    editResume: false,
    serverSave: false,
    ai: false,
    streaming: false,
    networkFeatures: false,
  },
  'safe-mode': {
    // API skew / integrity risk: read + preserve the local draft, block writes
    // the server might misinterpret, prompt reload.
    read: true,
    editResume: true,
    serverSave: false,
    ai: false,
    streaming: false,
    networkFeatures: false,
  },
};

export function capabilitiesFor(level: DegradationLevel): Capabilities {
  return CAPABILITIES[level];
}

/** Compute the current degradation level from observed signals (deterministic). */
export function computeDegradation(signals: DegradationSignals): DegradationLevel {
  // API version skew is the most severe operational hazard: enter Safe-Mode so
  // we never send writes an incompatible server could misinterpret (R9.8).
  if (signals.apiVersionSkew) return 'safe-mode';
  if (!signals.backendReachable) {
    // Offline: if local storage works we can read + edit + queue; otherwise the
    // safety net is gone -> read-only to avoid losing edits we can't persist.
    return signals.storageOk ? 'offline-read-write' : 'read-only';
  }
  if (!signals.aiAvailable) return 'degraded-ai';
  return 'full';
}

/** Human-facing summary for the DegradationBanner (SR + text, R6.5). */
export function describeDegradation(level: DegradationLevel): {
  label: string;
  message: string;
} {
  switch (level) {
    case 'full':
      return { label: 'All features available', message: '' };
    case 'degraded-ai':
      return {
        label: 'AI unavailable',
        message: 'AI generation is temporarily unavailable. Editing and saving work normally.',
      };
    case 'offline-read-write':
      return {
        label: 'Offline',
        message:
          'You are offline. You can keep reading and editing; changes are saved locally and will sync when you reconnect. AI and imports need a connection.',
      };
    case 'read-only':
      return {
        label: 'Read-only',
        message:
          'Local storage is unavailable, so editing is disabled to avoid losing changes. Reading still works.',
      };
    case 'safe-mode':
      return {
        label: 'Safe mode - reload needed',
        message:
          'A new version is available. Your work is saved locally. Reload when ready to continue saving.',
      };
  }
}
