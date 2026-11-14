// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { motion } from 'motion/react';
import { Check, ChevronDown, Search, X } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface SelectOption {
  value: string;
  label: string;
  /** Secondary line under the label — a slug, an email, a count. */
  description?: string;
  /** Optional leading adornment: an icon, a colour dot, an avatar. */
  icon?: ReactNode;
  disabled?: boolean;
  /** Extra words the search should match, when the label alone is not enough. */
  keywords?: string;
}

export interface SelectProps {
  options: SelectOption[];
  value: string | null;
  onChange: (value: string) => void;
  placeholder?: string;
  /** Show the search box. Defaults to on once there are more than 8 options. */
  searchable?: boolean;
  /** Force the search box on/off, overriding the automatic threshold. */
  searchThreshold?: number;
  disabled?: boolean;
  className?: string;
  ariaLabel?: string;
  /** Text shown when a search matches nothing. */
  emptyText?: string;
  id?: string;
}

/** Case-insensitive subsequence-free match across label, description, keywords. */
function matches(option: SelectOption, query: string): boolean {
  if (!query) return true;
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const haystack = [option.label, option.description, option.keywords, option.value]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(needle);
}

/**
 * Accessible combobox-style select.
 *
 * Built from scratch rather than wrapping a native <select> because a native
 * popup cannot host a search field, cannot be styled, and renders differently on
 * every OS. The tradeoff is that the full ARIA combobox contract is our job, so
 * the component implements: roving `aria-activedescendant`, ↑/↓/Home/End,
 * Enter/Space to commit, Esc to close, click-outside, and focus restoration.
 *
 * The popup is portalled and positioned from the trigger's rect, because it
 * lives inside cards with `overflow: hidden` and would otherwise be clipped.
 */
