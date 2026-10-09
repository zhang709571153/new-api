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
import { formatNumber } from '@/lib/format'

// These contracts are denominated in CNY. Display-currency preferences must
// never reinterpret the checkout price or the snapshotted credit denomination.
export function formatCNY(value: number, locale?: string): string {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}
export function formatBillingDays(seconds: number, locale?: string): string {
  return formatNumber(Math.floor(seconds / 864) / 100, locale)
}
