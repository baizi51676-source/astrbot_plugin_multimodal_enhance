// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

/**
 * Theme model + persistence.
 *
 * Deliberately storage-agnostic: the provider takes an optional `storage`
 * adapter, so the same engine works with localStorage (default), a REST
 * endpoint, or nothing at all (in-memory). That is what makes this a template
 * rather than an app — swap the adapter, keep the behaviour.
 */

export type ThemeMode = 'light' | 'dark' | 'system';

/** How much motion the UI is allowed to use. */
export type MotionPref = 'full' | 'subtle' | 'off';

/** Corner rounding, expressed as the `--radius` token. */
export type RadiusPref = 'sharp' | 'soft' | 'round';

/** Interface typeface family. */
export type FontPref = 'sans' | 'serif' | 'mono';

/** Sidebar presentation on desktop. */
export type SidebarPref = 'expanded' | 'collapsed';

export interface Wallpaper {
  /** Any CSS url() payload: an http(s) URL, a data URI, or a blob URL. */
  url?: string;
  /** Blur radius in px, clamped by the provider before it reaches CSS. */
  blur?: number;
  /** Darkening overlay, 0–100. */
  dim?: number;
}

export interface Brand {
  title?: string;
  subtitle?: string;
}

export interface ThemeUI {
  mode?: ThemeMode;
  /** Accent colour as hex. Empty / invalid falls back to the stylesheet value. */
  primary?: string;
  wallpaper?: Wallpaper;
  /** Translucent panels, only visible when a wallpaper is set. */
  glass?: boolean;
  /**
   * Glass blur radius in px. Also sets how opaque the panel is: a heavier blur
   * needs more opacity behind it or the image smears into unreadable text, so
   * the provider derives both from this single number.
   */
  glassBlur?: number;
  /** Paint the accent as a two-stop gradient (buttons, active pill, brand mark). */
  gradient?: boolean;
  radius?: RadiusPref;
  motion?: MotionPref;
  font?: FontPref;
  sidebar?: SidebarPref;
}

export interface Theme {
  brand?: Brand;
  ui?: ThemeUI;
  /** Page ids the user chose to hide in the sidebar. */
  hiddenNav?: string[];
  /** Id of the last applied preset (informational, drives the appearance UI). */
  preset?: string;
}

export const DEFAULT_THEME: Theme = {
  brand: { title: '多模态理解增强', subtitle: '图片 / 音频 / 视频' },
  ui: {
    mode: 'system',
    primary: '',
    glass: true,
    glassBlur: 18,
    gradient: false,
    radius: 'soft',
    motion: 'full',
    font: 'sans',
    sidebar: 'expanded',
  },
  hiddenNav: [],
};

/** Storage adapter — implement this to persist themes anywhere. */
export interface ThemeStorage {
  load: () => Theme | null;
  save: (theme: Theme) => void;
}

const STORAGE_KEY = 'mme.console.theme';

/** Default adapter: localStorage, tolerant of private mode / disabled storage. */
export const localThemeStorage: ThemeStorage = {
  load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? (JSON.parse(raw) as Theme) : null;
    } catch {
      return null;
    }
  },
  save(theme) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(theme));
    } catch {
      /* private mode / quota — a non-persistent theme is still usable */
    }
  },
};

/** In-memory adapter, handy for tests and stateless demos. */
export function memoryThemeStorage(initial: Theme = DEFAULT_THEME): ThemeStorage {
  let current: Theme = initial;
  return {
    load: () => current,
    save: (theme) => {
      current = theme;
    },
  };
}

export function prefersDark(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  );
}

export function resolveMode(theme: Theme): 'dark' | 'light' {
  const mode = theme.ui?.mode ?? 'system';
  if (mode === 'system') return prefersDark() ? 'dark' : 'light';
  return mode === 'dark' ? 'dark' : 'light';
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
}

/** `--radius` value for each rounding preference. */
export const RADIUS_VALUES: Record<RadiusPref, string> = {
  sharp: '0.25rem',
  soft: '0.75rem',
  round: '1.125rem',
};

/** Interface font stacks. Serif / mono exist so a skin can have real character. */
export const FONT_STACKS: Record<FontPref, string> = {
  sans: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', sans-serif",
  serif:
    "'Iowan Old Style', 'Source Serif 4', 'Songti SC', 'Noto Serif SC', Georgia, 'Times New Roman', serif",
  mono: "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, Consolas, 'PingFang SC', monospace",
};

/**
 * Motion budget per preference, consumed by both CSS (`data-motion`) and the
 * framer-motion `MotionConfig`. Keeping both in one place stops the two from
 * disagreeing — which reads as "the page animates but the panels snap".
 */
export const MOTION_TIMING: Record<
  MotionPref,
  { duration: number; springDamping: number; enabled: boolean }
> = {
  full: { duration: 0.32, springDamping: 26, enabled: true },
  subtle: { duration: 0.16, springDamping: 34, enabled: true },
  off: { duration: 0, springDamping: 60, enabled: false },
};

/* ------------------------------------------------------------------ */
/* glass tuning                                                        */
/* ------------------------------------------------------------------ */

export const GLASS_BLUR_MIN = 0;
export const GLASS_BLUR_MAX = 40;
export const GLASS_BLUR_DEFAULT = 18;

/**
 * Panel opacity for a given blur radius.
 *
 * These two knobs are coupled in practice: a heavy blur without enough opacity
 * smears the wallpaper into the text, while a light blur with high opacity just
 * looks like a solid panel. The curve keeps roughly constant legibility — more
 * blur buys a little more transparency, never less.
 *
 * 0px  → 0.94 (essentially solid, blur off)
 * 18px → 0.86 (the tuned default)
 * 40px → 0.78 (strong frost, still readable)
 */
export function glassOpacityFor(blur: number): number {
  const clamped = clamp(blur, GLASS_BLUR_MIN, GLASS_BLUR_MAX);
  return 0.94 - (clamped / GLASS_BLUR_MAX) * 0.16;
}

/** Merge a patch into the stored theme without dropping sibling keys. */
export function mergeTheme(base: Theme, patch: Partial<Theme>): Theme {
  return {
    ...base,
    ...patch,
    brand: { ...(base.brand ?? {}), ...(patch.brand ?? {}) },
    ui: {
      ...(base.ui ?? {}),
      ...(patch.ui ?? {}),
      wallpaper: { ...(base.ui?.wallpaper ?? {}), ...(patch.ui?.wallpaper ?? {}) },
    },
    hiddenNav: patch.hiddenNav ?? base.hiddenNav ?? [],
  };
}
