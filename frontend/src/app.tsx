// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { ThemeProvider } from '@/lib/theme-provider';
import { ToastProvider } from '@/components/ui/toast';
import { SessionProvider } from '@/components/layout/session';
import { Shell, useHashRoute } from '@/components/layout/shell';

/**
 * 应用根组件。
 *
 * 插件页面由 AstrBot 宿主鉴权，这里不再经过登录门控：
 * 仍然保留 SessionProvider，因为外壳的「退出登录」按钮依赖它，
 * 但会话直接视为已登录。
 */
function Gate() {
  const [page, navigate] = useHashRoute();
  return <Shell page={page} navigate={navigate} />;
}

export function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <SessionProvider>
          <Gate />
        </SessionProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}
