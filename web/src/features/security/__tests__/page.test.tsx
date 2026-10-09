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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SignUpForm } from '@/features/auth/sign-up/components/sign-up-form'
import { Profile } from '@/features/profile'
import type { UserProfile } from '@/features/profile/types'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'

import { Security } from '../index'

const profile: UserProfile = {
  id: 1,
  username: 'alice',
  display_name: 'Alice',
  role: 1,
  group: 'default',
  quota: 1000000,
  used_quota: 0,
  request_count: 0,
  status: 1,
  aff_count: 0,
  aff_quota: 0,
  aff_history_quota: 0,
  created_time: 0,
  setting: JSON.stringify({
    notify_type: 'email',
    quota_warning_threshold: 500000,
  }),
}

beforeEach(() => {
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
  })
  vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined)
  useAuthStore
    .getState()
    .auth.setUser({ ...profile, permissions: { sidebar_settings: false } })
  vi.spyOn(api, 'get').mockImplementation(async (url) => {
    if (url === '/api/user/token/status') {
      return {
        data: {
          success: true,
          data: {
            exists: false,
            token_ref: '',
            created_at: null,
            last_used_at: null,
            last_used_ip: '',
          },
        },
      }
    }
    if (url === '/api/user/self') {
      return { data: { success: true, data: profile } }
    }
    if (url === '/api/user/passkey') {
      return { data: { success: true, data: { enabled: false } } }
    }
    if (url === '/api/user/2fa/status') {
      return {
        data: {
          success: true,
          data: { enabled: false, locked: false, backup_codes_remaining: 0 },
        },
      }
    }
    if (url === '/api/user/sessions') {
      return { data: { success: true, data: [] } }
    }
    throw new Error(`Unexpected GET ${url}`)
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  useAuthStore.getState().auth.reset()
  vi.restoreAllMocks()
})

