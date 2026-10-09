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
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'

import { ChannelHealthDialog } from '../channel-health-dialog'

let client: QueryClient
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})
afterEach(() => client.clear())
function renderHealth() {
  render(
    <QueryClientProvider client={client}>
      <ChannelHealthDialog />
    </QueryClientProvider>
  )
}
const rows = [
  {
    id: 1,
    name: 'Active sample',
    routing_state: 'enabled',
    sample_state: 'degraded',
    request_count: 4,
    success_count: 3,
    success_rate: 75,
    avg_latency_ms: 100,
    series: [{ ts: 1790568000, request_count: 4, success_rate: 75 }],
  },
  {
    id: 2,
    name: 'Idle sample',
    routing_state: 'enabled',
    sample_state: 'no_data',
    request_count: 0,
    success_count: 0,
    success_rate: null,
    avg_latency_ms: null,
    series: [],
  },
  {
    id: 3,
    name: 'Manual sample',
    routing_state: 'manually_disabled',
    sample_state: 'healthy',
    request_count: 1,
    success_count: 1,
    success_rate: 100,
    avg_latency_ms: 100,
    series: [],
  },
  {
    id: 4,
    name: 'Quota sample',
    routing_state: 'quota_exhausted',
    sample_state: 'no_data',
    request_count: 0,
    success_count: 0,
    success_rate: null,
    avg_latency_ms: null,
    series: [],
  },
]
describe('channel availability', () => {
  it('distinguishes routing state from observed successes and no data', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({
      data: {
        success: true,
        data: {
          hours: 24,
          observed_at: 1790568000,
          collection_enabled: true,
          channels: rows,
        },
      },
    })
    renderHealth()
    expect(get).not.toHaveBeenCalled()
    await userEvent.click(
      screen.getByRole('button', { name: 'Channel health' })
    )
    expect(await screen.findByText('75.00%')).toBeVisible()
    expect(screen.getByText('Manually disabled')).toBeVisible()
    expect(screen.getByText('Quota exhausted')).toBeVisible()
    expect(screen.getAllByText('No data').length).toBeGreaterThan(0)
    await userEvent.click(screen.getByRole('tab', { name: '7 days' }))
    await waitFor(() =>
      expect(get).toHaveBeenLastCalledWith('/api/channel/health', {
        params: { hours: 168 },
      })
    )
  })
  it('shows collection disabled explicitly', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({
      data: {
        success: true,
        data: {
          hours: 24,
          observed_at: 1790568000,
          collection_enabled: false,
          channels: [],
        },
      },
    })
    renderHealth()
    await userEvent.click(
      screen.getByRole('button', { name: 'Channel health' })
    )
    expect(
      await screen.findByText(
        'Health collection is disabled. Historical data may be outdated.'
      )
    ).toBeVisible()
  })
  it('shows a retry on API rejection instead of an all-healthy state', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({
      data: { success: false, message: 'denied' },
    })
    renderHealth()
    await userEvent.click(
      screen.getByRole('button', { name: 'Channel health' })
    )
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeVisible()
    expect(screen.queryByText('Healthy')).not.toBeInTheDocument()
  })
})
