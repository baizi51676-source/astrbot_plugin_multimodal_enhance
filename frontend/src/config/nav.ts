// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

/**
 * 页面注册表 —— 增删页面只改这里。
 *
 * 外壳的侧边栏、移动端标签栏与外观页的显隐开关都读这一份配置。
 */
import { lazy, type ComponentType, type LazyExoticComponent } from 'react';
import { Gauge, ListTree, ScrollText, Wrench, type LucideIcon } from 'lucide-react';

export interface NavItem {
  id: string;
  label: string;
  /** 外壳标题栏下方的一行说明。 */
  description?: string;
  icon: LucideIcon;
  /** 移动端底栏显示（保持 5 个以内）。 */
  primary?: boolean;
  component: LazyExoticComponent<ComponentType<Record<string, never>>>;
}

export interface NavGroup {
  group: string;
  items: NavItem[];
}

function page(loader: () => Promise<{ default: ComponentType<Record<string, never>> }>) {
  return lazy(loader);
}

export const NAV: NavGroup[] = [
  {
    group: '控制台',
    items: [
      {
        id: 'overview',
        label: '总览',
        description: '插件状态、各 Bot 的功能开关与实际生效配置。',
        icon: Gauge,
        primary: true,
        component: page(() => import('@/pages/overview')),
      },
      {
        id: 'config',
        label: '配置',
        description: '默认配置与每个 Bot 的覆写项，保存后立即生效。',
        icon: ListTree,
        primary: true,
        component: page(() => import('@/pages/config')),
      },
      {
        id: 'logs',
        label: '插件日志',
        description: '最近的插件日志，可开启实时流或导出。',
        icon: ScrollText,
        primary: true,
        component: page(() => import('@/pages/logs')),
      },
      {
        id: 'env',
        label: '环境配置',
        description: '依赖检测、一键安装与临时文件清理。',
        icon: Wrench,
        primary: true,
        component: page(() => import('@/pages/env')),
      },
    ],
  },
];

export const PRIMARY_ITEMS = NAV.flatMap((group) => group.items.filter((item) => item.primary));

export const ALL_ITEMS = NAV.flatMap((group) => group.items);

export function findNavItem(id: string): NavItem | undefined {
  return ALL_ITEMS.find((item) => item.id === id);
}
