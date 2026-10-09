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
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError } from 'axios'
import i18next from 'i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { getUserSubscriptions } from '@/features/subscriptions/api'
import type { UserSubscriptionRecord } from '@/features/subscriptions/types'
import zh from '@/i18n/locales/zh.json'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'

import {
  SubscriptionSummary,
  UserSubscriptionSummary,
} from '../subscription-summary'

const now = 1791158400
const active: UserSubscriptionRecord = {
  subscription: {
    id: 1,
    user_id: 2,
    plan_id: 1,
    status: 'active',
    purchase_title: 'Monthly Plus',
    start_time: now - 86400,
    end_time: now + 86400,
    amount_total: 0,
    amount_used: 0,
    weekly_amount: 500000,
  },
}
let client: QueryClient
beforeEach(() => {
  vi.spyOn(Date, 'now').mockReturnValue(now * 1000)
  useAuthStore.getState().auth.setUser({ id: 1, username: 'admin', role: 100 })
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})
afterEach(async () => {
  client.clear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  await i18next.changeLanguage('en')
})

it('shows no subscription for empty, expired, cancelled and future records', () => {
  const { rerender } = render(<SubscriptionSummary subscriptions={[]} />)
  expect(screen.getByText('No subscription')).toBeVisible()
  rerender(
    <SubscriptionSummary
      subscriptions={[
        { subscription: { ...active.subscription, id: 2, end_time: now } },
        {
          subscription: { ...active.subscription, id: 3, status: 'cancelled' },
        },
        {
          subscription: { ...active.subscription, id: 4, start_time: now + 1 },
        },
      ]}
    />
  )
  expect(screen.getByText('No subscription')).toBeVisible()
  expect(screen.queryByText('Active')).not.toBeInTheDocument()
})

it('shows the current plan, scope, weekly remaining and expiry for personal and team subscriptions', () => {
  render(
    <SubscriptionSummary
      showScope
      subscriptions={[
        active,
        {
          subscription: {
            ...active.subscription,
            id: 2,
            workspace_team_id: 5,
            purchase_title: 'Team Plus',
          },
        },
      ]}
    />
  )
  expect(screen.getByText('Monthly Plus')).toBeVisible()
  expect(screen.getByText('Team Plus')).toBeVisible()
  expect(screen.getByText('Personal')).toBeVisible()
  expect(screen.getByText('Team')).toBeVisible()
  expect(screen.getAllByText(/Weekly remaining:/)).toHaveLength(2)
  expect(screen.getAllByRole('time')[0]).toHaveAttribute(
    'datetime',
    new Date((now + 86400) * 1000).toISOString()
  )
})

it('updates the empty state and dates when Chinese is selected', async () => {
  i18next.addResourceBundle('zhCN', 'translation', zh.translation)
  const { rerender } = render(<SubscriptionSummary subscriptions={[]} />)
  await act(async () => {
    await i18next.changeLanguage('zhCN')
  })
  expect(screen.getByText('无订阅')).toBeVisible()
  rerender(<SubscriptionSummary subscriptions={[active]} />)
  expect(screen.getByRole('time')).toHaveTextContent(
    new Date((now + 86400) * 1000).toLocaleDateString('zh-CN')
  )
})

it('does not label a loading or failed request as no subscription and supports retry', async () => {
  vi.spyOn(api, 'get')
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValue({ data: { success: true, data: [] } })
  render(
    <QueryClientProvider client={client}>
      <UserSubscriptionSummary userId={2} />
    </QueryClientProvider>
  )
  expect(screen.getByText('Loading...')).toBeVisible()
  expect(screen.queryByText('No subscription')).not.toBeInTheDocument()
  expect(await screen.findByText('Unable to load subscription')).toBeVisible()
  expect(screen.queryByText('No subscription')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('No subscription')).toBeVisible()
})

