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
import { afterEach, expect, it } from 'vitest'

import {
  useSystemConfigStore,
  DEFAULT_CURRENCY_CONFIG,
} from '@/stores/system-config-store'

import { processChartData } from '../charts'

afterEach(() =>
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
)
it('plots CNY amounts on the same scale as its spending tooltip while preserving raw quota and token counts', () => {
  useSystemConfigStore
    .getState()
    .setConfig({
      currency: {
        ...DEFAULT_CURRENCY_CONFIG,
        quotaDisplayType: 'CNY',
        usdExchangeRate: 7,
      },
    })
  const charts = processChartData([
    {
      created_at: 1700000000,
      model_name: 'gpt-6-astra',
      quota: 500000,
      count: 1,
      token_used: 100,
    },
  ])
  const values = (
    charts.spec_line.data as { values: { rawQuota: number; Usage: number }[] }[]
  )[0].values
  expect(values.find((row) => row.rawQuota === 500000)?.Usage).toBe(7)
  expect(charts.totalQuotaDisplay).toMatch(/^¥7(?:\.0+)?$/)
})
it('keeps a single accounting unit visible in CNY chart data', () => {
  useSystemConfigStore
    .getState()
    .setConfig({
      currency: {
        ...DEFAULT_CURRENCY_CONFIG,
        quotaDisplayType: 'CNY',
        usdExchangeRate: 7,
      },
    })
  const charts = processChartData([
    {
      created_at: 1700000000,
      model_name: 'gpt-6-astra',
      quota: 1,
      count: 1,
      token_used: 1,
    },
  ])
  const values = (
    charts.spec_line.data as { values: { rawQuota: number; Usage: number }[] }[]
  )[0].values
  expect(values.find((row) => row.rawQuota === 1)?.Usage).toBeCloseTo(
    0.000014,
    9
  )
  expect(charts.totalQuotaDisplay).toBe('¥0.000014')
})
