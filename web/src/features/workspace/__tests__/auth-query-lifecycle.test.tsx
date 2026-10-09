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
import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WorkspaceOverview } from '@/features/dashboard/components/overview/workspace-overview'
import { SupplierOverview } from '@/features/team'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'

import { MyTeam } from '../my-team'
import { SupplierTeams } from '../supplier-teams'
import { MyUsage } from '../usage'

let client: QueryClient
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useAuthStore.getState().auth.setUser({ id: 1, username: 'owner', role: 100 })
  vi.spyOn(api, 'get').mockImplementation(async (url) => {
    if (!useAuthStore.getState().auth.user) {
      throw new Error('Anonymous request after logout')
    }
    const data: Record<string, unknown> = {
      '/api/workspace': {
        mode: 'personal',
        balance_usd: 0,
        used_usd: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        requests: 0,
        api_key: null,
        team: null,
      },
      '/api/workspace/team': null,
      '/api/team/workspaces': { teams: [] },
      '/api/team/overview': {
        members: [],
        totals: { quota: 0, requests: 0 },
        quota_per_unit: 500000,
      },
    }
    return { data: { success: true, data: data[url] } }
  })
})
afterEach(() => {
  client.clear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
})
describe('workspace query authentication lifecycle', () => {
  it.each([
    ['workspace', WorkspaceOverview],
    ['team', MyTeam],
    ['supplier teams', SupplierTeams],
    ['supplier accounts', SupplierOverview],
  ])(
    'stops %s queries after logout even while the page remains mounted',
    async (_name, component) => {
      const router = createRouter({
        routeTree: createRootRoute({ component }),
        history: createMemoryHistory({ initialEntries: ['/'] }),
      })
      await router.load()
      render(
        <QueryClientProvider client={client}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      )
      await waitFor(() => expect(api.get).toHaveBeenCalled())
      await waitFor(() => expect(client.isFetching()).toBe(0))
      vi.mocked(api.get).mockClear()
      await act(async () => {
        useAuthStore.getState().auth.reset()
      })
      await act(async () => {
        await client.invalidateQueries()
      })
      await waitFor(() => expect(client.isFetching()).toBe(0))
      expect(api.get).not.toHaveBeenCalled()
    }
  )
  it('replaces the old usage page with personal consumption logs without querying the old endpoint', async () => {
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    const root = createRootRoute()
    const legacyUsage = createRoute({
      getParentRoute: () => root,
      path: '/workspace/usage',
      component: MyUsage,
    })
    const logs = createRoute({
      getParentRoute: () => root,
      path: '/usage-logs/$section',
      component: () => <h1>Usage logs</h1>,
    })
    const history = createMemoryHistory({
      initialEntries: ['/workspace/usage'],
    })
    const router = createRouter({
      routeTree: root.addChildren([legacyUsage, logs]),
      history,
    })
    await router.load()
    render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    )
    expect(
      await screen.findByRole('heading', { name: 'Usage logs' })
    ).toBeVisible()
    expect(router.state.location.pathname).toBe('/usage-logs/common')
    expect(router.state.location.search).toEqual({
      scope: 'self',
      type: ['2'],
    })
    expect(history.length).toBe(1)
    expect(api.get).not.toHaveBeenCalled()
  })
})
