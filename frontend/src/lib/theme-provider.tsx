// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { MotionConfig } from 'motion/react';
import {
  DEFAULT_THEME,
  FONT_STACKS,
  GLASS_BLUR_DEFAULT,
  GLASS_BLUR_MAX,
  GLASS_BLUR_MIN,
  MOTION_TIMING,
  RADIUS_VALUES,
  clamp,
  localThemeStorage,
  glassOpacityFor,
  mergeTheme,
  prefersDark,
  type Brand,
  type FontPref,
  type MotionPref,
  type RadiusPref,
  type SidebarPref,
  type Theme,
  type ThemeMode,
  type ThemeStorage,
  type Wallpaper,
} from './theme';
import { applyAccentVars, clearAccentVars, isHexColor, paintRootBackground, readableOn } from './color';
import { findPreset } from '@/config/presets';

interface ThemeContextValue {
  theme: Theme;
  /** Resolved light/dark, with `system` already folded in. */
  isDark: boolean;
  /** Patch the theme; persists through the configured storage adapter. */
  setTheme: (patch: Partial<Theme>) => void;
  setMode: (mode: ThemeMode, event?: { clientX: number; clientY: number }) => void;
  setAccent: (accent: string) => void;
  setBrand: (brand: Brand) => void;
  setWallpaper: (wallpaper: Wallpaper) => void;
  setGlass: (glass: boolean) => void;
  setGlassBlur: (blur: number) => void;
  setGradient: (gradient: boolean) => void;
  setRadius: (radius: RadiusPref) => void;
  setMotion: (motion: MotionPref) => void;
  setFont: (font: FontPref) => void;
  setSidebar: (sidebar: SidebarPref) => void;
  toggleSidebar: () => void;
  toggleNavItem: (id: string) => void;
  applyPreset: (id: string) => void;
  reset: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error('useTheme must be used inside <ThemeProvider>');
  return value;
}

/**
 * Theme provider: owns theme state, persists it through the injected adapter,
 * and keeps the document in sync (class, data attributes, accent variables,
 * page title, base-plate colour).
 *
 * Everything visual is expressed as a CSS custom property or a `data-*`
 * attribute on <html>, so styles react through the cascade rather than through
 * React re-renders — that keeps theme switches instant even mid-animation.
 */
