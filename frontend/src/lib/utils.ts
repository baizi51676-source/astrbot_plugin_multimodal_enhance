// SPDX-License-Identifier: MIT
// 源自 Aneko 界面模板（原 AGPL-3.0-or-later），本项目内另以 MIT 授权，见 frontend/LICENSE
// Copyright (c) 2026 Skylar (github.com/baizi51676-source)

import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Merge Tailwind class names, letting later conditional classes win over
 * earlier ones. The standard clsx + tailwind-merge pairing.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
