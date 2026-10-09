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
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'

import { PerformanceHealthPanel } from '../performance-health-panel'

let client: QueryClient
const metrics = {
  success: true,
  data: {
    summary: { success_rate: 100, avg_latency_ms: 1000, avg_tps: 10 },
    models: [
      {
        model_name: 'available-model',
        success_rate: 100,
        avg_latency_ms: 1000,
        avg_tps: 10,
      },
    ],
  },
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})
afterEach(() => {
  cleanup()
  client.clear()
})

describe('health status refresh', () => {
  it('shows unknown metrics after a failed refresh and restores them after retry succeeds', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'get')
      .mockResolvedValueOnce({ data: metrics })
      .mockRejectedValueOnce(new Error('Metrics temporarily unavailable'))
      .mockResolvedValueOnce({ data: metrics })
    render(
      <QueryClientProvider client={client}>
        <PerformanceHealthPanel showAllModels />
      </QueryClientProvider>
    )
    expect(await screen.findByText('available-model')).toBeVisible()
    await act(async () =>
      client.invalidateQueries({ queryKey: ['perf-metrics-summary', 24] })
    )
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeVisible()
    expect(screen.queryByText('available-model')).not.toBeInTheDocument()
    expect(screen.queryByText('100.00%')).not.toBeInTheDocument()
    expect(screen.queryByText('0.00%')).not.toBeInTheDocument()
    expect(screen.getAllByText('—')).toHaveLength(3)
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('available-model')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Retry' })
    ).not.toBeInTheDocument()
  })
})
