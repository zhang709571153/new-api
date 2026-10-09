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
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18next from 'i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import zh from '@/i18n/locales/zh.json'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { useSystemConfigStore } from '@/stores/system-config-store'

import { TutorialCenter } from '..'

let client: QueryClient
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
  vi.spyOn(api, 'get').mockImplementation(async (url) => ({
    data: {
      success: true,
      data: url === '/api/notice' ? '' : { register_enabled: true },
    },
  }))
})
afterEach(async () => {
  cleanup()
  client.clear()
  i18next.removeResourceBundle('zhCN', 'translation')
  await i18next.changeLanguage('en')
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
})
async function renderTutorials() {
  const router = createRouter({
    routeTree: createRootRoute({ component: TutorialCenter }),
    history: createMemoryHistory({ initialEntries: ['/tutorials'] }),
  })
  await router.load()
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  await screen.findByRole('tab', { name: 'Windows' })
}
describe('documentation navigation', () => {
  it('shows the matching setup for all four app and operating system combinations', async () => {
    const user = userEvent.setup()
    await renderTutorials()
    expect(screen.getByRole('heading', { name: 'Install Codex' })).toBeVisible()
    expect(
      screen.getByText(/Open PowerShell from the Start menu/)
    ).toBeVisible()
    expect(
      screen.getByText(/fully quit it, including the tray icon/)
    ).toBeVisible()
    await user.click(screen.getByRole('tab', { name: 'Windows' }))
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: 'macOS' })).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('tab', { name: 'macOS' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.getByText(/Use Command \+ Q to quit Codex/)).toBeVisible()
    expect(
      screen.queryByText(/Open PowerShell from the Start menu/)
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'WorkBuddy' }))
    expect(
      screen.getByRole('heading', { name: 'Add a custom model' })
    ).toBeVisible()
    expect(screen.getByText(/has not yet been tested on a Mac/)).toBeVisible()
    expect(
      screen.getByText('https://api.realyu.fun/v1/chat/completions')
    ).toBeVisible()
    await user.click(screen.getByRole('tab', { name: 'Windows' }))
    expect(screen.getByText(/For WorkBuddy 5.6.2 on Windows/)).toBeVisible()
    expect(
      screen.queryByRole('heading', { name: 'Add a custom model' })
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: 'Official download' })
    ).toHaveAttribute('href', 'https://www.codebuddy.cn/work/')
    expect(
      screen.getByRole('link', { name: 'Open My workspace' })
    ).toHaveAttribute('href', '/dashboard/overview')
  })
  it('keeps the selected topic and translates its content when the language changes', async () => {
    const user = userEvent.setup()
    await renderTutorials()
    await user.click(screen.getByRole('tab', { name: 'WorkBuddy' }))
    await user.click(screen.getByRole('tab', { name: 'macOS' }))
    i18next.addResourceBundle('zhCN', 'translation', zh.translation, true, true)
    await act(() => i18next.changeLanguage('zhCN'))
    expect(screen.getByRole('tab', { name: 'WorkBuddy' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.getByRole('tab', { name: 'macOS' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(
      screen.getByRole('heading', { name: '添加自定义模型' })
    ).toBeVisible()
    expect(screen.getByText('开始使用')).toBeVisible()
  })
  it('keeps troubleshooting collapsed and links to the official usage guide', async () => {
    const user = userEvent.setup()
    await renderTutorials()
    expect(screen.queryByText(/Never copy sk-/)).not.toBeInTheDocument()
    await user.click(
      screen.getByRole('button', { name: 'Cannot copy the command?' })
    )
    expect(screen.getByText(/Never copy sk-/)).toBeVisible()
    expect(
      screen.getByRole('link', { name: 'Official usage guide' })
    ).toHaveAttribute('href', 'https://learn.chatgpt.com/docs/app')
  })
})
