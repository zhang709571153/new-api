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
import { toIntlLocale } from '@/i18n/languages'
import { formatNumber } from '@/lib/format'

/** Display only: sorting and billing continue to use the original token count. */
export function formatWorkspaceTokens(
  value: number | null | undefined,
  language?: string
): string {
  if (value == null || !Number.isFinite(value) || value < 0) return '—'
  const tokens = Math.trunc(value)
  const locale = toIntlLocale(language)
  if (tokens < 10000) return formatNumber(tokens, locale)
  const resolvedLocale = new Intl.NumberFormat(locale).resolvedOptions().locale
  if (resolvedLocale.startsWith('zh')) {
    const traditional =
      new Intl.Locale(resolvedLocale).maximize().script === 'Hant'
    let divisor = 10000
    let unit = traditional ? '萬' : '万'
    if (tokens >= 100000000) {
      divisor = 100000000
      unit = traditional ? '億' : '亿'
    } else if (tokens >= 1000000) {
      divisor = 1000000
      unit = traditional ? '百萬' : '百万'
    }
    return (
      new Intl.NumberFormat(locale, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
        useGrouping: false,
      }).format(tokens / divisor) + unit
    )
  }
  return new Intl.NumberFormat(locale, {
    notation: 'compact',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(tokens)
}
