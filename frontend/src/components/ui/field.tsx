// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { useId, type ComponentProps, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

const FIELD_BASE =
  'w-full rounded-md border border-input bg-transparent px-3 text-sm outline-none transition-colors ' +
  'placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/25 ' +
  'disabled:cursor-not-allowed disabled:opacity-60';

export function Input({ className, ...props }: ComponentProps<'input'>) {
  return <input className={cn(FIELD_BASE, 'h-9', className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<'textarea'>) {
  return <textarea className={cn(FIELD_BASE, 'min-h-20 resize-y py-2 leading-relaxed', className)} {...props} />;
}


/**
 * Labelled field wrapper.
 *
 * Passing `hint` renders help text, `error` renders it in the destructive tone
 * and wires `aria-invalid` onto the control via cloneElement-free composition:
 * the child keeps its own props, we only supply layout.
 */
export function Field({
  label,
  hint,
  error,
  htmlFor,
  className,
  children,
}: {
  label?: string;
  hint?: string;
  error?: string;
  htmlFor?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label className={cn('flex flex-col gap-1.5', className)} htmlFor={htmlFor}>
      {label ? <span className="text-[12.5px] font-medium text-foreground/85">{label}</span> : null}
      {children}
      {error ? (
        <span className="text-[11.5px] leading-relaxed text-destructive">{error}</span>
      ) : hint ? (
        <span className="text-[11.5px] leading-relaxed text-muted-foreground">{hint}</span>
      ) : null}
    </label>
  );
}

/** A field plus its own generated id, for callers that need the association. */
export function useFieldId(prefix = 'field'): string {
  const id = useId();
  return `${prefix}-${id}`;
}
