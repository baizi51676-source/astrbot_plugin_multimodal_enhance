// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import type { ReactNode } from 'react';
import { LayoutDashboard } from 'lucide-react';
import { cn } from '@/lib/utils';

/** Centred "nothing here yet" block, shared by every page. */
export function EmptyState({
  icon: Icon = LayoutDashboard,
  title,
  description,
  action,
  className,
}: {
  icon?: typeof LayoutDashboard;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-card/40 px-6 py-11 text-center',
        className,
      )}
    >
      <div className="grid size-12 place-items-center rounded-full bg-muted text-muted-foreground">
        <Icon className="size-5" />
      </div>
      <div className="space-y-1">
        <h3 className="text-sm font-medium text-foreground">{title}</h3>
        {description ? <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">{description}</p> : null}
      </div>
      {action ? <div className="pt-1">{action}</div> : null}
    </div>
  );
}
