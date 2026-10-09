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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'

import { ApiAccessSetupStatus } from '../api-access-setup'

let client: QueryClient
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useAuthStore.getState().auth.setUser({ id: 1, username: 'member', role: 1 })
})
afterEach(() => {
  client.clear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  vi.restoreAllMocks()
})

function mount(scope: 'personal' | 'team' = 'personal') {
  return render(
    <QueryClientProvider client={client}>
      <ApiAccessSetupStatus scope={scope} />
    </QueryClientProvider>
  )
}

describe('API access preparation status', () => {
  it('keeps legacy mode free of provisioning messages', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({
      data: { success: true, data: { enabled: false, status: 'disabled' } },
    })
    mount()
    await waitFor(() => expect(client.isFetching()).toBe(0))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('shows pending then ready only after a confirmed server result', async () => {
    const fetch = vi.spyOn(api, 'get').mockResolvedValueOnce({
      data: { success: true, data: { enabled: true, status: 'pending', reason: 'queued' } },
    }).mockResolvedValue({
      data: { success: true, data: { enabled: true, status: 'ready', reason: 'provisioned' } },
    })
    mount('team')
    expect(await screen.findByText('Preparing API access. This page will update automatically.')).toBeVisible()
    expect(fetch).toHaveBeenCalledWith('/api/workspace/sub2api/status?scope=team')
    await act(async () => { await client.invalidateQueries() })
    expect(await screen.findByText('API access setup complete.')).toBeVisible()
  })

  it.each(['operator_action_required', 'worker_unavailable'])('shows an actionable message for %s', async (reason) => {
    vi.spyOn(api, 'get').mockResolvedValue({
      data: { success: true, data: { enabled: true, status: 'error', reason } },
    })
    mount()
    expect(await screen.findByText('API access setup needs administrator attention.')).toBeVisible()
    expect(screen.queryByText('API access setup complete.')).not.toBeInTheDocument()
  })

  it('does not show stale ready data after switching users or logging out', async () => {
    const fetch = vi.spyOn(api, 'get').mockResolvedValueOnce({
      data: { success: true, data: { enabled: true, status: 'ready' } },
    }).mockResolvedValue({
      data: { success: true, data: { enabled: true, status: 'pending', reason: 'queued' } },
    })
    mount()
    expect(await screen.findByText('API access setup complete.')).toBeVisible()
    await act(async () => { useAuthStore.getState().auth.setUser({ id: 2, username: 'other', role: 1 }) })
    expect(await screen.findByText('Preparing API access. This page will update automatically.')).toBeVisible()
    expect(screen.queryByText('API access setup complete.')).not.toBeInTheDocument()
    await act(async () => { useAuthStore.getState().auth.reset() })
    fetch.mockClear()
    await act(async () => { await client.invalidateQueries() })
    expect(fetch).not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('does not leak backend error text or infer readiness when status lookup fails', async () => {
    vi.spyOn(api, 'get').mockRejectedValue(new Error('private upstream diagnostic'))
    mount()
    expect(await screen.findByText('Unable to check API access setup.')).toBeVisible()
    expect(screen.queryByText('private upstream diagnostic')).not.toBeInTheDocument()
    expect(screen.queryByText('API access setup complete.')).not.toBeInTheDocument()
  })
})
