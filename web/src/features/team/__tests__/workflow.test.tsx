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
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { useSystemConfigStore } from '@/stores/system-config-store'

import { SupplierOverview } from '..'
import type { TeamMember } from '../api'

let client: QueryClient
const alice: TeamMember = {
  id: 3,
  username: 'alice',
  display_name: 'Alice',
  status: 1,
  quota: 500000,
  used_quota: 100,
  request_count: 1,
  period_quota: 100,
  period_requests: 1,
  prompt_tokens: 80,
  completion_tokens: 20,
  last_request_at: 0,
}
const bob: TeamMember = {
  ...alice,
  id: 4,
  username: 'bob',
  display_name: 'Bob',
  period_quota: 300,
}
let members: TeamMember[]
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  members = [alice, bob]
  window.localStorage.clear()
  useAuthStore.getState().auth.setUser({ id: 1, username: 'admin', role: 100 })
  vi.spyOn(api, 'get').mockImplementation(async (url) => {
    if (url.startsWith('/api/subscription/admin/users/')) {
      return { data: { success: true, data: [] } }
    }
    if (url === '/api/team/workspaces') {
      return { data: { success: true, data: { teams: [] } } }
    }
    return {
      data: {
        success: true,
        data: {
          members,
          totals: {
            quota: 400,
            requests: 2,
            prompt_tokens: 160,
            completion_tokens: 40,
          },
          start_timestamp: 1,
          end_timestamp: 2,
          quota_per_unit: 500000,
        },
      },
    }
  })
})
afterEach(() => {
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
  client.clear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  window.localStorage.clear()
})
async function renderTeam() {
  const router = createRouter({
    routeTree: createRootRoute({ component: SupplierOverview }),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  await router.load()
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  await screen.findByRole('link', { name: 'Manage users' })
}
describe('team allowance workflow', () => {
  it('sorts customer usage, searches IDs and opens matching filtered request logs without mutations', async () => {
    const user = userEvent.setup()
    await renderTeam()
    await screen.findByText('Alice')
    expect(within(screen.getAllByRole('row')[1]).getByText('Bob')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Actions' })
    ).not.toBeInTheDocument()
    await user.type(
      screen.getByRole('textbox', { name: 'Search members' }),
      'alice'
    )
    expect(screen.queryByText('Bob')).not.toBeInTheDocument()
    const href = new URL(
      screen.getByRole('link', { name: 'Alice' }).getAttribute('href') ?? '',
      'https://fixture.invalid'
    )
    expect(href.pathname).toBe('/usage-logs/common')
    expect(href.searchParams.get('username')).toBe('alice')
    expect(href.searchParams.get('startTime')).toBe('1000')
    expect(href.searchParams.get('endTime')).toBe('1999')
    expect(screen.getByRole('columnheader', { name: 'ID' })).toBeVisible()
  })
  it('shows a failed request without presenting an empty team or enabling provisioning', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('usage unavailable'))
    await renderTeam()
    expect(await screen.findByText('usage unavailable')).toBeVisible()
    expect(screen.queryByText('No members to display')).not.toBeInTheDocument()
  })
  it('keeps mutation actions disabled for a read-only team administrator', async () => {
    useAuthStore.getState().auth.setUser({
      id: 2,
      username: 'reader',
      role: 10,
      permissions: {
        admin_permissions: { team: { read: true, write: false } },
      },
    })
    await renderTeam()
    await screen.findByText('Alice')
    expect(
      screen.queryByRole('button', { name: 'Edit subscription' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Actions' })
    ).not.toBeInTheDocument()
  })
  it('renders an explicit empty state when the member search has no results', async () => {
    const user = userEvent.setup()
    await renderTeam()
    await screen.findByText('Alice')
    await user.type(
      screen.getByRole('textbox', { name: 'Search members' }),
      'missing'
    )
    expect(await screen.findByText('No members to display')).toBeVisible()
  })
  it('shows subscriptions read-only and offers a separate user management entry', async () => {
    await renderTeam()
    const row = await screen.findByRole('row', { name: /Alice/ })
    expect(await within(row).findByText('No subscription')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Edit subscription' })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Manage users' })).toHaveAttribute(
      'href',
      '/users'
    )
  })
  it('separates account filters from collapsible team attribution', async () => {
    await renderTeam()
    await userEvent.click(screen.getByRole('tab', { name: 'Team breakdown' }))
    expect(await screen.findByText('No teams to display')).toBeVisible()
    expect(
      screen.queryByRole('tab', { name: 'Last 24 hours' })
    ).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: 'Account usage' }))
    expect(await screen.findByText('Alice')).toBeVisible()
  })
})
