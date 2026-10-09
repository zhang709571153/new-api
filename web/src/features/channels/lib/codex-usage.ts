/*
Copyright (C) 2023-2026 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/
import type { TFunction } from 'i18next'

import { formatNumber } from '@/lib/format'

export type CodexRateLimitWindow = {
  used_percent?: number | null
  reset_at?: number | null
  reset_after_seconds?: number | null
  limit_window_seconds?: number | null
}

export function codexUsagePercent(value: unknown): number | null {
  if (
    typeof value !== 'number' ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > 100
  ) {
    return null
  }
  return value
}

export function codexUsageWindows(
  source: unknown
): Array<{ key: string; window: CodexRateLimitWindow }> {
  if (!source || typeof source !== 'object') return []
  const payload = source as Record<string, unknown>
  const limits = payload.rate_limit ?? payload
  if (!limits || typeof limits !== 'object') return []
  return ['primary_window', 'secondary_window'].flatMap((key) => {
    const window = (limits as Record<string, unknown>)[key]
    if (
      !window ||
      typeof window !== 'object' ||
      Array.isArray(window) ||
      Object.keys(window).length === 0
    ) {
      return []
    }
    return [{ key, window: window as CodexRateLimitWindow }]
  })
}

export function codexUsageWindowTitle(
  window: CodexRateLimitWindow,
  t: TFunction,
  locale?: Intl.LocalesArgument
): string {
  const seconds = window.limit_window_seconds
  if (seconds === 18000) return t('5-Hour Window')
  if (seconds === 604800) return t('Weekly Window')
  if (
    typeof seconds !== 'number' ||
    !Number.isFinite(seconds) ||
    seconds <= 0
  ) {
    return t('Usage window')
  }
  return t('{{hours}}-hour window', {
    hours: formatNumber(seconds / 3600, locale),
  })
}
