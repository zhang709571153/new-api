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
import i18next from 'i18next'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SidebarProvider } from '@/components/ui/sidebar'
import { ThemeProvider } from '@/context/theme-provider'
import { About } from '@/features/about'
import { AuthLayout } from '@/features/auth/auth-layout'
import { GeneralError } from '@/features/errors/general-error'
import { getOperationsSectionNavItems } from '@/features/system-settings/operations/section-registry'
import { DEFAULT_FAVICON, DEFAULT_LOGO } from '@/lib/constants'
import { applyFaviconToDom } from '@/lib/dom-utils'
import { STATUS_QUERY_KEY } from '@/lib/status-query'
import { useAuthStore } from '@/stores/auth-store'
import { useSystemConfigStore } from '@/stores/system-config-store'

import { AppHeader } from '../components/app-header'
import { Footer } from '../components/footer'
import { HeaderLogo } from '../components/header-logo'
import { PublicHeader } from '../components/public-header'
import { PublicLayout } from '../components/public-layout'
import { SystemBrand } from '../components/system-brand'

let client: QueryClient
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(STATUS_QUERY_KEY, {
    user_agreement_enabled: true,
    privacy_policy_enabled: true,
    version: 'realyu-private-release',
  })
  client.setQueryData(['notice'], { success: true, data: '' })
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] })
  )
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  document.documentElement.classList.remove('dark', 'light')
  useSystemConfigStore.getState().setLoading(false)
})
afterEach(() => {
  vi.unstubAllGlobals()
  document.querySelectorAll('link[rel="icon"]').forEach((link) => link.remove())
  client.clear()
  window.localStorage.clear()
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
})

