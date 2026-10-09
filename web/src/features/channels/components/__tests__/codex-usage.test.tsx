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
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18next from 'i18next'
import { afterEach, beforeAll, beforeEach, expect, test, vi } from 'vitest'

import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'

import { codexUsagePercent } from '../../lib/codex-usage'
import { channelSchema } from '../../types'
import { CodexUsageCell } from '../codex-usage-cell'

let client: QueryClient
const channel = channelSchema.parse({
  id: 42,
  type: 57,
  key: '',
  name: 'Subscription account',
  status: 1,
  created_time: 1,
  test_time: 0,
  response_time: 0,
  balance_updated_time: 0,
})
const usage = {
  success: true,
  upstream_status: 200,
  data: {
    email: 'ops@example.test',
    plan_type: 'pro',
    rate_limit: {
      allowed: true,
      primary_window: {
        used_percent: 57,
        limit_window_seconds: 604800,
        reset_at: 4102444800,
      },
      secondary_window: null,
    },
    model_usage: { 'gpt-6-astra': { available: false } },
  },
}

beforeAll(async () => {
  await i18next.init({
    lng: 'en',
    fallbackLng: 'en',
    nsSeparator: false,
    resources: { en: { translation: {} } },
  })
})

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useAuthStore.getState().auth.setUser({ id: 1, username: 'admin', role: 10 })
})
afterEach(() => {
  cleanup()
  client.clear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  vi.restoreAllMocks()
})
function renderCell(status = 1, sensitiveVisible = true, multiKey = false) {
  return render(
    <QueryClientProvider client={client}>
      <CodexUsageCell
        channel={{
          ...channel,
          status,
          channel_info: { ...channel.channel_info, is_multi_key: multiKey },
        }}
        sensitiveVisible={sensitiveVisible}
      />
    </QueryClientProvider>
  )
}

test('shows a single reported weekly window with used, remaining and reset date', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({ data: usage })
  renderCell()
  expect(await screen.findByText('Used: 57%')).toBeVisible()
  expect(screen.getByText('Remaining: 43%')).toBeVisible()
  expect(screen.getByText('ops@example.test')).toBeVisible()
  expect(screen.getByText('pro')).toBeVisible()
  expect(
    screen.getByRole('progressbar', { name: 'Weekly Window' })
  ).toHaveAttribute('aria-valuenow', '57')
  expect(screen.queryByText('5-Hour Window')).not.toBeInTheDocument()
  expect(screen.getByText(/2100/)).toBeVisible()
})

test('does not infer zero usage or a full remaining allowance when the percentage is absent', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({
    data: {
      success: true,
      data: {
        rate_limit: { primary_window: { limit_window_seconds: 604800 } },
      },
    },
  })
  renderCell()
  expect(await screen.findByText('Usage unavailable')).toBeVisible()
  expect(screen.queryByText(/100%/)).not.toBeInTheDocument()
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Details' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).queryByText('0%')).not.toBeInTheDocument()
})

test('shows credential failure without presenting old allowance as available', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({
    data: { success: false, upstream_status: 401 },
  })
  renderCell()
  expect(await screen.findByText('Credentials need updating')).toBeVisible()
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  expect(screen.queryByText(/100%/)).not.toBeInTheDocument()
})

test('does not automatically query disabled or multi-key accounts', async () => {
  const get = vi.spyOn(api, 'get').mockResolvedValue({ data: usage })
  const first = renderCell(0)
  expect(screen.getByText('Not queried')).toBeVisible()
  expect(get).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Details' }))
  await waitFor(() => expect(get).toHaveBeenCalledTimes(1))
  first.unmount()
  renderCell(1, true, true)
  expect(
    screen.getByText('Separate accounts are required for usage monitoring')
  ).toBeVisible()
  expect(get).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  expect(screen.queryByText('ops@example.test')).not.toBeInTheDocument()
})

test('hiding sensitive channel data prevents quota requests and display', () => {
  client.setQueryData(['codex-channel-usage', 1, 42], usage)
  const get = vi.spyOn(api, 'get')
  renderCell(1, false)
  expect(screen.getByText('••••')).toBeVisible()
  expect(get).not.toHaveBeenCalled()
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
  expect(screen.queryByText('ops@example.test')).not.toBeInTheDocument()
})

test('reuses the loaded snapshot in details and prevents immediate duplicate refreshes', async () => {
  const get = vi.spyOn(api, 'get').mockResolvedValue({ data: usage })
  renderCell()
  await screen.findByText('Used: 57%')
  expect(
    screen.getByRole('button', { name: 'Refresh subscription usage' })
  ).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Details' }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText('gpt-6-astra')).toBeVisible()
  expect(within(dialog).getByText('Limited')).toBeVisible()
  expect(get).toHaveBeenCalledTimes(1)
})

test('stale cached snapshots are labeled without implying current capacity', async () => {
  client.setQueryData(['codex-channel-usage', 1, 42], usage, {
    updatedAt: Date.now() - 6 * 60 * 1000,
  })
  const get = vi.spyOn(api, 'get')
  renderCell(0)
  expect(screen.getByText('Usage data is stale')).toBeVisible()
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  expect(get).not.toHaveBeenCalled()
})

test('a failed refresh removes old percentages instead of showing a successful zero', async () => {
  const get = vi.spyOn(api, 'get').mockResolvedValue({ data: usage })
  renderCell()
  await screen.findByText('Used: 57%')
  get.mockRejectedValue(new Error('Network unavailable'))
  await act(async () => {
    await client.invalidateQueries({ queryKey: ['codex-channel-usage'] })
  })
  expect(await screen.findByText('Failed to fetch usage')).toBeVisible()
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
})

test('only a valid numeric zero represents fully unused capacity', () => {
  expect(codexUsagePercent(0)).toBe(0)
  expect(codexUsagePercent(null)).toBeNull()
  expect(codexUsagePercent(undefined)).toBeNull()
  expect(codexUsagePercent('')).toBeNull()
  expect(codexUsagePercent(-1)).toBeNull()
  expect(codexUsagePercent(101)).toBeNull()
})
