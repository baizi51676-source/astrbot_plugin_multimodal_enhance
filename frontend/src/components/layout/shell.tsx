// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { Suspense, useCallback, useEffect, useState, type MouseEvent, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {

  Menu,
  Moon,
  PanelLeftClose,
  RefreshCw,
  Sun,
  X,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTheme } from '@/lib/theme-provider';
import { APP_CONFIG } from '@/config/app-config';
import { NAV, PRIMARY_ITEMS, findNavItem, type NavGroup } from '@/config/nav';
import { Button } from '@/components/ui/button';
import { BrandMark } from './brand-mark';
import { WallpaperLayer } from './wallpaper-layer';


/** Easing shared by every shell animation, so nothing feels out of family. */
const EASE = [0.16, 1, 0.3, 1] as const;

/** Read the page id out of the URL hash, falling back to the default page. */
function readRoute(): string {
  const raw = window.location.hash.replace(/^#\/?/, '');
  return findNavItem(raw) ? raw : (PRIMARY_ITEMS[0]?.id ?? 'dashboard');
}

/** Resolve the current page id from the URL hash, e.g. `#/appearance`. */
export function useHashRoute(): [string, (id: string) => void] {
  const [page, setPage] = useState(readRoute);

  // Keep the view in sync with manual hash edits and the back button. This has
  // to live in an effect: assigning `window.onhashchange` during render runs on
  // every render and clobbers React's own listener bookkeeping.
  useEffect(() => {
    const onHashChange = () => setPage(readRoute());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const navigate = useCallback((id: string) => {
    setPage(id);
    if (window.location.hash !== `#/${id}`) window.location.hash = `#/${id}`;
    window.scrollTo({ top: 0 });
  }, []);

  return [page, navigate];
}

function PageFallback() {
  return (
    <div className="flex flex-col gap-3">
      <div className="sk-shimmer h-24 w-full rounded-xl" />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="rounded-xl border border-border/60 bg-card p-4">
            <div className="sk-shimmer h-3 w-16 rounded" />
            <div className="sk-shimmer mt-3 h-6 w-24 rounded" />
            <div className="sk-shimmer mt-4 h-4 w-20 rounded-full" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function Shell({ page, navigate }: { page: string; navigate: (id: string) => void }) {
  const { theme, isDark, setMode, toggleSidebar } = useTheme();

  const [mobileNav, setMobileNav] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const hidden = theme.hiddenNav ?? [];
  const collapsed = theme.ui?.sidebar === 'collapsed';
  const current = findNavItem(page);
  const Page = current?.component;

  const toggleTheme = (event: MouseEvent<HTMLButtonElement>) => {
    setMode(isDark ? 'light' : 'dark', { clientX: event.clientX, clientY: event.clientY });
  };

  const refresh = () => {
    setRefreshing(true);
    window.setTimeout(() => window.location.reload(), 200);
  };

  const activeGroups = NAV.map((group) => ({
    ...group,
    items: group.items.filter((item) => !hidden.includes(item.id)),
  })).filter((group) => group.items.length > 0);

  // `data-sidebar` drives the collapsed layout from CSS; keeping it on <html>
  // means the width transition survives re-renders of the whole tree.
  useEffect(() => {
    document.documentElement.dataset.sidebar = collapsed ? 'collapsed' : 'expanded';
  }, [collapsed]);

  const tools: ShellTool[] = [
    { id: 'theme', label: isDark ? '切换到浅色' : '切换到深色', icon: null, onClick: toggleTheme },
    {
      id: 'refresh',
      label: '刷新',
      icon: <RefreshCw className={cn('size-4 transition-transform duration-500', refreshing && 'rotate-180')} />,
      onClick: refresh,
    },
  ];

  return (
    <div className="flex min-h-dvh w-full">
      <WallpaperLayer />

      {/* ---------- sidebar (desktop) ---------- */}
      <aside
        data-slot="sidebar"
        className={cn(
          'sticky top-0 z-10 hidden h-dvh shrink-0 flex-col overflow-y-auto overflow-x-hidden md:flex',
          'border-r border-sidebar-border bg-sidebar',
          // Width is animated here, not via CSS attribute rules, so the timing
          // matches the rest of the shell exactly.
          'transition-[width] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]',
        )}
        style={{ width: collapsed ? 64 : 232 }}
      >
        <SidebarBrand collapsed={collapsed} />
        {/* Collapse control sits directly under the logo, not at the bottom. */}
        <SidebarCollapse collapsed={collapsed} onToggle={toggleSidebar} />

        <SidebarNav page={page} navigate={navigate} groups={activeGroups} collapsed={collapsed} />

        {/* Tools live at the bottom of the sidebar so the header stays a pure
            title card. */}
        <div className="mt-auto border-t border-sidebar-border p-2">
          <SidebarTools tools={tools} isDark={isDark} onThemeToggle={toggleTheme} collapsed={collapsed} />
        </div>
      </aside>

      {/* ---------- mobile drawer ---------- */}
      <AnimatePresence>
        {mobileNav ? (
          <div className="fixed inset-0 z-50 md:hidden">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="absolute inset-0 bg-black/45 backdrop-blur-[1px]"
              onClick={() => setMobileNav(false)}
            />
            <motion.aside
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: 'spring', stiffness: 380, damping: 34 }}
              className="relative flex h-full w-[258px] flex-col overflow-y-auto border-r border-sidebar-border bg-sidebar shadow-float"
            >
              <div className="flex items-center justify-between pr-1">
                <SidebarBrand collapsed={false} />
                <Button variant="ghost" size="icon" aria-label="关闭菜单" onClick={() => setMobileNav(false)}>
                  <X className="size-4" />
                </Button>
              </div>
              <SidebarNav
                page={page}
                collapsed={false}
                groups={activeGroups}
                navigate={(id) => {
                  navigate(id);
                  setMobileNav(false);
                }}
              />
              <div className="mt-auto border-t border-sidebar-border p-2">
                <SidebarTools tools={tools} isDark={isDark} onThemeToggle={toggleTheme} collapsed={false} />
              </div>
            </motion.aside>
          </div>
        ) : null}
      </AnimatePresence>

      {/* ---------- main column ---------- */}
      <div className="flex min-w-0 flex-1 flex-col">
        <main className="relative z-10 mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-3 p-3 pb-24 md:p-4 md:pb-6">
          {/*
            Title bar and page body share one width, one radius language and the
            same card surface, so the top of the page reads as a single column
            instead of a bar plus a floating heading.
          */}
          <motion.header
            key={`head-${page}`}
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.26, ease: EASE }}
            data-slot="card"
            className="flex items-center gap-3 rounded-xl border border-border/70 bg-card px-4 py-3"
          >
            <Button
              variant="ghost"
              size="icon"
              aria-label="打开菜单"
              className="md:hidden"
              onClick={() => setMobileNav(true)}
            >
              <Menu className="size-4" />
            </Button>

            <div className="min-w-0 flex-1">
              <h1 className="truncate text-[15px] font-semibold tracking-tight">{current?.label ?? '页面'}</h1>
              {current?.description ? (
                <p className="mt-0.5 truncate text-[11.5px] leading-relaxed text-muted-foreground">
                  {current.description}
                </p>
              ) : null}
            </div>

            {/* Mobile keeps the tools inline; the desktop sidebar owns them. */}
            <div className="flex items-center gap-0.5 md:hidden">
              <ThemeToggle isDark={isDark} onToggle={toggleTheme} />
              <Button variant="ghost" size="icon" aria-label="刷新" onClick={refresh}>
                <RefreshCw className={cn('size-4 transition-transform duration-500', refreshing && 'rotate-180')} />
              </Button>
            </div>
          </motion.header>

          {/* Page transition: a short fade + rise, keyed by route so the exiting
              page plays its exit before the next mounts (`mode="wait"`). */}
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={page}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.22, ease: EASE }}
              className="flex flex-col gap-4"
            >
              <Suspense fallback={<PageFallback />}>
                {Page ? <Page /> : <PageFallback />}
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </main>
      </div>

      {/* ---------- mobile tab bar ---------- */}
      <nav className="fixed inset-x-0 bottom-0 z-40 flex gap-0.5 border-t border-border bg-card/95 px-1.5 pb-[calc(6px+env(safe-area-inset-bottom))] pt-1.5 backdrop-blur-md md:hidden">
        {PRIMARY_ITEMS.filter((item) => !hidden.includes(item.id)).map((item) => {
          const Icon = item.icon;
          const active = page === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => navigate(item.id)}
              className={cn(
                'relative flex flex-1 flex-col items-center justify-center gap-0.5 rounded-md px-2 py-1.5 text-[10.5px]',
                'transition-colors duration-200',
                active ? 'text-primary' : 'text-muted-foreground',
              )}
            >
              {/* Shared layoutId slides the pill between tabs instead of
                  toggling a background on each one. */}
              {active ? (
                <motion.span
                  layoutId="tab-pill"
                  className="absolute inset-0 rounded-md bg-[var(--accent-soft)]"
                  transition={{ type: 'spring', stiffness: 420, damping: 32 }}
                />
              ) : null}
              <Icon className="relative size-4" />
              <span className="relative">{item.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}

interface ShellTool {
  id: string;
  label: string;
  icon: ReactNode;
  onClick: (event: MouseEvent<HTMLButtonElement>) => void;
}

/**
 * Sidebar tool row.
 *
 * In the expanded sidebar the buttons are laid out horizontally with labels as
 * tooltips; collapsed, they stack as icons. The theme button is rendered by
 * `ThemeToggle` so its sun/moon cross-fade stays identical wherever it appears.
 */
function SidebarTools({
  tools,
  isDark,
  onThemeToggle,
  collapsed,
}: {
  tools: ShellTool[];
  isDark: boolean;
  onThemeToggle: (event: MouseEvent<HTMLButtonElement>) => void;
  collapsed: boolean;
}) {
  return (
    <div className={cn('flex items-center gap-0.5', collapsed ? 'flex-col' : 'justify-between')}>
      {tools.map((tool) =>
        tool.id === 'theme' ? (
          <ThemeToggle key={tool.id} isDark={isDark} onToggle={onThemeToggle} />
        ) : (
          <Button key={tool.id} variant="ghost" size="icon" aria-label={tool.label} title={tool.label} onClick={tool.onClick}>
            {tool.icon}
          </Button>
        ),
      )}
    </div>
  );
}

/**
 * Sidebar collapse control, pinned under the logo.
 *
 * The icon swaps while rotating 180°, which reads as the same object turning
 * over rather than two different glyphs cross-fading.
 */
function SidebarCollapse({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <div className={cn('shrink-0 px-2.5 pb-1 pt-2', collapsed && 'px-2')}>
      <button
        type="button"
        onClick={onToggle}
        aria-label={collapsed ? '展开侧边栏' : '收起侧边栏'}
        title={collapsed ? '展开侧边栏' : '收起侧边栏'}
        className={cn(
          'nav-item flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-[12px]',
          'text-muted-foreground transition-colors duration-200 hover:bg-accent hover:text-accent-foreground',
          collapsed && 'justify-center px-0',
        )}
      >
        <motion.span
          animate={{ rotate: collapsed ? 180 : 0 }}
          transition={{ duration: 0.32, ease: EASE }}
          className="grid shrink-0 place-items-center"
        >
          <PanelLeftClose className="size-4" />
        </motion.span>
        <span className="nav-label shrink-0 whitespace-nowrap">收起</span>
      </button>
    </div>
  );
}

/**
 * Theme toggle with a cross-fading sun/moon.
 *
 * The two icons are stacked and rotated in opposite directions, so the swap
 * reads as one object turning rather than two icons swapping.
 */
function ThemeToggle({ isDark, onToggle }: { isDark: boolean; onToggle: (event: MouseEvent<HTMLButtonElement>) => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={isDark ? '切换到浅色' : '切换到深色'}
      title={isDark ? '切换到浅色' : '切换到深色'}
      className={cn(
        'relative grid size-9 place-items-center overflow-hidden rounded-md',
        'text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground',
        'active:scale-95',
      )}
    >
      <motion.span
        initial={false}
        animate={{ y: isDark ? 0 : 22, rotate: isDark ? 0 : -90, opacity: isDark ? 1 : 0 }}
        transition={{ duration: 0.28, ease: EASE }}
        className="absolute"
      >
        <Moon className="size-4" />
      </motion.span>
      <motion.span
        initial={false}
        animate={{ y: isDark ? -22 : 0, rotate: isDark ? 90 : 0, opacity: isDark ? 0 : 1 }}
        transition={{ duration: 0.28, ease: EASE }}
        className="absolute"
      >
        <Sun className="size-4" />
      </motion.span>
    </button>
  );
}

function SidebarBrand({ collapsed }: { collapsed: boolean }) {
  const { theme } = useTheme();
  return (
    <div className="flex shrink-0 items-center gap-2.5 overflow-hidden px-3.5" style={{ height: 52 }}>
      <BrandMark size={30} />
      {collapsed ? null : (
        <div className="brand-text min-w-0">
          <div className="truncate text-[13.5px] font-semibold leading-tight">
            {theme.brand?.title || APP_CONFIG.name}
          </div>
          <div className="truncate text-[11.5px] text-muted-foreground">
            {theme.brand?.subtitle || APP_CONFIG.subtitle}
          </div>
        </div>
      )}
    </div>
  );
}

function SidebarNav({
  page,
  navigate,
  groups,
  collapsed,
}: {
  page: string;
  navigate: (id: string) => void;
  groups: NavGroup[];
  collapsed: boolean;
}) {
  return (
    <nav className="flex flex-1 flex-col gap-0.5 px-2.5 pb-3 pt-1">
      {groups.map((group) => (
        <div key={group.group}>
          <div className="nav-group-label px-2 pb-1.5 pt-3 text-[10.5px] font-semibold uppercase tracking-[0.07em] text-muted-foreground">
            {group.group}
          </div>
          {group.items.map((item) => {
            const Icon = item.icon;
            const active = page === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => navigate(item.id)}
                title={collapsed ? item.label : undefined}
                className={cn(
                  'nav-item relative flex w-full items-center gap-2.5 rounded-md border border-transparent px-2.5 py-2 text-left text-[13.5px]',
                  'transition-colors duration-200',
                  collapsed && 'justify-center px-0',
                  active
                    ? 'font-semibold text-foreground'
                    : 'text-muted-foreground hover:bg-accent/60 hover:text-accent-foreground',
                )}
              >
                {/* The active pill is a shared layout element: it slides to the
                    new row instead of popping in. `data-accent-tint` opts it
                    into the soft gradient skin (a full-strength gradient behind
                    body text would be unreadable). */}
                {active ? (
                  <motion.span
                    layoutId="nav-pill"
                    data-accent-tint=""
                    className="absolute inset-0 rounded-md border border-border bg-card shadow-xs"
                    transition={{ type: 'spring', stiffness: 400, damping: 34 }}
                  />
                ) : null}
                <Icon className={cn('relative size-4 shrink-0', active && 'text-primary')} />
                <span className="nav-label relative truncate">{item.label}</span>
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