async function renderBrand(content: ReactNode) {
  const router = createRouter({
    routeTree: createRootRoute({ component: () => content }),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  await router.load()
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider defaultTheme='light'>
        <RouterProvider router={router} />
      </ThemeProvider>
    </QueryClientProvider>
  )
}

describe('RealYu site branding', () => {
  it.each([true, false])(
    'provides one main landmark and a focusable skip target for signed-in pages (container=%s)',
    async (showMainContainer) => {
      useAuthStore
        .getState()
        .auth.setUser({ id: 3, username: 'alice', role: 1 })
      await renderBrand(
        <PublicLayout
          showSidebarWhenAuthenticated
          showMainContainer={showMainContainer}
        >
          Page content
        </PublicLayout>
      )
      expect(screen.getAllByRole('main')).toHaveLength(1)
      const skip = screen.getByRole('link', { name: 'Skip to Main' })
      const href = skip.getAttribute('href')
      if (!href) throw new Error('Missing skip target')
      const content = document.querySelector<HTMLElement>(href)
      if (!content) throw new Error('Missing content container')
      expect(content).toHaveTextContent('Page content')
      content.focus()
      expect(content).toHaveFocus()
    }
  )
  it('reuses workspace navigation for signed-in informational pages', async () => {
    useAuthStore.getState().auth.setUser({ id: 3, username: 'alice', role: 1 })
    await renderBrand(
      <PublicLayout showSidebarWhenAuthenticated>Page content</PublicLayout>
    )
    expect(screen.getByText('Page content')).toBeVisible()
    expect(
      document.querySelector('[data-sidebar="sidebar"]')
    ).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Home' })).not.toBeInTheDocument()
  })

  it('keeps informational pages accessible to visitors with public navigation', async () => {
    await renderBrand(
      <PublicLayout showSidebarWhenAuthenticated>Page content</PublicLayout>
    )
    expect(screen.getByRole('link', { name: 'Home' })).toBeVisible()
    expect(
      document.querySelector('[data-sidebar="sidebar"]')
    ).not.toBeInTheDocument()
  })
  it('exposes the public pages and keeps working theme selection with spaced full-width branding', async () => {
    const user = userEvent.setup()
    await renderBrand(<PublicHeader />)
    const header = screen.getByRole('banner')
    expect(within(header).getByRole('link', { name: 'Home' })).toHaveAttribute(
      'href',
      '/'
    )
    for (const [name, href] of [
      ['Model catalog', '/pricing'],
      ['Documentation', '/docs'],
      ['Service status', '/service-status'],
    ]) {
      expect(within(header).getByRole('link', { name })).toHaveAttribute(
        'href',
        href
      )
    }
    expect(
      within(header).getByRole('img', { name: 'RealYu API' })
    ).toHaveAttribute('src', DEFAULT_LOGO)
    expect(within(header).getAllByRole('navigation')[0]).toHaveClass(
      'max-w-6xl',
      'px-6',
      'sm:px-8',
      'py-5'
    )
    expect(
      within(header).getByRole('link', { name: 'Sign in' })
    ).toHaveAttribute('href', '/sign-in')
    expect(
      within(header).getByRole('link', { name: 'Get started' })
    ).toHaveAttribute('href', '/sign-up')
    await user.click(
      within(header).getByRole('button', { name: 'Toggle theme' })
    )
    expect(document.documentElement).toHaveClass('dark')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    await user.click(
      within(header).getByRole('button', { name: 'Toggle theme' })
    )
    expect(document.documentElement).toHaveClass('light')
    expect(header).toHaveClass('text-foreground', 'bg-background/85')
  })

  it.each(['public', 'authenticated'] as const)(
    'shows the account name and preserves the logout confirmation without update controls in the %s header',
    async (surface) => {
      const user = userEvent.setup()
      useAuthStore.getState().auth.setUser({
        id: 1,
        username: 'test-admin',
        display_name: 'Team admin',
        role: 100,
      })
      await renderBrand(
        surface === 'public' ? (
          <PublicHeader />
        ) : (
          <SidebarProvider>
            <AppHeader />
          </SidebarProvider>
        )
      )
      const header = screen.getByRole('banner')
      const account = within(header).getByRole('button', { name: 'test-admin' })
      expect(account).toHaveClass('h-11')
      expect(within(account).getByText('test-admin')).toHaveClass('truncate')
      expect(
        within(header).queryByRole('button', {
          name: /System updates|Check for updates/,
        })
      ).not.toBeInTheDocument()
      expect(header).not.toHaveTextContent('realyu-private-release')
      expect(fetch).not.toHaveBeenCalled()
      await user.click(account)
      await user.click(screen.getByRole('menuitem', { name: 'Sign out' }))
      expect(
        screen.getByRole('alertdialog', { name: 'Sign out' })
      ).toBeVisible()
    }
  )

  it('removes the system update entry from maintenance navigation', () => {
    const links = getOperationsSectionNavItems(i18next.t)
    expect(links.some((link) => link.url.endsWith('/update-checker'))).toBe(
      false
    )
  })
  it('shows RealYu and legal links without upstream attribution in the default footer', async () => {
    await renderBrand(<Footer />)
    const footer = screen.getByRole('contentinfo')
    expect(footer).not.toHaveTextContent(/New API|QuantumNous/)
    expect(
      within(footer).getByRole('link', { name: /RealYu API/ })
    ).toHaveAttribute('href', '/')
    expect(
      within(footer).getByRole('link', { name: 'User Agreement' })
    ).toHaveAttribute('href', '/user-agreement')
    expect(
      within(footer).getByRole('link', { name: 'Privacy Policy' })
    ).toHaveAttribute('href', '/privacy-policy')
    expect(
      within(footer).getByRole('img', { name: 'RealYu API' })
    ).toHaveAttribute('src', DEFAULT_LOGO)
  })

  it('preserves configured footer content and legal links without adding upstream attribution', async () => {
    useSystemConfigStore
      .getState()
      .setConfig({ footerHtml: '<p>Company support</p>' })
    await renderBrand(<Footer />)
    const footer = screen.getByRole('contentinfo')
    expect(within(footer).getByText('Company support')).toBeVisible()
    expect(
      within(footer).getByRole('link', { name: 'User Agreement' })
    ).toBeVisible()
    expect(
      within(footer).getByRole('link', { name: 'Privacy Policy' })
    ).toBeVisible()
    expect(footer).not.toHaveTextContent(/New API|QuantumNous/)
  })

  it('uses the site brand on an unconfigured about page without upstream author links', async () => {
    client.setQueryDefaults(['about-content'], { staleTime: Infinity })
    client.setQueryData(['about-content'], { success: true, data: '' })
    client.setQueryData(['notice'], { success: true, data: '' })
    await renderBrand(<About />)
    expect(screen.getByRole('main')).not.toHaveTextContent(
      /NewAPI|New API|QuantumNous/
    )
    expect(
      within(screen.getByRole('main')).getByRole('img', { name: 'RealYu API' })
    ).toHaveAttribute('src', DEFAULT_LOGO)
  })

  it('offers navigation after an error without directing users to the upstream project', async () => {
    await renderBrand(<GeneralError />)
    expect(
      screen.queryByRole('link', { name: 'Report an issue' })
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/GitHub Issues/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Back to Home' })).toBeVisible()
  })

  it.each(['auth', 'public', 'inline', 'sidebar'] as const)(
    'shows the complete image logo with bounded dimensions in the %s brand placement',
    async (placement) => {
      const surfaces = {
        auth: (
          <AuthLayout>
            <p>Sign in form</p>
          </AuthLayout>
        ),
        public: <HeaderLogo src={DEFAULT_LOGO} loading={false} logoLoaded />,
        inline: <SystemBrand variant='inline' />,
        sidebar: (
          <SidebarProvider>
            <SystemBrand />
          </SidebarProvider>
        ),
      }
      await renderBrand(surfaces[placement])
      const logo = screen.getByRole('img', { name: 'RealYu API' })
      expect(logo).toHaveAttribute('src', DEFAULT_LOGO)
      expect(logo).toHaveClass('h-10', 'w-45', 'max-w-full', 'object-contain')
      expect(logo).toBeVisible()
    }
  )

  it('shows a configured logo directly without waiting for a separate preload', async () => {
    useSystemConfigStore.getState().setConfig({ logo: '/custom-wordmark.png' })
    await renderBrand(
      <AuthLayout>
        <p>Sign in form</p>
      </AuthLayout>
    )
    expect(screen.getByRole('img', { name: 'RealYu API' })).toHaveAttribute(
      'src',
      '/custom-wordmark.png'
    )
  })

  it('uses the complete default logo when a placement has no configured image', () => {
    render(<HeaderLogo src='' loading={false} logoLoaded={false} />)
    expect(screen.getByRole('img', { name: 'RealYu API' })).toHaveAttribute(
      'src',
      DEFAULT_LOGO
    )
  })

  it('replaces a cached Yu favicon with the blank icon', () => {
    const favicon = document.createElement('link')
    favicon.rel = 'icon'
    favicon.href = '/brand/favicon.ico?v=20260924-2'
    document.head.appendChild(favicon)
    applyFaviconToDom(DEFAULT_FAVICON)
    expect(document.querySelector('link[rel="icon"]')).toHaveAttribute(
      'href',
      'data:,'
    )
  })
})
