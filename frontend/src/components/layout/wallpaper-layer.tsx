// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { useTheme } from '@/lib/theme-provider';

/**
 * Fixed wallpaper layer behind the whole shell.
 *
 * The blurred layer is capped and scaled slightly: a `blur()` on a fixed
 * full-viewport element bleeds transparent pixels at its own edges, which reads
 * as a broken, holey background. Zooming a few percent pushes those edges
 * outside the viewport.
 */
export function WallpaperLayer() {
  const { theme } = useTheme();
  const wallpaper = theme.ui?.wallpaper;
  if (!wallpaper?.url) return null;

  const blur = Math.min(Number(wallpaper.blur) || 0, 18);
  const dim = Number(wallpaper.dim) || 0;

  return (
    <div aria-hidden className="wallpaper-layer pointer-events-none fixed inset-0 z-0 overflow-hidden">
      <div
        className="absolute inset-0 bg-cover bg-center"
        style={{
          backgroundImage: `url(${wallpaper.url})`,
          filter: blur ? `blur(${blur}px)` : undefined,
          transform: blur ? 'scale(1.08)' : undefined,
          transformOrigin: 'center',
          willChange: 'filter',
          backfaceVisibility: 'hidden',
        }}
      />
      {dim > 0 ? (
        <div className="absolute inset-0" style={{ background: `rgba(0,0,0,${dim / 100})` }} />
      ) : null}
    </div>
  );
}
