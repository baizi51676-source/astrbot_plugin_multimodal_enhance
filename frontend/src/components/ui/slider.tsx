// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { useEffect, useRef, type CSSProperties } from 'react';
import { cn } from '@/lib/utils';

export interface SliderProps {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
  /** Called once when the drag ends — use it to persist instead of every tick. */
  onCommit?: (value: number) => void;
  disabled?: boolean;
  ariaLabel?: string;
  className?: string;
  /** Formats the accessible value text; defaults to the raw number. */
  formatValue?: (value: number) => string;
}

/** Knob radius including its border, matched to the CSS padding on .ui-slider. */
const KNOB_HALF = 11;

/**
 * Range slider with a filled track.
 *
 * A native `<input type="range">` drives everything — dragging, arrow keys,
 * Home/End and screen-reader announcements all come free and behave exactly as
 * the platform expects. Only the *appearance* is replaced, which is why the real
 * element stays in the DOM instead of being simulated with divs.
 *
 * The filled portion is a `linear-gradient` stop on the input's own background;
 * see `.ui-slider` in `styles/effects.css` for why it is not painted on the
 * `::-webkit-slider-runnable-track` pseudo-element.
 */
export function Slider({
  value,
  min,
  max,
  step = 1,
  onChange,
  onCommit,
  disabled,
  ariaLabel,
  className,
  formatValue,
}: SliderProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  const fraction = max === min ? 0 : (value - min) / (max - min);
  const percent = fraction * 100;

  /*
   * Keep the gradient stop aligned with the knob centre.
   *
   * The input has horizontal padding equal to the knob's overhang and paints its
   * track with `background-clip: content-box`, so the thumb travels from
   * `KNOB_HALF` to `clientWidth - KNOB_HALF`. Expressing the stop against the
   * padded box (rather than the raw percentage) stops the fill from drifting by
   * up to half a knob at the extremes.
   */
  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;

    const apply = () => {
      const width = input.clientWidth;
      if (!width) return;
      const contentWidth = Math.max(0, width - KNOB_HALF * 2);
      const stop = percent <= 0 ? 0 : percent >= 100 ? width : KNOB_HALF + fraction * contentWidth;
      input.style.setProperty('--slider-percent', `${(stop / width) * 100}%`);
    };

    apply();
    // clientWidth is only known after layout and changes with the container, so
    // a ResizeObserver keeps the fill correct inside responsive cards.
    const observer = new ResizeObserver(apply);
    observer.observe(input);
    return () => observer.disconnect();
  }, [fraction, percent]);

  const style: CSSProperties = { ['--slider-percent' as string]: `${percent}%` };

  return (
    <input
      ref={inputRef}
      type="range"
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-valuetext={formatValue ? formatValue(value) : undefined}
      onChange={(event) => onChange(Number(event.target.value))}
      onPointerUp={() => onCommit?.(value)}
      onKeyUp={() => onCommit?.(value)}
      className={cn('ui-slider', className)}
      style={style}
    />
  );
}
