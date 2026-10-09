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
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Channels } from '..'
import { getChannelOps, getSub2APIStatus } from '../api'

vi.mock('../api', () => ({ getSub2APIStatus: vi.fn(), getChannelOps: vi.fn() }))
vi.mock('../components/channels-table', () => ({
  ChannelsTable: () => <div>Legacy channel editor</div>,
}))
vi.mock('../components/channels-dialogs', () => ({
  ChannelsDialogs: () => null,
}))
vi.mock('../components/channels-primary-buttons', () => ({
  ChannelsPrimaryButtons: () => null,
}))
vi.mock('../components/channel-health-dialog', () => ({
  ChannelHealthDialog: () => null,
}))
vi.mock('../components/channels-provider', () => ({
  ChannelsProvider: (props: { children: React.ReactNode }) => props.children,
}))

let client: QueryClient
afterEach(() => client?.clear())
function renderPage() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <Channels />
    </QueryClientProvider>
  )
}

describe('upstream management routing', () => {
  it('retains channel management when the server explicitly selects the legacy driver', async () => {
    vi.mocked(getSub2APIStatus).mockResolvedValue({
      enabled: false,
      configured: false,
      admin_url: '',
    })
    vi.mocked(getChannelOps).mockResolvedValue({
      success: true,
      data: { retry_times: 0 },
    })
    renderPage()
    expect(await screen.findByText('Legacy channel editor')).toBeInTheDocument()
    expect(
      screen.queryByRole('link', { name: 'Open Sub2API management' })
    ).not.toBeInTheDocument()
  })

  it('replaces the legacy channel editor when the server enables Sub2API', async () => {
    vi.mocked(getSub2APIStatus).mockResolvedValue({
      enabled: true,
      configured: true,
      admin_url: 'https://supply.example.test',
    })
    renderPage()
    expect(
      await screen.findByRole('link', { name: 'Open Sub2API management' })
    ).toBeInTheDocument()
    expect(screen.queryByText('Legacy channel editor')).not.toBeInTheDocument()
  })

  it('does not expose the legacy editor while mode is unknown or the status request fails', async () => {
    vi.mocked(getSub2APIStatus).mockRejectedValue(
      new Error('Connection unavailable')
    )
    renderPage()
    expect(screen.queryByText('Legacy channel editor')).not.toBeInTheDocument()
    expect(
      await screen.findByRole('button', { name: 'Retry' })
    ).toBeInTheDocument()
    expect(screen.queryByText('Legacy channel editor')).not.toBeInTheDocument()
  })
})
