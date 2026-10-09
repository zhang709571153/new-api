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
// Keep account balances within the cross-database integer storage limit.
export const MAX_TEAM_QUOTA = 2_000_000_000

export function pointsToQuota(
  points: number,
  quotaPerUnit: number,
  currentQuota = 0
): number | null {
  if (
    !Number.isFinite(points) ||
    points <= 0 ||
    !Number.isFinite(quotaPerUnit) ||
    quotaPerUnit <= 0
  ) {
    return null
  }
  const quota = Math.round(points * quotaPerUnit)
  if (
    !Number.isSafeInteger(quota) ||
    quota <= 0 ||
    quota > MAX_TEAM_QUOTA ||
    !Number.isSafeInteger(currentQuota) ||
    currentQuota + quota > MAX_TEAM_QUOTA
  ) {
    return null
  }
  return quota
}