it('refreshes the displayed plan after an allowance update invalidates the user query', async () => {
  vi.spyOn(api, 'get')
    .mockResolvedValueOnce({ data: { success: true, data: [] } })
    .mockResolvedValue({ data: { success: true, data: [active] } })
  render(
    <QueryClientProvider client={client}>
      <UserSubscriptionSummary userId={2} />
    </QueryClientProvider>
  )
  expect(await screen.findByText('No subscription')).toBeVisible()
  await act(async () => {
    await client.invalidateQueries({ queryKey: ['user-allowance', 2] })
  })
  expect(await screen.findByText('Monthly Plus')).toBeVisible()
})

it('queues a customer list instead of opening every subscription connection at once', async () => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  let activeRequests = 0
  let peakRequests = 0
  vi.spyOn(api, 'get').mockImplementation(async () => {
    activeRequests++
    peakRequests = Math.max(peakRequests, activeRequests)
    await pending
    activeRequests--
    return { data: { success: true, data: [] } }
  })
  render(
    <QueryClientProvider client={client}>
      {[2, 3, 4, 5, 6, 7, 8, 9].map((id) => (
        <UserSubscriptionSummary key={id} userId={id} />
      ))}
    </QueryClientProvider>
  )
  try {
    await waitFor(() => expect(activeRequests).toBeGreaterThan(0))
    expect(peakRequests).toBeLessThanOrEqual(4)
  } finally {
    await act(async () => {
      release()
    })
  }
  await waitFor(() =>
    expect(screen.getAllByText('No subscription')).toHaveLength(8)
  )
})

it('recovers a temporary connection failure without leaving a customer in the error state', async () => {
  vi.spyOn(api, 'get')
    .mockRejectedValueOnce(new AxiosError('Network Error', 'ERR_NETWORK'))
    .mockResolvedValue({ data: { success: true, data: [active] } })
  render(
    <QueryClientProvider client={client}>
      <UserSubscriptionSummary userId={2} />
    </QueryClientProvider>
  )
  expect(
    await screen.findByText('Monthly Plus', {}, { timeout: 5000 })
  ).toBeVisible()
  expect(
    screen.queryByText('Unable to load subscription')
  ).not.toBeInTheDocument()
})

it('stops after two transient retries and never labels an unavailable subscription as empty', async () => {
  const get = vi.spyOn(api, 'get').mockRejectedValue(
    Object.assign(new AxiosError('Bad Gateway'), {
      response: { status: 502, headers: {} },
    })
  )
  render(
    <QueryClientProvider client={client}>
      <UserSubscriptionSummary userId={2} />
    </QueryClientProvider>
  )
  expect(
    await screen.findByText(
      'Unable to load subscription',
      {},
      { timeout: 5000 }
    )
  ).toBeVisible()
  expect(get).toHaveBeenCalledTimes(3)
  expect(screen.queryByText('No subscription')).not.toBeInTheDocument()
})

it('does not retry a forbidden subscription read', async () => {
  const get = vi.spyOn(api, 'get').mockRejectedValue(
    Object.assign(new AxiosError('Forbidden'), {
      response: { status: 403, headers: {} },
    })
  )
  render(
    <QueryClientProvider client={client}>
      <UserSubscriptionSummary userId={2} />
    </QueryClientProvider>
  )
  expect(await screen.findByText('Unable to load subscription')).toBeVisible()
  expect(get).toHaveBeenCalledTimes(1)
})

it('releases failed connection slots and skips a cancelled customer waiting in the queue', async () => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => {
    release = resolve
  })
  const get = vi.spyOn(api, 'get').mockImplementation(async () => {
    await pending
    throw new Error('offline')
  })
  const abort = new AbortController()
  const reads = [2, 3, 4, 5].map((id) => getUserSubscriptions(id))
  reads.push(getUserSubscriptions(6, abort.signal), getUserSubscriptions(7))
  const results = Promise.allSettled(reads)
  abort.abort()
  release()
  await results
  expect(get.mock.calls.map(([url]) => url)).not.toContain(
    '/api/subscription/admin/users/6/subscriptions'
  )
  expect(get).toHaveBeenCalledTimes(5)
  get.mockResolvedValueOnce({ data: { success: true, data: [] } })
  await expect(getUserSubscriptions(8)).resolves.toMatchObject({
    success: true,
    data: [],
  })
})
