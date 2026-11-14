// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react';

/**
 * 会话占位。
 *
 * 插件页面由 AstrBot 宿主完成鉴权，页面内没有登录流程；
 * 这里始终返回一个已登录用户，只为让外壳的会话相关 UI 能正常工作。
 */
export interface SessionUser {
  name: string;
  signedInAt: number;
}

interface SessionContextValue {
  user: SessionUser | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession must be used inside <SessionProvider>');
  return value;
}

const HOST_USER: SessionUser = { name: 'AstrBot', signedInAt: 0 };

export function SessionProvider({ children }: { children: ReactNode }) {
  // 不读取 localStorage：会话由宿主决定，页面不做持久化。
  const login = useCallback(async () => {
    /* 宿主负责鉴权，页面不需要登录动作 */
  }, []);
  const logout = useCallback(() => {
    /* 退出登录只能由 AstrBot 控制台完成 */
  }, []);

  const value = useMemo<SessionContextValue>(
    () => ({ user: HOST_USER, login, logout }),
    [login, logout],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
