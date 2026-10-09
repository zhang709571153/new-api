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
import dayjs from '@/lib/dayjs'
import { quotaUnitsToDollars } from '@/lib/format'
import type { TimeGranularity } from '@/lib/time'

import type { QuotaDataItem, UsageMetric } from '../types'

export function aggregateUsageCharts(
  data: QuotaDataItem[],
  metric: UsageMetric,
  granularity: TimeGranularity,
  otherLabel: string,
  quotaUnitValue = quotaUnitsToDollars(1)
) {
  const totals = new Map<string, number>()
  const buckets = new Map<number, Map<string, number>>()
  for (const row of data) {
    const field = {
      quota: 'quota',
      requests: 'count',
      tokens: 'token_used',
    } as const
    const raw = Number(row[field[metric]]) || 0
    const time = dayjs.unix(row.created_at).startOf(granularity).unix()
    if (!Number.isFinite(time) || !Number.isFinite(raw)) continue
    const model = row.model_name || 'Unknown'
    totals.set(model, (totals.get(model) ?? 0) + raw)
    const bucket = buckets.get(time) ?? new Map<string, number>()
    bucket.set(model, (bucket.get(model) ?? 0) + raw)
    buckets.set(time, bucket)
  }
  const ranked = [...totals].sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
  )
  const top = new Set(ranked.slice(0, 15).map(([model]) => model))
  const groups = new Map<
    string,
    { Model: string; Name: string; Raw: number; Value: number }
  >()
  const value = (raw: number) =>
    metric === 'quota' ? raw * quotaUnitValue : raw
  for (const [model, raw] of ranked) {
    const key = top.has(model) ? `model:${model}` : 'other'
    const group = groups.get(key) ?? {
      Model: key,
      Name: top.has(model) ? model : otherLabel,
      Raw: 0,
      Value: 0,
    }
    group.Raw += raw
    group.Value = value(group.Raw)
    groups.set(key, group)
  }
  let times = [...buckets.keys()].sort((a, b) => a - b)
  if (times.length > 1) {
    // Keep every observed bucket, even if filling a long gap would be excessive.
    const filled: number[] = []
    let cursor = dayjs.unix(times[0])
    const end = times.at(-1) ?? 0
    while (cursor.unix() <= end && filled.length <= 744) {
      filled.push(cursor.unix())
      cursor = cursor.add(1, granularity)
    }
    if (cursor.unix() > end) times = filled
  }
  const trend = times.flatMap((time) => {
    const values = new Map<string, number>()
    for (const [model, raw] of buckets.get(time) ?? []) {
      const key = top.has(model) ? `model:${model}` : 'other'
      values.set(key, (values.get(key) ?? 0) + raw)
    }
    return [...groups.values()].map((group) => {
      const raw = values.get(group.Model) ?? 0
      return { ...group, Time: time, Raw: raw, Value: value(raw) }
    })
  })
  return {
    totals: [...groups.values()],
    trend,
    total: [...totals.values()].reduce((a, b) => a + b, 0),
  }
}
