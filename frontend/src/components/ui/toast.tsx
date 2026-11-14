// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { cn } from '@/lib/utils';

export type ToastTone = 'success' | 'error' | 'info';

export interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

interface ToastApi {
  notify: (message: string, tone?: ToastTone) => void;
  dismiss: (id: number) => void;
  toasts: ToastItem[];
}

const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const value = useContext(ToastContext);
  if (!value) throw new Error('useToast must be used inside <ToastProvider>');
  return value;
}

/**
 * Toast provider + viewport.
 *
 * Identical messages arriving in quick succession are collapsed into one toast:
 * sliders and rapid clicks would otherwise stack six copies of "已保存".
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(0);
  const last = useRef<{ message: string; at: number }>({ message: '', at: 0 });

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const notify = useCallback(
    (message: string, tone: ToastTone = 'info') => {
      const now = Date.now();
      if (last.current.message === message && now - last.current.at < 2500) {
        last.current.at = now;
        return;
      }
      last.current = { message, at: now };
      const id = ++nextId.current;
      setToasts((current) => [...current, { id, message, tone }].slice(-3));
      window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 3600);
    },
    [],
  );

  const value = useMemo<ToastApi>(() => ({ notify, dismiss, toasts }), [notify, dismiss, toasts]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-6 left-1/2 z-[110] flex -translate-x-1/2 flex-col items-center gap-2">
        <AnimatePresence initial={false}>
          {toasts.map((toast) => (
            <motion.div
              key={toast.id}
              role="status"
              initial={{ opacity: 0, y: 12, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.98 }}
              transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
              onClick={() => dismiss(toast.id)}
              className={cn(
                'pointer-events-auto max-w-[92vw] cursor-pointer rounded-lg border bg-popover px-4 py-2.5 text-sm shadow-lg',
                toast.tone === 'error'
                  ? 'border-destructive/40 text-destructive'
                  : toast.tone === 'success'
                    ? 'border-[var(--accent-line)] text-primary'
                    : 'border-border text-foreground',
              )}
            >
              {toast.message}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}