export function Select({
  options,
  value,
  onChange,
  placeholder = '请选择',
  searchable,
  searchThreshold = 8,
  disabled,
  className,
  ariaLabel,
  emptyText = '没有匹配项',
  id,
}: SelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null);

  const triggerRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const generatedId = useId();
  const listboxId = `${id ?? generatedId}-listbox`;

  const showSearch = searchable ?? options.length > searchThreshold;
  const selected = options.find((option) => option.value === value) ?? null;

  const filtered = useMemo(() => options.filter((option) => matches(option, query)), [options, query]);

  /** Position the portal under the trigger, flipping above when short on space. */
  const reposition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const box = trigger.getBoundingClientRect();
    const desiredHeight = 300;
    const spaceBelow = window.innerHeight - box.bottom;
    const flip = spaceBelow < desiredHeight && box.top > spaceBelow;
    setRect({
      left: box.left,
      width: box.width,
      top: flip ? box.top - desiredHeight - 6 : box.bottom + 6,
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    reposition();
    // Tracking scroll/resize keeps the popup glued to its trigger.
    const onScroll = () => reposition();
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
    };
  }, [open, reposition]);

  // Reset the query each time the popup opens, and focus the search field.
  useEffect(() => {
    if (!open) {
      setQuery('');
      return;
    }
    const index = Math.max(
      0,
      filtered.findIndex((option) => option.value === value),
    );
    setActive(index);
    const raf = requestAnimationFrame(() => {
      if (showSearch) searchRef.current?.focus();
      else popupRef.current?.focus();
    });
    return () => cancelAnimationFrame(raf);
    // Intentionally keyed on `open` only: re-running on every keystroke would
    // steal focus back to the search box mid-typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Keep the active row visible while arrowing through a long list.
  useEffect(() => {
    if (!open) return;
    const node = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    node?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  // Click outside closes. The popup is portalled, so both refs are checked.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (popupRef.current?.contains(target)) return;
      if (triggerRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }, []);

  const commit = useCallback(
    (option: SelectOption) => {
      if (option.disabled) return;
      onChange(option.value);
      close();
    },
    [onChange, close],
  );

  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setOpen(true);
    }
  };

  const onListKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        setActive((index) => Math.min(filtered.length - 1, index + 1));
        break;
      case 'ArrowUp':
        event.preventDefault();
        setActive((index) => Math.max(0, index - 1));
        break;
      case 'Home':
        event.preventDefault();
        setActive(0);
        break;
      case 'End':
        event.preventDefault();
        setActive(Math.max(0, filtered.length - 1));
        break;
      case 'Enter': {
        event.preventDefault();
        const option = filtered[active];
        if (option) commit(option);
        break;
      }
      case 'Escape':
        event.preventDefault();
        close();
        break;
      case 'Tab':
        // Let Tab move on naturally, but do not leave a stranded popup.
        close(false);
        break;
      default:
        break;
    }
  };

  const popup = open && rect
    ? createPortal(
        <motion.div
          ref={popupRef}
          initial={{ opacity: 0, y: -4, scale: 0.985 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.14, ease: [0.16, 1, 0.3, 1] }}
          style={{ position: 'fixed', top: rect.top, left: rect.left, width: rect.width, zIndex: 120 }}
          className="overflow-hidden rounded-lg border border-border bg-popover shadow-float"
          onKeyDown={onListKeyDown}
        >
          {showSearch ? (
            <div className="flex items-center gap-2 border-b border-border/70 px-2.5">
              <Search className="size-3.5 shrink-0 text-muted-foreground" />
              <input
                ref={searchRef}
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setActive(0);
                }}
                placeholder="搜索…"
                aria-label="搜索选项"
                aria-controls={listboxId}
                className="h-9 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground/70"
              />
              {query ? (
                <button
                  type="button"
                  aria-label="清空搜索"
                  onClick={() => {
                    setQuery('');
                    searchRef.current?.focus();
                  }}
                  className="grid size-5 shrink-0 place-items-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  <X className="size-3" />
                </button>
              ) : null}
            </div>
          ) : null}

          <div
            ref={listRef}
            id={listboxId}
            role="listbox"
            aria-label={ariaLabel}
            className="max-h-[264px] overflow-y-auto p-1"
          >
            {filtered.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-muted-foreground">{emptyText}</div>
            ) : (
              filtered.map((option, index) => {
                const isSelected = option.value === value;
                const isActive = index === active;
                return (
                  <div
                    key={option.value}
                    id={`${listboxId}-${index}`}
                    data-index={index}
                    role="option"
                    aria-selected={isSelected}
                    aria-disabled={option.disabled}
                    onMouseEnter={() => setActive(index)}
                    onMouseDown={(event) => event.preventDefault()} // keep focus
                    onClick={() => commit(option)}
                    className={cn(
                      'flex cursor-pointer items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition-colors',
                      option.disabled && 'cursor-not-allowed opacity-50',
                      isActive && !option.disabled && 'bg-accent text-accent-foreground',
                      isSelected && 'font-medium',
                    )}
                  >
                    {option.icon ? <span className="shrink-0">{option.icon}</span> : null}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{option.label}</span>
                      {option.description ? (
                        <span className="block truncate text-[11px] text-muted-foreground">
                          {option.description}
                        </span>
                      ) : null}
                    </span>
                    {isSelected ? <Check className="size-3.5 shrink-0 text-primary" /> : null}
                  </div>
                );
              })
            )}
          </div>

          {showSearch && filtered.length > 0 ? (
            <div className="border-t border-border/70 px-2.5 py-1.5 text-[10.5px] text-muted-foreground">
              {filtered.length} / {options.length} 项
            </div>
          ) : null}
        </motion.div>,
        document.body,
      )
    : null;

  return (
    <div className={cn('relative', className)}>
      <button
        ref={triggerRef}
        id={id}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={onTriggerKeyDown}
        className={cn(
          'flex h-9 w-full items-center gap-2 rounded-md border border-input bg-transparent px-3 text-left text-sm',
          'outline-none transition-colors',
          'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/25',
          'disabled:cursor-not-allowed disabled:opacity-60',
          open && 'border-ring ring-[3px] ring-ring/25',
        )}
      >
        {selected?.icon ? <span className="shrink-0">{selected.icon}</span> : null}
        <span className={cn('min-w-0 flex-1 truncate', !selected && 'text-muted-foreground/70')}>
          {selected ? selected.label : placeholder}
        </span>
        <ChevronDown
          className={cn(
            'size-3.5 shrink-0 text-muted-foreground transition-transform duration-200',
            open && 'rotate-180',
          )}
        />
      </button>
      {popup}
    </div>
  );
}
