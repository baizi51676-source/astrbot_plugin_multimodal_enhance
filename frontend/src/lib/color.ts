// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

/**
 * Colour helpers.
 *
 * These deliberately avoid color-mix() and any canvas/OKLCH parsing: the engine
 * is asked to resolve the colour for us, which works for hex, rgb(), oklch()
 * and named colours alike.
 */

/** Resolve any CSS colour into an `r, g, b` triplet, or null if unparsable. */
export function resolveRgb(value: string): string | null {
  try {
    const probe = document.createElement('span');
    probe.style.position = 'absolute';
    probe.style.opacity = '0';
    probe.style.color = value;
    document.body.appendChild(probe);
    const computed = getComputedStyle(probe).color;
    probe.remove();
    const match = computed.match(/rgba?\(([^)]+)\)/);
    if (!match) return null;
    return match[1]
      .split(',')
      .map((part) => part.trim())
      .slice(0, 3)
      .join(', ');
  } catch {
    return null;
  }
}

/**
 * Publish rgba() derivatives of the accent colour as CSS variables.
 *
 * `--accent-soft` / `--accent-faint` / `--accent-line` are what every tinted
 * surface (chips, status dots, chart fills, selection) reads, so they follow
 * the accent without needing color-mix().
 */
export function applyAccentVars(root: HTMLElement, accent: string): void {
  const rgb = resolveRgb(accent);
  if (!rgb) return;
  root.style.setProperty('--accent-soft', `rgba(${rgb}, 0.12)`);
  root.style.setProperty('--accent-faint', `rgba(${rgb}, 0.07)`);
  root.style.setProperty('--accent-line', `rgba(${rgb}, 0.34)`);
}

export function clearAccentVars(root: HTMLElement): void {
  ['--accent-soft', '--accent-faint', '--accent-line'].forEach((key) => root.style.removeProperty(key));
}

/**
 * Pick black or white text for a background colour, by relative luminance.
 * Used for the accent-coloured buttons so a light accent stays readable.
 */
export function readableOn(hex: string): string {
  const clean = hex.replace('#', '');
  const full = clean.length === 3 ? clean.split('').map((char) => char + char).join('') : clean;
  if (full.length !== 6) return '#ffffff';
  const r = parseInt(full.slice(0, 2), 16) / 255;
  const g = parseInt(full.slice(2, 4), 16) / 255;
  const b = parseInt(full.slice(4, 6), 16) / 255;
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return luminance > 0.62 ? '#0b1220' : '#ffffff';
}

export function isHexColor(value: string | undefined | null): value is string {
  return !!value && /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(value);
}

/**
 * Paint the document root with the theme's real background colour.
 *
 * With a wallpaper on, `body` is transparent, so the host WebView's own default
 * (often black) shows through at the edges and on overscroll — the "light theme
 * but the base plate is black" symptom. Probing a real element is sturdier than
 * reading the variable, because the variable name is theme-dependent.
 */
export function paintRootBackground(root: HTMLElement, fallback: string): void {
  let color = '';
  try {
    const probe = document.createElement('div');
    probe.className = 'bg-background';
    probe.style.cssText = 'position:absolute;left:-9999px;top:0;width:1px;height:1px;';
    document.body.appendChild(probe);
    color = getComputedStyle(probe).backgroundColor;
    probe.remove();
  } catch {
    /* fall through to the fallback */
  }
  root.style.backgroundColor = !color || color === 'rgba(0, 0, 0, 0)' ? fallback : color;
}
