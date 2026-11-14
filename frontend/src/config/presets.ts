// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

/**
 * Preset skins.
 *
 * A preset is just a Theme fragment: applying one merges it over the current
 * theme, so a user can start from a look and then tweak individual knobs. Keep
 * every preset internally consistent (accent ↔ radius ↔ font ↔ gradient) —
 * a preset that only recolours tends to look accidental.
 */
import type { Theme } from '@/lib/theme';

export interface ThemePreset {
  id: string;
  name: string;
  /** One-line description shown under the name. */
  hint: string;
  /** Swatch colours used to draw the preview chip: [page, accent, panel]. */
  swatch: [string, string, string];
  theme: Theme;
}

export const PRESETS: ThemePreset[] = [
  {
    id: 'default',
    name: '默认',
    hint: '中性底 + 天蓝强调，安全耐看',
    swatch: ['#f7f9fb', '#0ea5e9', '#ffffff'],
    theme: {
      ui: {
        primary: '',
        radius: 'soft',
        font: 'sans',
        gradient: false,
        glass: true,
      },
    },
  },
  {
    id: 'midnight',
    name: '午夜',
    hint: '深色 + 靛紫渐变，适合长时间盯屏',
    swatch: ['#0b1116', '#8b5cf6', '#121a21'],
    theme: {
      ui: {
        mode: 'dark',
        primary: '#8b5cf6',
        radius: 'soft',
        font: 'sans',
        gradient: true,
        glass: true,
      },
    },
  },
  {
    id: 'terminal',
    name: '终端',
    hint: '等宽字体 + 直角 + 荧光绿，极客感',
    swatch: ['#0d1117', '#22c55e', '#161b22'],
    theme: {
      ui: {
        mode: 'dark',
        primary: '#22c55e',
        radius: 'sharp',
        font: 'mono',
        gradient: false,
        glass: false,
      },
    },
  },
  {
    id: 'paper',
    name: '纸感',
    hint: '衬线字体 + 大圆角 + 暖褐，安静克制',
    swatch: ['#faf7f2', '#b45309', '#fffdf9'],
    theme: {
      ui: {
        mode: 'light',
        primary: '#b45309',
        radius: 'round',
        font: 'serif',
        gradient: false,
        glass: true,
      },
    },
  },
  {
    id: 'candy',
    name: '糖果',
    hint: '玫红渐变 + 大圆角，活泼轻快',
    swatch: ['#fdf7fb', '#ec4899', '#ffffff'],
    theme: {
      ui: {
        mode: 'light',
        primary: '#ec4899',
        radius: 'round',
        font: 'sans',
        gradient: true,
        glass: true,
      },
    },
  },
  {
    id: 'forest',
    name: '森林',
    hint: '墨绿 + 柔和圆角，稳重而不沉闷',
    swatch: ['#f5faf7', '#0d9488', '#ffffff'],
    theme: {
      ui: {
        mode: 'light',
        primary: '#0d9488',
        radius: 'soft',
        font: 'sans',
        gradient: true,
        glass: true,
      },
    },
  },
];

export function findPreset(id: string | undefined): ThemePreset | undefined {
  return id ? PRESETS.find((preset) => preset.id === id) : undefined;
}