async function renderPage(
  path = '/security',
  statusOverrides: Record<string, unknown> = {}
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  client.setQueryData(['status'], {
    passkey_login: true,
    two_fa_enrollment_enabled: true,
    checkin_enabled: false,
    wechat_login: true,
    github_oauth: true,
    oidc_enabled: true,
    custom_oauth_providers: [
      {
        id: 1,
        name: 'Gitea',
        slug: 'gitea',
        client_id: 'test-client',
        authorization_endpoint: 'https://example.com/oauth/authorize',
        scopes: 'openid',
      },
    ],
    ...statusOverrides,
  })
  const root = createRootRoute()
  const security = createRoute({
    getParentRoute: () => root,
    path: '/security',
    component: Security,
  })
  const personal = createRoute({
    getParentRoute: () => root,
    path: '/profile',
    component: Profile,
  })
  const router = createRouter({
    routeTree: root.addChildren([
      security,
      personal,
      createRoute({
        getParentRoute: () => root,
        path: '/sign-up',
        component: SignUpForm,
      }),
    ]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  await router.load()
  const rendered = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  return { ...rendered, router }
}

describe('security page migration', () => {
  it.each([false, undefined])(
    'does not mount Passkey or fetch credentials when passkey_login is %s',
    async (enabled) => {
      await renderPage('/security', { passkey_login: enabled })
      await screen.findByText('Two-Factor Authentication')
      expect(screen.queryByText('Passkey Login')).not.toBeInTheDocument()
      expect(api.get).not.toHaveBeenCalledWith('/api/user/passkey')
    }
  )

  it('hides disabled two-factor and email setup without querying them', async () => {
    await renderPage('/security', {
      email_binding_enabled: false,
      two_fa_enrollment_enabled: false,
      passkey_login: false,
    })
    await screen.findByText('Access Token', { exact: true })
    expect(screen.queryByText('Email', { exact: true })).not.toBeInTheDocument()
    expect(
      screen.queryByText('Two-Factor Authentication')
    ).not.toBeInTheDocument()
    expect(screen.queryByText('Security verification')).not.toBeInTheDocument()
    expect(api.get).not.toHaveBeenCalledWith('/api/user/2fa/status')
    expect(api.get).not.toHaveBeenCalledWith('/api/user/passkey')
  })

  it('places account management on the left and verification on the right without IP recording', async () => {
    await renderPage()
    const login = await screen.findByRole('region', {
      name: 'Login & Authentication',
    })
    expect(
      screen
        .getAllByRole('region')
        .map((region) => within(region).getAllByRole('heading')[0].textContent)
    ).toEqual([
      'Login & Authentication',
      'Sessions & Access',
      'Account Actions',
    ])
    expect(
      within(login).getByRole('button', { name: 'Change Password' })
    ).toBeVisible()
    expect(within(login).getByText('Account Bindings')).toBeVisible()
    const verification = screen.getByRole('complementary', {
      name: 'Security verification',
    })
    expect(await within(verification).findByText('Passkey Login')).toBeVisible()
    expect(
      await within(verification).findByText('Two-Factor Authentication')
    ).toBeVisible()
    expect(
      within(verification).queryByRole('switch', { name: 'Record IP Address' })
    ).not.toBeInTheDocument()
    expect(verification).toHaveClass('xl:sticky', 'xl:top-0')
    expect(verification.parentElement).toHaveClass(
      'grid',
      'xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.46fr)]'
    )
    const access = screen.getByRole('region', { name: 'Sessions & Access' })
    expect(
      within(access).getByRole('heading', { name: 'Access Token' })
    ).toBeVisible()
    expect(
      await within(access).findByText('No active login sessions')
    ).toBeVisible()
    expect(
      screen.queryByRole('switch', { name: 'Record IP Address' })
    ).not.toBeInTheDocument()
    expect(
      within(screen.getByRole('region', { name: 'Account Actions' })).getByRole(
        'button',
        { name: 'Delete Account' }
      )
    ).toBeVisible()
  })

  it('built-in and custom bindings share one compact responsive grid', async () => {
    await renderPage()
    const bindings = await screen.findByRole('list', {
      name: 'Account Bindings',
    })
    expect(within(bindings).getAllByRole('listitem')).toHaveLength(5)
    expect(within(bindings).getByText('Gitea')).toBeVisible()
    expect(bindings).toHaveClass(
      'grid-cols-1',
      'sm:grid-cols-2',
      'lg:grid-cols-3',
      'gap-2'
    )
    expect(screen.queryByText('Custom OAuth')).not.toBeInTheDocument()
  })

  it('the password action opens the existing dialog by keyboard and Escape closes it', async () => {
    const user = userEvent.setup()
    await renderPage()
    const action = await screen.findByRole('button', {
      name: 'Change Password',
    })
    action.focus()
    await user.keyboard('{Enter}')
    const dialog = await screen.findByRole('dialog', {
      name: 'Change Password',
    })
    expect(within(dialog).getByLabelText('Current Password')).toBeVisible()
    expect(
      within(dialog).getByLabelText('New Password', { exact: true })
    ).toBeVisible()
    await user.keyboard('{Escape}')
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    )
  })

  it('registration offers an optional nickname even without email verification', async () => {
    const user = userEvent.setup()
    const post = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ data: { success: false, message: 'test stop' } })
    await renderPage('/sign-up', {
      email_verification: false,
      oauth_register_enabled: false,
    })
    await user.type(await screen.findByLabelText('Username'), 'new-account')
    await user.type(
      screen.getByLabelText('Password', { exact: true }),
      'Test-only-password-894!'
    )
    await user.type(
      screen.getByLabelText('Confirm password'),
      'Test-only-password-894!'
    )
    expect(screen.getByLabelText('Nickname')).toHaveAttribute(
      'placeholder',
      'Optional'
    )
    await user.type(screen.getByLabelText('Nickname'), ' 小韩 ')
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        '/api/user/register',
        expect.objectContaining({
          username: 'new-account',
          display_name: '小韩',
        }),
        expect.anything()
      )
    )
    await user.clear(screen.getByLabelText('Nickname'))
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    await waitFor(() =>
      expect(post).toHaveBeenLastCalledWith(
        '/api/user/register',
        expect.objectContaining({ display_name: '' }),
        expect.anything()
      )
    )
  })

  it('Profile saves and clears a nickname without sending login credentials', async () => {
    const user = userEvent.setup()
    const put = vi
      .spyOn(api, 'put')
      .mockResolvedValue({ data: { success: true } })
    await renderPage('/profile')
    const input = await screen.findByLabelText('Nickname')
    expect(input).toHaveAttribute('placeholder', 'Optional')
    await user.clear(input)
    await user.type(input, ' 小韩 ')
    await user.click(screen.getByRole('button', { name: 'Save nickname' }))
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        '/api/user/self',
        { display_name: '小韩' },
        { acceptAuthRotation: false }
      )
    )
    expect(useAuthStore.getState().auth.user?.display_name).toBe('小韩')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Save nickname' })
      ).toBeDisabled()
    )
    await user.clear(input)
    await user.click(screen.getByRole('button', { name: 'Save nickname' }))
    await waitFor(() =>
      expect(put).toHaveBeenLastCalledWith(
        '/api/user/self',
        { display_name: '' },
        { acceptAuthRotation: false }
      )
    )
  })

  it('Profile offers password and security access without loading advanced security data', async () => {
    await renderPage('/profile')
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Change Password' })
      ).toBeVisible()
    )
    expect(
      screen.queryByRole('button', { name: 'Save Settings' })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: 'Security & Access' })
    ).toHaveAttribute('href', '/security')
    expect(screen.queryByText('Account Bindings')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('switch', { name: 'Record IP Address' })
    ).not.toBeInTheDocument()
    expect(api.get).not.toHaveBeenCalledWith('/api/user/passkey')
    expect(api.get).not.toHaveBeenCalledWith('/api/user/2fa/status')
    expect(api.get).not.toHaveBeenCalledWith('/api/user/sessions')
  })

  it('a failed profile load offers retry before exposing account actions', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: { success: false } })
    const user = userEvent.setup()
    await renderPage()
    expect(await screen.findByText('Failed to load profile')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Delete Account' })
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(
      await screen.findByRole('button', { name: 'Delete Account' })
    ).toBeVisible()
  })
})
