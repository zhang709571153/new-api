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
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18next from 'i18next'
import { afterEach, beforeAll, beforeEach, expect, test, vi } from 'vitest'

import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'

import { channelSchema } from '../../types'
import { CodexUsageCell } from '../codex-usage-cell'
import { CodexLoginDialog } from '../dialogs/codex-login-dialog'

const channel = channelSchema.parse({
  id: 42,
  type: 57,
  key: '',
  name: 'Pro account',
  status: 3,
  created_time: 1,
  test_time: 0,
  response_time: 0,
  balance_updated_time: 0,
})
let client: QueryClient

beforeAll(async () => {
  await i18next.init({
    lng: 'en',
    fallbackLng: 'en',
    nsSeparator: false,
    resources: { en: { translation: {} } },
  })
})
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  useAuthStore.getState().auth.setUser({ id: 1, username: 'root', role: 100 })
})
afterEach(() => {
  cleanup()
  client.clear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  vi.useRealTimers()
  vi.restoreAllMocks()
})

test('root can update credentials directly from a disabled channel; other admins cannot', async () => {
  const user = userEvent.setup()
  render(
    <QueryClientProvider client={client}>
      <CodexUsageCell channel={channel} sensitiveVisible />
    </QueryClientProvider>
  )
  await user.click(
    screen.getByRole('button', { name: 'Update login credentials' })
  )
  expect(screen.getByRole('button', { name: 'Sign in again' })).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Close' }))
  useAuthStore
    .getState()
    .auth.setUser({ id: 2, username: 'operator', role: 10 })
  await waitFor(() =>
    expect(
      screen.queryByRole('button', { name: 'Update login credentials' })
    ).not.toBeInTheDocument()
  )
})

test.each([true, false])(
  'official login completion automatically saves and tests; enables only a passing channel (%s)',
  async (pass) => {
    vi.useFakeTimers()

    vi.spyOn(api, 'get').mockImplementation(async (url) => {
      if (url === '/api/channel/test/42') return { data: { success: pass } }
      if (url === '/api/channel/42') {
        return { data: { success: true, data: channel } }
      }
      throw new Error('Unexpected request')
    })
    let checks = 0
    const post = vi.spyOn(api, 'post').mockImplementation(async (url) => {
      if (url.endsWith('/start') || url.endsWith('/status')) {
        if (url.endsWith('/status') && ++checks === 1) {
          throw new Error('Temporary disconnect')
        }
        return {
          data: {
            success: true,
            data: {
              id: 'attempt-A',
              status: url.endsWith('/start') ? 'pending' : 'ready',
              email: 'pro@example.test',
              verification_url: 'https://auth.openai.com/codex/device',
              user_code: 'ABCD-1234',
              expires_at: Date.now() / 1000 + 60,
            },
          },
        }
      }
      return { data: { success: true } }
    })
    render(
      <QueryClientProvider client={client}>
        <CodexLoginDialog channel={channel} onClose={vi.fn()} />
      </QueryClientProvider>
    )
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Sign in again' }))
    })
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument()
    expect(screen.getByText('ABCD-1234')).toBeVisible()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(
      screen.getByText(
        'Connection interrupted. Retrying authorization status...'
      )
    ).toBeVisible()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(
      screen.getByText(
        pass
          ? 'Credential updated. Channel tested and enabled.'
          : 'Credential updated, but the channel test failed. Check channel details before enabling it.'
      )
    ).toBeVisible()
    expect(
      screen.queryByText('Waiting for authorization...')
    ).not.toBeInTheDocument()
    expect(
      post.mock.calls.some(([url]) => url === '/api/channel/42/status')
    ).toBe(pass)
    expect(
      post.mock.calls.filter(([url]) => url.endsWith('/complete'))
    ).toHaveLength(1)
    expect(post.mock.calls.some(([url]) => url === '/api/verify')).toBe(false)
  }
)

test('closing a pending login cancels only that attempt and never saves', async () => {
  const user = userEvent.setup()
  const post = vi.spyOn(api, 'post').mockImplementation(async (url) => ({
    data: {
      success: true,
      data: url.endsWith('/start')
        ? {
            id: 'attempt-A',
            status: 'pending',
            email: 'pro@example.test',
            verification_url: 'https://auth.openai.com/codex/device',
            user_code: 'ABCD-1234',
            expires_at: Date.now() / 1000 + 60,
          }
        : null,
    },
  }))
  const view = render(
    <QueryClientProvider client={client}>
      <CodexLoginDialog channel={channel} onClose={vi.fn()} />
    </QueryClientProvider>
  )
  await user.click(screen.getByRole('button', { name: 'Sign in again' }))
  expect(await screen.findByText('ABCD-1234')).toBeVisible()
  view.unmount()
  await waitFor(() =>
    expect(post.mock.calls.some(([url]) => url.endsWith('/cancel'))).toBe(true)
  )
  expect(post.mock.calls.some(([url]) => url.endsWith('/complete'))).toBe(false)
})

test('a login reaching its deadline stops waiting even when the network is down', async () => {
  vi.useFakeTimers()

  vi.spyOn(api, 'post').mockImplementation(async (url) => {
    if (url.endsWith('/start')) {
      return {
        data: {
          success: true,
          data: {
            id: 'expired-attempt',
            status: 'pending',
            expires_at: Date.now() / 1000 + 5,
            verification_url: 'https://auth.openai.com/codex/device',
            user_code: 'ABCD-1234',
          },
        },
      }
    }
    throw new Error('Offline')
  })
  render(
    <QueryClientProvider client={client}>
      <CodexLoginDialog channel={channel} onClose={vi.fn()} />
    </QueryClientProvider>
  )
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Sign in again' }))
  })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(6000)
  })
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Login failed or expired. Close this dialog and try again.'
  )
  expect(
    screen.queryByText('Waiting for authorization...')
  ).not.toBeInTheDocument()
})
