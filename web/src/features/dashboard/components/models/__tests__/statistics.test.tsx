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
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'

import { LogStatCards } from '../log-stat-cards'
import { TeamUsage } from '../team-usage'

afterEach(() => {
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  vi.restoreAllMocks()
})

test('an administrator viewing personal trends uses the personal endpoint', async () => {
  useAuthStore.getState().auth.setUser({ id: 1, role: 10, username: 'admin' })
  const get = vi.spyOn(api, 'get').mockResolvedValue({
    data: {
      success: true,
      data: [{ created_at: 1, quota: 500000, count: 12, token_used: 1200 }],
    },
  })
  render(<LogStatCards viewScope='self' />)
  expect(await screen.findByText('1,200')).toBeVisible()
  expect(get).toHaveBeenCalledWith('/api/data/self', expect.anything())
  expect(get).not.toHaveBeenCalledWith('/api/data', expect.anything())
})

test('a regular user cannot select the administrator statistics endpoint', async () => {
  useAuthStore.getState().auth.setUser({ id: 2, role: 1, username: 'member' })
  const get = vi
    .spyOn(api, 'get')
    .mockResolvedValue({ data: { success: true, data: [] } })
  render(<LogStatCards viewScope='all' />)
  await waitFor(() =>
    expect(get).toHaveBeenCalledWith('/api/data/self', expect.anything())
  )
  expect(get).not.toHaveBeenCalledWith('/api/data', expect.anything())
})

test.each(['business', 'http'])(
  'failed %s statistics report failure to charts instead of a successful zero bill',
  async (failure) => {
    const get = vi.spyOn(api, 'get')
    if (failure === 'business') {
      get.mockResolvedValue({
        data: { success: false, message: 'Statistics unavailable' },
      })
    } else {
      get.mockRejectedValue(new Error('HTTP 500'))
    }
    const onDataUpdate = vi.fn()
    render(<LogStatCards viewScope='self' onDataUpdate={onDataUpdate} />)
    expect(await screen.findAllByText('--')).toHaveLength(5)
    expect(screen.queryByText('$0')).not.toBeInTheDocument()
    expect(onDataUpdate).toHaveBeenLastCalledWith([], false, true)
  }
)

vi.mock('../model-charts', () => ({
  ModelCharts: ({
    data,
    title,
  }: {
    data: { model_name?: string }[]
    title?: string
  }) => (
    <div data-testid='member-chart'>
      {title}: {data.map((row) => row.model_name).join(',')}
    </div>
  ),
}))
vi.mock('../consumption-distribution-chart', () => ({
  ConsumptionDistributionChart: () => null,
}))

test('team trends compare individual members, filter models and retain former members', async () => {
  useAuthStore.getState().auth.setUser({ id: 3, username: 'owner', role: 1 })
  const get = vi.spyOn(api, 'get').mockResolvedValue({
    data: {
      success: true,
      data: [
        {
          user_id: 3,
          username: 'Owner',
          model_name: 'gpt-6-astra',
          created_at: 100,
          count: 1,
          token_used: 12,
          quota: 100,
        },
        {
          user_id: 4,
          username: 'Alice',
          model_name: 'gpt-6-sol',
          created_at: 100,
          count: 2,
          token_used: 24,
          quota: 200,
        },
      ],
    },
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const root = createRootRoute({
    component: () => (
      <TeamUsage
        filters={{
          start_timestamp: new Date(1000),
          end_timestamp: new Date(200000),
        }}
        team={{ team: { id: 5, owner_user_id: 3, name: 'Team' }, members: [] }}
      />
    ),
  })
  const router = createRouter({
    routeTree: root,
    history: createMemoryHistory(),
  })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  await waitFor(() =>
    expect(screen.getByTestId('member-chart')).toHaveTextContent(
      'Alice (Former member)'
    )
  )
  expect(get).toHaveBeenCalledWith(
    '/api/workspace/team/usage',
    expect.anything()
  )
  fireEvent.change(screen.getByLabelText('Member'), { target: { value: '4' } })
  expect(screen.getByTestId('member-chart')).not.toHaveTextContent('Owner')
  fireEvent.change(screen.getByLabelText('Compare by'), {
    target: { value: 'models' },
  })
  expect(screen.getByTestId('member-chart')).toHaveTextContent('gpt-6-sol')
  expect(screen.getByTestId('member-chart')).not.toHaveTextContent(
    'gpt-6-astra'
  )
  client.clear()
})
