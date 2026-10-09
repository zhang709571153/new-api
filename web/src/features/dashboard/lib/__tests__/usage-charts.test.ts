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
import { describe, expect, it } from 'vitest'

import type { QuotaDataItem } from '../../types'
import { aggregateUsageCharts } from '../usage-charts'

describe('usage chart metric matrix', () => {
  const data: QuotaDataItem[] = Array.from({ length: 18 }, (_, i) => ({
    model_name: i === 17 ? 'Other' : `Model ${i}`,
    quota: (i + 1) * 500000,
    count: 18 - i,
    token_used: (i + 1) * 17,
    created_at: 1767225600 + (i % 2) * 7200,
  }))
  for (const metric of ['quota', 'requests', 'tokens'] as const) {
    it(`${metric} has identical totals in bar, distribution and trend, including grouped tail models`, () => {
      const result = aggregateUsageCharts(data, metric, 'hour', 'Other')
      const field = {
        quota: 'quota',
        requests: 'count',
        tokens: 'token_used',
      } as const
      const expected = data.reduce(
        (sum, row) => sum + (row[field[metric]] ?? 0),
        0
      )
      expect(result.total).toBe(expected)
      expect(result.totals.reduce((n, v) => n + v.Raw, 0)).toBe(expected)
      expect(result.trend.reduce((n, v) => n + v.Raw, 0)).toBe(expected)
      expect(result.totals).toHaveLength(16)
      expect(new Set(result.totals.map((v) => v.Model)).size).toBe(16)
      const times = [...new Set(result.trend.map((v) => v.Time))]
      expect(times).toHaveLength(3)
      expect(
        result.trend
          .filter((v) => v.Time === times[1])
          .every((v) => v.Raw === 0)
      ).toBe(true)
    })
  }
  it('preserves chronological order across years and all observations across long gaps', () => {
    const result = aggregateUsageCharts(
      [
        { ...data[0], created_at: 1767225600 },
        { ...data[1], created_at: 1735689600 },
      ],
      'requests',
      'hour',
      'Other'
    )
    const times = [...new Set(result.trend.map((v) => v.Time))]
    expect(times).toHaveLength(2)
    expect(times[0]).toBeLessThan(times[1])
    expect(result.trend.reduce((sum, row) => sum + row.Raw, 0)).toBe(35)
  })
  it('handles empty and single-point data without invented usage', () => {
    expect(aggregateUsageCharts([], 'tokens', 'day', 'Other')).toEqual({
      totals: [],
      trend: [],
      total: 0,
    })
    const result = aggregateUsageCharts([data[0]], 'tokens', 'day', 'Other')
    expect(result.trend).toHaveLength(1)
    expect(result.total).toBe(17)
  })
})
