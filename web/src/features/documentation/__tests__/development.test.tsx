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
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { isDevelopmentSite } from '@/lib/development-site'
import { useAuthStore } from '@/stores/auth-store'
import { useSystemConfigStore } from '@/stores/system-config-store'

import { Documentation } from '..'

vi.mock('@/lib/development-site', () => ({ isDevelopmentSite: vi.fn() }))

let client: QueryClient
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
  vi.mocked(isDevelopmentSite).mockReturnValue(true)
  vi.spyOn(api, 'get').mockImplementation(async (url) => ({
    data: {
      success: true,
      data: url === '/api/notice' ? '' : { register_enabled: true },
    },
  }))
})

afterEach(() => {
  cleanup()
  client.clear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
})

async function renderDocumentation() {
  const router = createRouter({
    routeTree: createRootRoute({ component: Documentation }),
    history: createMemoryHistory({ initialEntries: ['/docs'] }),
  })
  await router.load()
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  await screen.findByRole('tab', { name: 'Codex' })
}

describe('development documentation boundary', () => {
  it('previews the full guide but directs configuration and keys to the live workspace', async () => {
    const user = userEvent.setup()
    await renderDocumentation()
    expect(
      screen.getByText(
        'These instructions are for the live site. Get keys and setup commands only from the live workspace, not this test site.'
      )
    ).toBeVisible()
    expect(
      screen.getByRole('link', { name: 'Official download' })
    ).toBeVisible()
    expect(screen.getByText('Start using it')).toBeVisible()
    expect(
      screen.getByRole('heading', { name: 'Run the setup command' })
    ).toBeVisible()
    await user.click(screen.getByRole('tab', { name: 'WorkBuddy' }))
    await user.click(screen.getByRole('tab', { name: 'macOS' }))
    expect(screen.getByText('Manual connection details')).toBeVisible()
    expect(
      screen.getByText('https://api.realyu.fun/v1/chat/completions')
    ).toBeVisible()
    expect(
      screen.queryByText(
        'Copy your personal or team key from the live My workspace. Never use a test-site key here.'
      )
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: 'Open My workspace' })
    ).toHaveAttribute('href', 'https://api.realyu.fun/dashboard/overview')
    expect(
      screen.getByRole('link', { name: 'Open live site' })
    ).toHaveAttribute('href', 'https://api.realyu.fun/dashboard/overview')
  })

  it('retains official manual WorkBuddy configuration on the live site', async () => {
    vi.mocked(isDevelopmentSite).mockReturnValue(false)
    const user = userEvent.setup()
    await renderDocumentation()
    await user.click(screen.getByRole('tab', { name: 'WorkBuddy' }))
    await user.click(screen.getByRole('tab', { name: 'macOS' }))
    expect(screen.getByText('Manual connection details')).toBeVisible()
    expect(
      screen.getByText('https://api.realyu.fun/v1/chat/completions')
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Copy URL' })).toBeVisible()
    expect(
      screen.queryByRole('link', { name: 'Open live site' })
    ).not.toBeInTheDocument()
  })

  it('does not offer Codex-specific troubleshooting in the WorkBuddy guide', async () => {
    vi.mocked(isDevelopmentSite).mockReturnValue(false)
    const user = userEvent.setup()
    await renderDocumentation()
    expect(
      screen.getByRole('button', {
        name: 'Setup failed or the app still asks you to sign in?',
      })
    ).toBeVisible()
    await user.click(screen.getByRole('tab', { name: 'WorkBuddy' }))
    expect(
      screen.queryByRole('button', {
        name: 'Setup failed or the app still asks you to sign in?',
      })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: 'Official usage guide' })
    ).toHaveAttribute(
      'href',
      'https://www.codebuddy.ai/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Model'
    )
  })
})
