// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

/**
 * 插件标识。
 *
 * 内联 SVG：不额外发起请求，任意缩放都清晰。
 * 图形表达「图片 + 音频 + 视频」三种模态融合为一。
 */
export function BrandMark({ size = 30, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 512 512"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <rect width="512" height="512" rx="112" fill="#4e576e" />
      {/* 图片：左侧相框 */}
      <rect x="96" y="128" width="176" height="144" rx="24" fill="#f1f1f4" opacity="0.92" />
      <circle cx="140" cy="172" r="16" fill="#4e576e" />
      <path d="M112 248l48-56 40 44 28-28 32 40z" fill="#4e576e" />
      {/* 视频：右下播放三角 */}
      <rect x="240" y="240" width="176" height="144" rx="24" fill="#f1f1f4" opacity="0.98" />
      <path d="M308 282l64 30-64 30z" fill="#4e576e" />
      {/* 音频：底部声波 */}
      <path
        d="M120 336v32M160 320v64M200 344v24M296 336v32M336 320v64M376 344v24"
        stroke="#f1f1f4"
        strokeWidth="18"
        strokeLinecap="round"
        opacity="0.85"
      />
    </svg>
  );
}
