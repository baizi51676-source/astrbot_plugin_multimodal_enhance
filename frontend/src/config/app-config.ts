// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

/**
 * 品牌与外观配置 —— 换品牌只改这一个文件。
 */

export const APP_CONFIG = {
  /** 侧边栏与文档标题使用的名称。 */
  name: '多模态理解增强',
  subtitle: '图片 / 音频 / 视频',
  tagline: 'AstrBot 多模态理解增强插件控制台',

  /**
   * 插件页面由 AstrBot 宿主鉴权，页面自身不做登录，
   * 因此这里恒为 false：Gate 始终直接渲染外壳。
   */
  demoAuth: false as boolean,

  accentPresets: [
    '#4e576e',
    '#0284c7',
    '#2563eb',
    '#6366f1',
    '#8b5cf6',
    '#10b981',
    '#14b8a6',
    '#f59e0b',
  ],
};

export type AppConfig = typeof APP_CONFIG;