export function ThemeProvider({
  children,
  storage = localThemeStorage,
  initialTheme,
}: {
  children: ReactNode;
  storage?: ThemeStorage;
  initialTheme?: Theme;
}) {
  const [theme, setThemeState] = useState<Theme>(() => initialTheme ?? storage.load() ?? DEFAULT_THEME);
  const [systemDark, setSystemDark] = useState(prefersDark);

  // Track the OS setting only while the mode is `system`.
  useEffect(() => {
    if (typeof window === 'undefined' || theme.ui?.mode !== 'system') return;
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    // Older WebViews only implement the deprecated addListener API; calling a
    // missing addEventListener here would take the whole shell down.
    if (typeof media.addEventListener === 'function') {
      media.addEventListener('change', handler);
      return () => media.removeEventListener('change', handler);
    }
    media.addListener(handler);
    return () => media.removeListener(handler);
  }, [theme.ui?.mode]);

  const mode = theme.ui?.mode ?? 'system';
  const isDark = mode === 'system' ? systemDark : mode === 'dark';

  const setTheme = useCallback(
    (patch: Partial<Theme>) => {
      setThemeState((current) => {
        const next = mergeTheme(current, patch);
        storage.save(next);
        return next;
      });
    },
    [storage],
  );

  const setMode = useCallback(
    (nextMode: ThemeMode, event?: { clientX: number; clientY: number }) => {
      const nextDark = nextMode === 'system' ? prefersDark() : nextMode === 'dark';
      const apply = () => {
        const root = document.documentElement;
        root.classList.toggle('dark', nextDark);
        root.dataset.theme = nextDark ? 'dark' : 'light';
        root.style.colorScheme = nextDark ? 'dark' : 'light';
      };
      const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown };
      if (event && doc.startViewTransition) {
        // Circular reveal from the click point.
        document.documentElement.style.setProperty('--vt-x', `${event.clientX}px`);
        document.documentElement.style.setProperty('--vt-y', `${event.clientY}px`);
        doc.startViewTransition(apply);
      } else {
        apply();
      }
      setTheme({ ui: { mode: nextMode } });
    },
    [setTheme],
  );

  const setAccent = useCallback((accent: string) => setTheme({ ui: { primary: accent } }), [setTheme]);
  const setBrand = useCallback((brand: Brand) => setTheme({ brand }), [setTheme]);
  const setGlass = useCallback((glass: boolean) => setTheme({ ui: { glass } }), [setTheme]);
  const setGlassBlur = useCallback(
    (blur: number) => setTheme({ ui: { glassBlur: clamp(blur, GLASS_BLUR_MIN, GLASS_BLUR_MAX) } }),
    [setTheme],
  );
  const setGradient = useCallback((gradient: boolean) => setTheme({ ui: { gradient } }), [setTheme]);
  const setRadius = useCallback((radius: RadiusPref) => setTheme({ ui: { radius } }), [setTheme]);
  const setMotion = useCallback((motion: MotionPref) => setTheme({ ui: { motion } }), [setTheme]);
  const setFont = useCallback((font: FontPref) => setTheme({ ui: { font } }), [setTheme]);
  const setSidebar = useCallback((sidebar: SidebarPref) => setTheme({ ui: { sidebar } }), [setTheme]);

  const toggleSidebar = useCallback(
    () =>
      setThemeState((current) => {
        const next = mergeTheme(current, {
          ui: { sidebar: current.ui?.sidebar === 'collapsed' ? 'expanded' : 'collapsed' },
        });
        storage.save(next);
        return next;
      }),
    [storage],
  );

  const setWallpaper = useCallback(
    (wallpaper: Wallpaper) =>
      setTheme({
        ui: {
          wallpaper: {
            ...wallpaper,
            blur: wallpaper.blur === undefined ? undefined : clamp(wallpaper.blur, 0, 18),
            dim: wallpaper.dim === undefined ? undefined : clamp(wallpaper.dim, 0, 100),
          },
        },
      }),
    [setTheme],
  );

  const toggleNavItem = useCallback(
    (id: string) =>
      setThemeState((current) => {
        const hidden = current.hiddenNav ?? [];
        const nextHiddenNav = hidden.includes(id) ? hidden.filter((item) => item !== id) : [...hidden, id];
        const next = { ...current, hiddenNav: nextHiddenNav };
        storage.save(next);
        return next;
      }),
    [storage],
  );

  const applyPreset = useCallback(
    (id: string) => {
      const preset = findPreset(id);
      if (!preset) return;
      setThemeState((current) => {
        // Presets intentionally keep the user's wallpaper and hidden nav: a skin
        // should not wipe personalisation that is orthogonal to it.
        const next = mergeTheme(current, {
          ...preset.theme,
          preset: id,
          ui: { ...preset.theme.ui, wallpaper: current.ui?.wallpaper },
        });
        storage.save(next);
        return next;
      });
    },
    [storage],
  );

  const reset = useCallback(() => {
    setThemeState(DEFAULT_THEME);
    storage.save(DEFAULT_THEME);
  }, [storage]);

  /* --- document side effects ------------------------------------------- */

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('dark', isDark);
    root.dataset.theme = isDark ? 'dark' : 'light';
    root.style.colorScheme = isDark ? 'dark' : 'light';
    // Keep the root painted so no host default (black) shows through the
    // transparent body while a wallpaper is enabled.
    paintRootBackground(root, isDark ? '#0b0f14' : '#ffffff');
  }, [isDark]);

  const accent = theme.ui?.primary ?? '';
  useEffect(() => {
    const root = document.documentElement;
    if (isHexColor(accent)) {
      root.style.setProperty('--primary', accent);
      root.style.setProperty('--ring', accent);
      root.style.setProperty('--primary-foreground', readableOn(accent));
      applyAccentVars(root, accent);
    } else {
      root.style.removeProperty('--primary');
      root.style.removeProperty('--ring');
      root.style.removeProperty('--primary-foreground');
      clearAccentVars(root);
    }
  }, [accent]);

  const radius = theme.ui?.radius ?? 'soft';
  useEffect(() => {
    document.documentElement.style.setProperty('--radius', RADIUS_VALUES[radius]);
    document.documentElement.dataset.radius = radius;
  }, [radius]);

  const font = theme.ui?.font ?? 'sans';
  useEffect(() => {
    document.documentElement.style.setProperty('--font-ui', FONT_STACKS[font]);
    document.documentElement.dataset.font = font;
  }, [font]);

  const motion = theme.ui?.motion ?? 'full';
  useEffect(() => {
    document.documentElement.dataset.motion = motion;
  }, [motion]);

  const gradient = theme.ui?.gradient === true;
  useEffect(() => {
    document.documentElement.dataset.gradient = gradient ? 'on' : 'off';
  }, [gradient]);

  const wallpaperUrl = theme.ui?.wallpaper?.url;
  useEffect(() => {
    const root = document.documentElement;
    if (wallpaperUrl) root.dataset.wallpaper = 'on';
    else delete root.dataset.wallpaper;
  }, [wallpaperUrl]);

  const glass = theme.ui?.glass !== false;
  useEffect(() => {
    document.documentElement.dataset.glass = glass ? 'on' : 'off';
  }, [glass]);

  /*
   * Glass blur + the opacity derived from it.
   *
   * Both are published as custom properties rather than baked into the
   * stylesheet, so the appearance slider can change them live without shipping
   * a Tailwind class per possible value. `glassOpacityFor` keeps the panel
   * legible as the blur gets heavier.
   */
  const glassBlur = clamp(
    theme.ui?.glassBlur ?? GLASS_BLUR_DEFAULT,
    GLASS_BLUR_MIN,
    GLASS_BLUR_MAX,
  );
  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--glass-blur', `${glassBlur}px`);
    root.style.setProperty('--glass-opacity', String(glassOpacityFor(glassBlur)));
    // Dark mode panels need slightly more body: dark translucent surfaces over a
    // bright photo wash out faster than light ones over a dark photo.
    root.style.setProperty('--glass-opacity-dark', String(Math.min(0.97, glassOpacityFor(glassBlur) - 0.02)));
  }, [glassBlur]);

  const presetId = theme.preset;
  useEffect(() => {
    if (presetId) document.documentElement.dataset.preset = presetId;
    else delete document.documentElement.dataset.preset;
  }, [presetId]);

  useEffect(() => {
    const title = theme.brand?.title || DEFAULT_THEME.brand?.title || 'Console';
    const subtitle = (theme.brand?.subtitle || '').trim();
    document.title = subtitle ? `${title} · ${subtitle}` : title;
  }, [theme.brand?.title, theme.brand?.subtitle]);

  /* --- context value ---------------------------------------------------- */

  const value = useMemo<ThemeContextValue>(
    () => ({
      theme,
      isDark,
      setTheme,
      setMode,
      setAccent,
      setBrand,
      setWallpaper,
      setGlass,
      setGlassBlur,
      setGradient,
      setRadius,
      setMotion,
      setFont,
      setSidebar,
      toggleSidebar,
      toggleNavItem,
      applyPreset,
      reset,
    }),
    [
      theme,
      isDark,
      setTheme,
      setMode,
      setAccent,
      setBrand,
      setWallpaper,
      setGlass,
      setGlassBlur,
      setGradient,
      setRadius,
      setMotion,
      setFont,
      setSidebar,
      toggleSidebar,
      toggleNavItem,
      applyPreset,
      reset,
    ],
  );

  const timing = MOTION_TIMING[motion];

  return (
    <ThemeContext.Provider value={value}>
      {/*
        MotionConfig makes the budget global: framer-motion reads `transition`
        from here when a component does not pass its own, and `reducedMotion`
        short-circuits every animated component when motion is off.
      */}
      <MotionConfig
        transition={{ duration: timing.duration, ease: [0.16, 1, 0.3, 1] }}
        reducedMotion={timing.enabled ? 'never' : 'always'}
      >
        {children}
      </MotionConfig>
    </ThemeContext.Provider>
  );
}
