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
import type { LogOtherData } from '../types'

/** Read the historical settlement ratio; never recalculate from current settings. */
export function getRecordedGroupRatio(
  other: LogOtherData | null
): number | null {
  const userRatio = other?.user_group_ratio
  if (userRatio != null && userRatio >= 0 && Number.isFinite(userRatio)) {
    return userRatio
  }
  const groupRatio = other?.group_ratio
  if (groupRatio != null && groupRatio >= 0 && Number.isFinite(groupRatio)) {
    return groupRatio
  }
  return null
}

export function getDisplayedPriceRatio(
  other: LogOtherData | null,
  isAdmin: boolean
): number {
  if (isAdmin) return 1
  return getRecordedGroupRatio(other) ?? 1
}

/** Recorded request rules affect model charges, but not separate tool charges. */
export function getDisplayedModelPriceRatio(
  other: LogOtherData | null,
  isAdmin: boolean
): number {
  if (isAdmin) return 1
  return (other?.request_rules ?? []).reduce(
    (ratio, rule) => {
      if (
        !rule.matched ||
        !Number.isFinite(rule.multiplier) ||
        rule.multiplier < 0
      ) {
        return ratio
      }
      return ratio * rule.multiplier
    },
    getDisplayedPriceRatio(other, false)
  )
}
