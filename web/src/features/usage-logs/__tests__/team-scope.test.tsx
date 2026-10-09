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
  createRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'

import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'

import { usageLogSchema } from '../data/schema'
import { UsageLogs } from '../index'

const clients: QueryClient[] = []

async function renderLogs(
  options: { scope?: string; owner?: boolean; fail?: boolean } = {}
) {
  useAuthStore.getState().auth.setUser({ id: 1, username: 'owner', role: 1 })
  const get = vi.spyOn(api, 'get').mockImplementation(async (url) => {
    const parsed = new URL(String(url), 'https://fixture.invalid')
    let data: unknown = {}
    if (parsed.pathname === '/api/workspace/team') {
      data = {
        team: { id: 10, owner_user_id: options.owner === false ? 2 : 1 },
        members: [],
      }
    } else if (parsed.pathname.endsWith('/stat')) {
      data = { quota: 100, rpm: 2, tpm: 15 }
    } else if (
      parsed.pathname === '/api/workspace/team/logs' ||
      parsed.pathname === '/api/log/self'
    ) {
      if (options.fail && parsed.pathname === '/api/workspace/team/logs') {
        return {
          data: { success: false, message: 'Team request query failed' },
        }
      }
      const team = parsed.pathname === '/api/workspace/team/logs'
      data = {
        total: 1,
        items: [
          usageLogSchema.parse({
            id: 1,
            user_id: team ? 2 : 1,
            username: team ? 'Colleague' : 'owner',
            created_at: 1700000000,
            type: 2,
            content: '',
            model_name: team ? 'gpt-6.1-sol' : 'gpt-6-astra',
            quota: 100,
            prompt_tokens: 12,
            completion_tokens: 3,
          }),
        ],
        members: [
          { user_id: 2, name: 'Colleague', former: false },
          { user_id: 3, name: 'Retired colleague', former: true },
        ],
      }
    } else if (parsed.pathname === '/api/user/self/groups') {
      data = { default: { ratio: 1 } }
    }
    return { data: { success: true, data } }
  })
  const root = createRootRoute()
  const auth = createRoute({ getParentRoute: () => root, id: '_authenticated' })
  const logs = createRoute({
    getParentRoute: () => auth,
    path: '/usage-logs/$section',
    component: UsageLogs,
    validateSearch: (search: Record<string, unknown>) => search,
  })
  const dashboard = createRoute({
    getParentRoute: () => auth,
    path: '/dashboard/$section',
    component: () => null,
  })
  const router = createRouter({
    routeTree: root.addChildren([auth.addChildren([logs, dashboard])]),
    history: createMemoryHistory({
      initialEntries: [`/usage-logs/common?scope=${options.scope ?? 'self'}`],
    }),
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  await screen.findByText('Request details')
  return { router, get }
}

afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
  vi.restoreAllMocks()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
})

test('an owner switches to team requests, filters former members, and returns to their own requests', async () => {
  const { router, get } = await renderLogs()
  await userEvent.click(await screen.findByRole('tab', { name: 'My team' }))
  const member = await screen.findByRole('combobox', { name: 'Member' })
  await screen.findByRole('option', {
    name: /Retired colleague.*Former member/,
  })
  expect(screen.getByRole('tab', { name: 'My team' })).toHaveAttribute(
    'aria-selected',
    'true'
  )
  expect(
    await screen.findByRole('cell', { name: 'Colleague (#2)' })
  ).toBeVisible()
  expect(
    screen.queryByRole('columnheader', { name: 'Channel' })
  ).not.toBeInTheDocument()
  expect(
    screen.queryByRole('tab', { name: 'All users' })
  ).not.toBeInTheDocument()
  await userEvent.selectOptions(member, '3')
  await waitFor(() =>
    expect(router.state.location.search).toMatchObject({
      scope: 'team',
      member: 3,
      page: 1,
    })
  )
  await waitFor(() =>
    expect(
      get.mock.calls.some(
        ([url]) =>
          String(url).startsWith('/api/workspace/team/logs?') &&
          String(url).includes('member_id=3')
      )
    ).toBe(true)
  )
  await waitFor(() =>
    expect(
      get.mock.calls.some(
        ([url]) =>
          String(url).startsWith('/api/workspace/team/logs/stat?') &&
          String(url).includes('member_id=3')
      )
    ).toBe(true)
  )
  await userEvent.click(screen.getByRole('button', { name: /^Search$/ }))
  await waitFor(() => expect(router.state.location.search.member).toBe(3))
  expect(screen.getByRole('button', { name: 'Usage trends' })).toHaveAttribute(
    'href',
    expect.stringContaining('scope=team')
  )
  await userEvent.click(screen.getByRole('tab', { name: 'Only Mine' }))
  await waitFor(() => expect(router.state.location.search.scope).toBe('self'))
  expect(router.state.location.search.member).toBeUndefined()
  await waitFor(() =>
    expect(
      screen.queryByRole('combobox', { name: 'Member' })
    ).not.toBeInTheDocument()
  )
  expect(
    get.mock.calls.some(([url]) => String(url).startsWith('/api/log/self?'))
  ).toBe(true)
})

test('a non-owner cannot open team details through a forged URL', async () => {
  const { get } = await renderLogs({ scope: 'team', owner: false })
  expect(
    await screen.findByText(
      'Team request details are available to the team owner.'
    )
  ).toBeVisible()
  expect(screen.queryByRole('tab', { name: 'My team' })).not.toBeInTheDocument()
  expect(
    get.mock.calls.some(([url]) =>
      String(url).startsWith('/api/workspace/team/logs')
    )
  ).toBe(false)
})

test('a team query failure shows the existing retry state instead of personal data', async () => {
  await renderLogs({ scope: 'team', fail: true })
  expect(await screen.findByText('Failed to load logs')).toBeVisible()
  expect(screen.queryByText('Colleague (#2)')).not.toBeInTheDocument()
  expect(
    within(screen.getByRole('tablist')).getByRole('tab', { name: 'My team' })
  ).toHaveAttribute('aria-selected', 'true')
})
