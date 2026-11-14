// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { cn } from '@/lib/utils';
import { Button } from './button';

/**
 * Modal dialog.
 *
 * Handles the three things hand-rolled modals usually get wrong: Escape to
 * close, background scroll lock while open, and focus restoration to whatever
 * was focused before it opened.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children?: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreFocus.current = document.activeElement as HTMLElement | null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Move focus into the panel so screen readers announce the dialog.
    const focusTimer = window.setTimeout(() => panelRef.current?.focus(), 30);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
      window.clearTimeout(focusTimer);
      restoreFocus.current?.focus?.();
    };
  }, [open, onClose]);

  return createPortal(
    <AnimatePresence>
      {open ? (
        <div className="fixed inset-0 z-[100] grid place-items-center p-4">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={onClose}
            className="absolute inset-0 bg-black/45 backdrop-blur-[2px]"
          />
          <motion.div
            ref={panelRef}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-label={title}
            initial={{ opacity: 0, y: 10, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.985 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
            className={cn(
              'relative flex w-full max-w-[520px] flex-col overflow-hidden rounded-2xl border border-border bg-popover shadow-2xl outline-none',
              className,
            )}
          >
            <div className="flex items-start gap-3 border-b border-border/60 px-4 py-3">
              <div className="min-w-0 flex-1">
                <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
                {description ? (
                  <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{description}</p>
                ) : null}
              </div>
              <Button variant="ghost" size="icon" aria-label="关闭" onClick={onClose} className="-mr-1 -mt-1 size-8">
                <X className="size-4" />
              </Button>
            </div>
            {children ? <div className="max-h-[65vh] overflow-y-auto px-4 py-3.5">{children}</div> : null}
            {footer ? (
              <div className="flex items-center justify-end gap-2 border-t border-border/60 px-4 py-2.5">{footer}</div>
            ) : null}
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
