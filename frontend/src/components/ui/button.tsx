// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import type { ComponentProps, ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * Button.
 *
 * A single component with `variant` / `size` maps instead of one component per
 * look — the classes are declared once here, so a template user re-skins the
 * whole app by editing this file rather than hunting through pages.
 *
 * Interaction detail: the press is a 0.97 scale plus a shadow drop, which reads
 * as "physical" at 120–200ms. Longer feels laggy, shorter flickers.
 */
export type ButtonVariant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'destructive';
export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon';

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-primary text-primary-foreground shadow-xs hover:shadow-md hover:brightness-[1.07] active:brightness-95',
  secondary: 'bg-secondary text-secondary-foreground shadow-xs hover:bg-secondary/75 active:bg-secondary/65',
  outline:
    'border border-border bg-card text-foreground shadow-xs hover:border-[var(--accent-line)] hover:bg-accent hover:text-accent-foreground',
  ghost: 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
  destructive:
    'bg-destructive text-destructive-foreground shadow-xs hover:shadow-md hover:brightness-[1.07] active:brightness-95',
};

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 gap-1.5 rounded-md px-2.5 text-xs',
  md: 'h-9 gap-2 rounded-md px-3.5 text-sm',
  lg: 'h-10 gap-2 rounded-lg px-5 text-sm',
  icon: 'size-9 rounded-md',
};

export interface ButtonProps extends ComponentProps<'button'> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({ className, variant = 'primary', size = 'md', type = 'button', ...props }: ButtonProps) {
  return (
    <button
      type={type}
      data-accent-surface={variant === 'primary' ? '' : undefined}
      className={cn(
        'inline-flex select-none items-center justify-center whitespace-nowrap font-medium',
        // One transition list for every interactive surface in the app.
        'transition-[background-color,color,box-shadow,filter,transform,border-color] duration-200 ',
        'active:scale-[0.97] outline-none',
        'disabled:pointer-events-none disabled:opacity-50 disabled:active:scale-100',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    />
  );
}

/** Card shell: one `data-slot` so glass mode can target every panel. */
export function Card({ className, children, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="card"
      className={cn(
        'rounded-xl border border-border/70 bg-card text-card-foreground',
        // A hair of lift on hover signals interactivity without moving layout.
        'transition-[border-color,box-shadow,transform] duration-200 hover:border-border',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/** Card that lifts on hover — use for clickable panels only. */
export function InteractiveCard({ className, children, ...props }: ComponentProps<'div'>) {
  return (
    <div
      data-slot="card"
      className={cn(
        'rounded-xl border border-border/70 bg-card text-card-foreground',
        'transition-[border-color,box-shadow,transform] duration-200',
        'hover:-translate-y-0.5 hover:border-[var(--accent-line)] hover:shadow-card',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardHeader({ className, ...props }: ComponentProps<'div'>) {
  return <div className={cn('flex flex-col gap-1 border-b border-border/60 px-4 py-3', className)} {...props} />;
}

export function CardTitle({ className, ...props }: ComponentProps<'h3'>) {
  return <h3 className={cn('text-sm font-semibold tracking-tight', className)} {...props} />;
}

export function CardDescription({ className, ...props }: ComponentProps<'p'>) {
  return <p className={cn('text-xs leading-relaxed text-muted-foreground', className)} {...props} />;
}

export function CardContent({ className, ...props }: ComponentProps<'div'>) {
  return <div className={cn('px-4 py-3.5', className)} {...props} />;
}

export function CardFooter({ className, ...props }: ComponentProps<'div'>) {
  return (
    <div
      className={cn('flex items-center justify-end gap-2 border-t border-border/60 px-4 py-2.5', className)}
      {...props}
    />
  );
}

/** Small status pill. */
export function Badge({
  className,
  tone = 'muted',
  ...props
}: ComponentProps<'span'> & { tone?: 'accent' | 'muted' | 'success' | 'warning' | 'destructive' }) {
  const tones: Record<string, string> = {
    accent: 'border-[var(--accent-line)] bg-[var(--accent-soft)] text-primary',
    muted: 'border-border bg-muted text-muted-foreground',
    success: 'border-success/30 bg-success/12 text-success',
    warning: 'border-warning/35 bg-warning/15 text-warning',
    destructive: 'border-destructive/35 bg-destructive/12 text-destructive',
  };
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-none',
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

/** Section heading used at the top of every page. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  /*
   * The header doubles as a glass panel in wallpaper mode. Its surface comes
   * from the *same* Tailwind classes Card uses (`rounded-xl border-border/70`),
   * and CSS only supplies the glass background/blur — so radius and border can
   * never drift from the cards again (they had: radius was one step smaller and
   * the border used the solid token instead of the 70% one).
   */
  return (
    <div
      data-slot="page-header"
      className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-border/70"
    >
      <div className="min-w-0">
        <h1 className="text-[19px] font-semibold tracking-tight">{title}</h1>
        {description ? <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
