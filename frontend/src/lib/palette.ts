// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

/**
 * Monet-style colour extraction.
 *
 * "Monet" here means the Material-You style dynamic-colour approach: take an
 * image, find the colours that actually read as *hues* (not the near-black
 * shadows or near-white highlights that dominate a photograph), and turn the
 * strongest one into a usable accent.
 *
 * The pipeline is deliberately simple and explainable rather than a faithful
 * reimplementation of Material's HCT engine:
 *
 *   1. downscale to ~160px so a 4K wallpaper stays cheap and noise averages out
 *   2. bucket every pixel by hue (24 bins of 15°)
 *   3. weight each pixel by saturation and mid-lightness, so muted greys and
 *      blown-out whites contribute almost nothing
 *   4. average each bucket's colour, rank buckets by accumulated weight
 *   5. nudge the winner into a range that works as a UI accent
 *
 * No dependencies, no CORS surprises beyond what the caller already controls.
 */

export interface SampledColor {
  hex: string;
  /** Share of the total weight, 0–1. Useful for showing relative prominence. */
  weight: number;
  h: number;
  s: number;
  l: number;
}

/* ------------------------------------------------------------------ */
/* colour space helpers                                                */
/* ------------------------------------------------------------------ */

export function rgbToHsl(r: number, g: number, b: number): { h: number; s: number; l: number } {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  const delta = max - min;

  if (delta === 0) return { h: 0, s: 0, l };

  const s = l > 0.5 ? delta / (2 - max - min) : delta / (max + min);
  let h: number;
  if (max === rn) h = ((gn - bn) / delta + (gn < bn ? 6 : 0)) / 6;
  else if (max === gn) h = ((bn - rn) / delta + 2) / 6;
  else h = ((rn - gn) / delta + 4) / 6;

  return { h: h * 360, s, l };
}

export function hslToRgb(h: number, s: number, l: number): { r: number; g: number; b: number } {
  const hue = ((h % 360) + 360) % 360 / 360;
  if (s === 0) {
    const v = Math.round(l * 255);
    return { r: v, g: v, b: v };
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const channel = (t: number) => {
    let value = t;
    if (value < 0) value += 1;
    if (value > 1) value -= 1;
    if (value < 1 / 6) return p + (q - p) * 6 * value;
    if (value < 1 / 2) return q;
    if (value < 2 / 3) return p + (q - p) * (2 / 3 - value) * 6;
    return p;
  };
  return {
    r: Math.round(channel(hue + 1 / 3) * 255),
    g: Math.round(channel(hue) * 255),
    b: Math.round(channel(hue - 1 / 3) * 255),
  };
}

export function hslToHex(h: number, s: number, l: number): string {
  const { r, g, b } = hslToRgb(h, s, l);
  return '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('');
}

/* ------------------------------------------------------------------ */
/* image sampling                                                      */
/* ------------------------------------------------------------------ */

const MAX_EDGE = 160;
const HUE_BINS = 24;

/** Load an image URL into a canvas. Rejects on network/CORS failure. */
function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    // Wallpapers stored as data URLs need no CORS handling; remote ones do.
    if (!url.startsWith('data:')) image.crossOrigin = 'anonymous';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('图片加载失败'));
    image.src = url;
  });
}

/**
 * Extract the dominant hues from an image.
 *
 * Returns up to `count` colours ordered by prominence. Empty array when the
 * image cannot be read (CORS-tainted canvas, broken URL) — callers should treat
 * that as "no suggestion" rather than an error.
 */
export async function extractPalette(url: string, count = 6): Promise<SampledColor[]> {
  const image = await loadImage(url);

  const scale = Math.min(1, MAX_EDGE / Math.max(image.naturalWidth, image.naturalHeight));
  const width = Math.max(1, Math.round(image.naturalWidth * scale));
  const height = Math.max(1, Math.round(image.naturalHeight * scale));

  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) return [];

  context.drawImage(image, 0, 0, width, height);

  let data: Uint8ClampedArray;
  try {
    data = context.getImageData(0, 0, width, height).data;
  } catch {
    // Tainted canvas — a remote image without permissive CORS headers.
    return [];
  }

  interface Bucket {
    weight: number;
    r: number;
    g: number;
    b: number;
  }
  const buckets = new Map<number, Bucket>();
  let totalWeight = 0;

  // Step 2 keeps the loop at a few thousand samples even for large sources.
  for (let y = 0; y < height; y += 2) {
    for (let x = 0; x < width; x += 2) {
      const offset = (y * width + x) * 4;
      const alpha = data[offset + 3];
      if (alpha < 125) continue;

      const r = data[offset];
      const g = data[offset + 1];
      const b = data[offset + 2];
      const { h, s, l } = rgbToHsl(r, g, b);

      // Ignore greys and near-extremes: they carry no usable hue and would
      // otherwise swamp the ranking (most photos are mostly shadow/white).
      if (s < 0.12) continue;
      if (l < 0.08 || l > 0.94) continue;

      // Vivid, mid-tone pixels count the most.
      const vividness = Math.pow(s, 1.35);
      const midness = 1 - Math.abs(l - 0.5) * 1.3;
      const weight = vividness * Math.max(0.08, midness);
      if (weight <= 0.001) continue;

      const bin = Math.floor((h / 360) * HUE_BINS) % HUE_BINS;
      const current = buckets.get(bin) ?? { weight: 0, r: 0, g: 0, b: 0 };
      current.weight += weight;
      current.r += r * weight;
      current.g += g * weight;
      current.b += b * weight;
      buckets.set(bin, current);
      totalWeight += weight;
    }
  }

  if (!buckets.size || totalWeight === 0) return [];

  const ranked = [...buckets.values()]
    .map((bucket) => {
      // Weighted average of the pixels that landed in this hue bin.
      const r = bucket.r / bucket.weight;
      const g = bucket.g / bucket.weight;
      const b = bucket.b / bucket.weight;
      const { h, s, l } = rgbToHsl(r, g, b);
      return {
        hex: hslToHex(h, s, l),
        weight: bucket.weight / totalWeight,
        h,
        s,
        l,
      };
    })
    .sort((a, b) => b.weight - a.weight);

  // Drop colours that are indistinguishable from one already kept, so the
  // swatch row shows six distinct hues instead of six blues.
  const picked: SampledColor[] = [];
  for (const color of ranked) {
    const tooClose = picked.some((kept) => {
      const hueGap = Math.abs(((kept.h - color.h + 540) % 360) - 180);
      return hueGap > 168 && Math.abs(kept.l - color.l) < 0.28;
    });
    if (!tooClose) picked.push(color);
    if (picked.length >= count) break;
  }

  return picked;
}

/* ------------------------------------------------------------------ */
/* turning a sampled colour into a usable accent                        */
/* ------------------------------------------------------------------ */

/**
 * Nudge a sampled colour into a range that works as a UI accent.
 *
 * Raw photograph colours are often either too pale (washed out on white) or too
 * dark (invisible on dark backgrounds). Clamping saturation and lightness into a
 * middle band keeps the hue — which is the character the user wanted — while
 * guaranteeing contrast in both themes.
 */
export function asAccent(color: SampledColor): string {
  const saturation = Math.min(0.78, Math.max(0.45, color.s));
  const lightness = Math.min(0.62, Math.max(0.44, color.l));
  return hslToHex(color.h, saturation, lightness);
}

/** All distinct accent candidates from a sampled palette. */
export function accentCandidates(colors: SampledColor[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const color of colors) {
    const hex = asAccent(color);
    if (!seen.has(hex)) {
      seen.add(hex);
      out.push(hex);
    }
  }
  return out;
}
