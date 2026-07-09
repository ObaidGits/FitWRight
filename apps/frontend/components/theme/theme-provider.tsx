'use client';

/**
 * Atelier theme provider (Task 1.2)
 *
 * Lightweight, class-based light/dark theming with:
 * - default light (Req 1.4)
 * - persistence to localStorage (Req 1.3)
 * - FOUC / hydration-mismatch prevention via an inline pre-hydration script
 *   (see <ThemeScript/>) that sets the `.dark` class on <html> before paint
 *   (Req 1.9)
 *
 * Dark tokens are defined as `.dark .atelier` (see styles/atelier.css). The
 * resume-render engine is never wrapped in `.atelier`, so preview/PDF stay
 * light regardless of theme (engine isolation).
 */

import * as React from 'react';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'fitwright-theme';

interface ThemeContextValue {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
}

const ThemeContext = React.createContext<ThemeContextValue | undefined>(undefined);

function applyThemeClass(theme: Theme): void {
  const root = document.documentElement;
  root.classList.toggle('dark', theme === 'dark');
  root.style.colorScheme = theme;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = React.useState<Theme>('light');

  // Read the theme the pre-hydration script already applied, so React state
  // matches the DOM and there is no flash or mismatch.
  React.useEffect(() => {
    const initial: Theme = document.documentElement.classList.contains('dark') ? 'dark' : 'light';
    setThemeState(initial);
  }, []);

  const setTheme = React.useCallback((next: Theme) => {
    setThemeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* storage unavailable — non-fatal */
    }
    applyThemeClass(next);
  }, []);

  const toggleTheme = React.useCallback(() => {
    setTheme(document.documentElement.classList.contains('dark') ? 'light' : 'dark');
  }, [setTheme]);

  const value = React.useMemo(
    () => ({ theme, setTheme, toggleTheme }),
    [theme, setTheme, toggleTheme]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = React.useContext(ThemeContext);
  if (!ctx) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return ctx;
}

/**
 * Inline script placed in <head> before hydration. Reads the persisted theme
 * (defaulting to light) and applies the `.dark` class synchronously so the
 * first paint is already correct — no flash of the wrong theme.
 *
 * Rendered via dangerouslySetInnerHTML with a static, non-user string.
 */
export function ThemeScript() {
  const script = `(function(){try{var t=localStorage.getItem('${STORAGE_KEY}');var d=t==='dark';var r=document.documentElement;r.classList.toggle('dark',d);r.style.colorScheme=d?'dark':'light';}catch(e){}})();`;
  return <script dangerouslySetInnerHTML={{ __html: script }} suppressHydrationWarning />;
}
