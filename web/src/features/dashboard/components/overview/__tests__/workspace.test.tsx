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
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18next from 'i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { WorkspaceInfo } from '@/features/workspace/api'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { useSystemConfigStore } from '@/stores/system-config-store'

import { buildSetupCommand } from '../setup-command'
import { WorkspaceOverview } from '../workspace-overview'

let client: QueryClient
let data: WorkspaceInfo
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  useAuthStore.getState().auth.setUser({ id: 3, username: 'alice', role: 1 })
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
  data = {
    mode: 'personal',
    balance_usd: 12.5,
    used_usd: 1.25,
    prompt_tokens: 1200,
    completion_tokens: 34,
    requests: 5,
    team: null,
    api_key: { id: 3, masked_key: 'sk-****abc', status: 1 },
  }
  vi.spyOn(api, 'post').mockResolvedValue({
    data: {
      success: true,
      data: { api_key: 'sk-default-visible-key-1234567890' },
    },
  })
  vi.spyOn(api, 'get').mockImplementation(async (url) => {
    if (url === '/api/perf-metrics/summary') {
      return { data: { success: true, data: { models: [], summary: null } } }
    }
    if (url === '/api/workspace') return { data: { success: true, data } }
    throw new Error(`Unexpected request: ${url}`)
  })
})
afterEach(async () => {
  Reflect.deleteProperty(document, 'execCommand')
  client.clear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
  for (const language of ['zhCN', 'zhTW', 'fr', 'ja', 'ru', 'vi']) {
    i18next.removeResourceBundle(language, 'translation')
  }
  await i18next.changeLanguage('en')
})
async function renderWorkspace(expectedTokens = '1,234') {
  const router = createRouter({
    routeTree: createRootRoute({ component: WorkspaceOverview }),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  await router.load()
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  await screen.findByText(expectedTokens)
}

describe('independent funding sources', () => {
  it('keeps only available balance and token usage in the overview summary', async () => {
    await renderWorkspace()
    expect(screen.getByText('Available balance')).toBeVisible()
    expect(screen.getByText('Tokens used')).toBeVisible()
    expect(screen.queryByText('Total spent')).not.toBeInTheDocument()
    expect(screen.queryByText('$1.25')).not.toBeInTheDocument()
    expect(screen.queryByText('Account overview')).not.toBeInTheDocument()
    expect(
      screen.queryByText('Personal setup uses only personal funds.')
    ).not.toBeInTheDocument()
  })
  it('offers a selectable WorkBuddy command when both clipboard methods fail', async () => {
    const user = userEvent.setup()
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(
      new Error('denied')
    )
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: vi.fn(() => false),
    })
    await renderWorkspace()
    expect(
      screen.getByText(
        'Quit Codex completely before running setup. Reopen it after setup finishes.'
      )
    ).toBeVisible()
    await user.click(screen.getByRole('tab', { name: 'WorkBuddy' }))
    await user.click(screen.getByRole('button', { name: 'Copy setup command' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Automatic copy was blocked.'
    )
    expect(
      screen.queryByRole('button', { name: 'Select command' })
    ).not.toBeInTheDocument()
    if (screen.queryByRole('button', { name: 'Show command key' })) {
      await user.click(screen.getByRole('button', { name: 'Show command key' }))
    }
    await user.click(
      screen.getByRole('textbox', { name: 'Setup command text' })
    )
    const command = screen.getByRole('textbox', {
      name: 'Setup command text',
    }) as HTMLTextAreaElement
    expect(command).toHaveFocus()
    expect(command.selectionStart).toBe(0)
    expect(command.selectionEnd).toBe(command.value.length)
    expect(command.value).toContain('/setup-workbuddy.ps1?v=3')
    expect(command.value).toContain('sk-default-visible-key-1234567890')
  })
  it('copies the selected WorkBuddy platform with the current key and keeps the guide concise', async () => {
    const user = userEvent.setup()
    await renderWorkspace()
    await user.click(screen.getByRole('tab', { name: 'WorkBuddy' }))
    expect(screen.queryByText(/Windows 5.6.2:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/GPT-6 Astra.*GPT-6 Sol/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Bundled CLI verified:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/gpt-image-2 uses/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Copy setup command' }))
    expect(await navigator.clipboard.readText()).toContain(
      '/setup-workbuddy.ps1?v=3'
    )
    expect(await navigator.clipboard.readText()).toContain(
      "'sk-default-visible-key-1234567890'"
    )
    await user.click(screen.getByRole('tab', { name: 'macOS' }))
    await user.click(screen.getByRole('button', { name: 'Copy setup command' }))
    expect(await navigator.clipboard.readText()).toContain(
      '/setup-workbuddy.sh?v=1'
    )
    expect(await navigator.clipboard.readText()).toContain(
      "'sk-default-visible-key-1234567890'"
    )
    await user.click(screen.getByRole('button', { name: 'Hide command key' }))
    expect(
      (
        screen.getByRole('textbox', {
          name: 'Setup command text',
        }) as HTMLTextAreaElement
      ).value
    ).toContain('sk-****')
    await user.click(screen.getByRole('tab', { name: 'Codex' }))
    expect(screen.getByRole('tab', { name: 'macOS / Linux' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(
      (
        screen.getByRole('textbox', {
          name: 'Setup command text',
        }) as HTMLTextAreaElement
      ).value
    ).toContain('/setup.sh?v=2')
  })

  it('keeps key setup available during a team subscription conflict without showing spendable allowance', async () => {
    data.mode = 'owner'
    data.scope = 'team'
    data.team = {
      id: 7,
      name: 'Conflict team',
      owner_user_id: 3,
      funding_version: 1,
    }
    data.subscription_conflict = true
    data.balance_usd = 0
    data.paygo_balance_usd = 100
    await renderWorkspace()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The selected account has multiple active subscriptions.'
    )
    expect(screen.getByText('$0', { selector: 'p.text-3xl' })).toBeVisible()
    expect(screen.queryByText('Pay-as-you-go balance')).not.toBeInTheDocument()
    expect(await screen.findByLabelText('My API key')).toHaveValue(
      'sk-default-visible-key-1234567890'
    )
    expect(
      screen.getByRole('button', { name: 'Copy setup command' })
    ).toBeEnabled()
  })
  it('switches balances and copies the selected personal or team setup key', async () => {
    const user = userEvent.setup()
    const clipboard = vi.spyOn(navigator.clipboard, 'writeText')
    const teamData: WorkspaceInfo = {
      ...data,
      mode: 'owner',
      scope: 'team',
      scopes: ['personal', 'team'],
      team: { id: 7, name: 'Test team', owner_user_id: 3, funding_version: 1 },
      balance_usd: 42,
      paygo_balance_usd: 100,
      api_key: { id: 88, masked_key: 'sk-****team', status: 1 },
    }
    const personalData: WorkspaceInfo = {
      ...teamData,
      mode: 'personal',
      scope: 'personal',
      balance_usd: 12.5,
      paygo_balance_usd: 12.5,
      api_key: { id: 3, masked_key: 'sk-****personal', status: 1 },
    }
    vi.mocked(api.get).mockImplementation(async (url) => ({
      data: {
        success: true,
        data: url === '/api/workspace?scope=personal' ? personalData : teamData,
      },
    }))
    vi.spyOn(api, 'post').mockImplementation(async (url) => ({
      data: {
        success: true,
        data: {
          api_key: url.includes('scope=personal')
            ? 'sk-personal-only-1234567890'
            : 'sk-team-only-1234567890',
        },
      },
    }))
    await renderWorkspace()
    expect(screen.getByRole('tab', { name: 'Team' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.getByText('$42', { selector: 'p.text-3xl' })).toBeVisible()
    expect(screen.queryByText('Pay-as-you-go balance')).not.toBeInTheDocument()
    expect(screen.queryByText('$100')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('link', { name: 'Top up' })
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Copy setup command' }))
    await waitFor(() =>
      expect(clipboard).toHaveBeenLastCalledWith(
        buildSetupCommand('windows', 'sk-team-only-1234567890')
      )
    )
    await user.click(screen.getByRole('tab', { name: 'Personal' }))
    await screen.findByRole('link', { name: 'Top up' })
    expect(screen.getByRole('tab', { name: 'Personal' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.queryByText('$42')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Top up' })).toBeVisible()
    expect(screen.getByText('Pay-as-you-go balance')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Copy setup command' }))
    await waitFor(() =>
      expect(clipboard).toHaveBeenLastCalledWith(
        buildSetupCommand('windows', 'sk-personal-only-1234567890')
      )
    )
    expect(api.post).toHaveBeenCalledWith(
      '/api/workspace/key/reveal?scope=team'
    )
    expect(api.post).toHaveBeenCalledWith(
      '/api/workspace/key/reveal?scope=personal'
    )
    expect(
      JSON.stringify(
        client
          .getQueryCache()
          .getAll()
          .map((q) => q.state.data)
      )
    ).not.toContain('sk-personal-only-1234567890')
  })

  it('discards a pending team setup credential after switching to personal', async () => {
    const user = userEvent.setup()
    const clipboard = vi.spyOn(navigator.clipboard, 'writeText')
    data = {
      ...data,
      scope: 'team',
      mode: 'owner',
      team: { id: 7, name: 'Test team', owner_user_id: 3, funding_version: 1 },
    }
    vi.mocked(api.get).mockImplementation(async (url) => ({
      data: {
        success: true,
        data: {
          ...data,
          scope: url.includes('personal') ? 'personal' : 'team',
        },
      },
    }))
    let resolve: (value: unknown) => void = () => {}
    vi.spyOn(api, 'post').mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r
        })
    )
    await renderWorkspace()
    await user.click(screen.getByRole('button', { name: 'Copy setup command' }))
    await user.click(screen.getByRole('tab', { name: 'Personal' }))
    await screen.findByRole('link', { name: 'Top up' })
    await act(async () => {
      resolve({
        data: { success: true, data: { api_key: 'sk-old-team-secret' } },
      })
    })
    expect(clipboard).not.toHaveBeenCalled()
  })
})

describe('simple personal workspace', () => {
  it('offers a tutorial link beside setup without revealing the account key', async () => {
    await renderWorkspace()
    expect(screen.getByRole('link', { name: 'Documentation' })).toHaveAttribute(
      'href',
      '/docs'
    )
    expect(api.get).not.toHaveBeenCalledWith(expect.stringContaining('/reveal'))
  })
  it('adds permanent balance to the available weekly subscription allowance and links to top-up', async () => {
    data.paygo_balance_usd = 20
    data.balance_usd = 50
    data.subscriptions = [
      {
        subscription: {
          id: 1,
          user_id: 3,
          plan_id: 1,
          status: 'active',
          start_time: Date.now() / 1000 - 60,
          end_time: Date.now() / 1000 + 86400,
          amount_total: 200 * 500000,
          amount_used: 0,
          weekly_amount: 50 * 500000,
          weekly_used: 0,
        },
      },
    ]
    await renderWorkspace()
    expect(screen.getByText('$70')).toBeVisible()
    expect(screen.getByText('Pay-as-you-go balance')).toBeVisible()
    expect(screen.getByText('$20')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Top up' })).toHaveAttribute(
      'href',
      '/wallet'
    )
  })
  it('keeps a team member balance within the server-authorized limit', async () => {
    data.mode = 'member'
    data.balance_usd = 2
    data.personal_balance_usd = 100
    data.paygo_balance_usd = 0
    await renderWorkspace()
    expect(screen.getByText('$2')).toBeVisible()
    expect(screen.queryByText('$100')).not.toBeInTheDocument()
  })
  it.each([
    [
      'legacy total cap is exceeded but weekly allowance remains',
      'active',
      -2,
      45,
      '$10',
    ],
    ['exhausted week', 'active', 100, 50, '$5'],
    ['ended subscription', 'cancelled', 100, 0, '$5'],
  ] as const)(
    'uses only spendable subscription quota for %s',
    async (_, status, remaining, weeklyUsed, expected) => {
      data.paygo_balance_usd = 5
      data.subscriptions = [
        {
          subscription: {
            id: 1,
            user_id: 3,
            plan_id: 1,
            status,
            start_time: Date.now() / 1000 - 60,
            end_time: Date.now() / 1000 + 86400,
            amount_total: 200 * 500000,
            amount_used: (200 - Number(remaining)) * 500000,
            weekly_amount: 50 * 500000,
            weekly_used: Number(weeklyUsed) * 500000,
          },
        },
      ]
      await renderWorkspace()
      expect(
        screen.getByText(expected, { selector: 'p.text-3xl' })
      ).toBeVisible()
    }
  )
  it('shows USD balance and own usage without provider catalogs or technical setup prose', async () => {
    await renderWorkspace()
    expect(screen.getAllByText('$12.5')[0]).toBeVisible()
    expect(screen.queryByText('$1.25')).not.toBeInTheDocument()
    expect(screen.getByLabelText('My API key')).toHaveAttribute('type', 'text')
    expect(screen.getByRole('link', { name: 'View my usage' })).toHaveAttribute(
      'href',
      '/usage'
    )
    expect(screen.queryByText(/config.toml/)).not.toBeInTheDocument()
    expect(
      screen.queryByText('Choose a model that fits your task')
    ).not.toBeInTheDocument()
  })
  it('shows RMB balance at seven yuan per stored dollar', async () => {
    const currency = useSystemConfigStore.getState().config.currency
    useSystemConfigStore.getState().setConfig({
      currency: { ...currency, quotaDisplayType: 'CNY', usdExchangeRate: 7 },
    })
    await renderWorkspace()
    expect(screen.getAllByText('¥87.5')[0]).toBeVisible()
    expect(screen.queryByText('¥8.75')).not.toBeInTheDocument()
    expect(screen.queryByText(/\(USD\)/)).not.toBeInTheDocument()
  })
  it('shows currency-neutral Chinese account labels with CNY amounts', async () => {
    const resource = await import('@/i18n/locales/zh.json')
    i18next.addResourceBundle('zhCN', 'translation', resource.translation)
    await i18next.changeLanguage('zhCN')
    const currency = useSystemConfigStore.getState().config.currency
    useSystemConfigStore.getState().setConfig({
      currency: { ...currency, quotaDisplayType: 'CNY', usdExchangeRate: 7 },
    })
    await renderWorkspace()
    expect(screen.getByText('可用余额')).toBeVisible()
    expect(screen.getAllByText('¥87.5')[0]).toBeVisible()
    expect(screen.queryByText(/美元|\(USD\)/)).not.toBeInTheDocument()
  })
  it.each(['en', 'zh', 'zh-TW', 'fr', 'ja', 'ru', 'vi'])(
    'keeps legacy account labels currency-neutral in %s',
    async (language) => {
      const modules = import.meta.glob<{ translation: Record<string, string> }>(
        '/src/i18n/locales/*.json'
      )
      const resource = await modules[`/src/i18n/locales/${language}.json`]()
      const translator = i18next.createInstance()
      await translator.init({
        lng: language,
        resources: { [language]: resource },
      })
      for (const label of [
        'Available balance',
        'Initial allowance',
        'Monthly allowance',
        'Remaining allowance',
        'Shared balance',
        'Total spent',
        'Weekly allowance',
      ]) {
        expect(translator.t(`${label} (USD)`)).toBe(translator.t(label))
      }
    }
  )
  it('shows the key and complete command by default, hides with sk prefix, and copies the full key while hidden', async () => {
    const user = userEvent.setup()
    const secret = 'sk-visible-personal-key-1234567890'
    vi.mocked(api.post).mockResolvedValue({
      data: { success: true, data: { api_key: secret } },
    })
    await renderWorkspace()
    await waitFor(() =>
      expect(screen.getByLabelText('My API key')).toHaveValue(secret)
    )
    expect(screen.getByLabelText('Setup command text')).toHaveValue(
      buildSetupCommand('windows', secret)
    )
    await user.click(screen.getByRole('button', { name: 'Hide key' }))
    expect(screen.getByLabelText('My API key')).toHaveValue('sk-****')
    expect(
      (screen.getByLabelText('Setup command text') as HTMLTextAreaElement).value
    ).toContain('sk-****')
    expect(document.body.innerHTML).not.toContain(secret)
    const requests = vi.mocked(api.post).mock.calls.length
    await user.click(screen.getByRole('button', { name: 'Copy API key' }))
    await waitFor(async () =>
      expect(await navigator.clipboard.readText()).toBe(secret)
    )
    expect(api.post).toHaveBeenCalledTimes(requests)
    await user.click(screen.getByRole('button', { name: 'Show key' }))
    expect(screen.getByLabelText('My API key')).toHaveValue(secret)
    expect(JSON.stringify(window.localStorage)).not.toContain(secret)
    expect(JSON.stringify(window.sessionStorage)).not.toContain(secret)
    expect(
      JSON.stringify(
        client
          .getQueryCache()
          .getAll()
          .map((query) => query.state)
      )
    ).not.toContain(secret)
    expect(
      JSON.stringify(
        client
          .getMutationCache()
          .getAll()
          .map((mutation) => mutation.state)
      )
    ).not.toContain(secret)
  })
  it('rotates only after confirmation and updates both the visible key and copied command', async () => {
    const user = userEvent.setup()
    const next = 'sk-replacement-visible-key-1234567890'
    vi.mocked(api.post).mockImplementation(async (url) => ({
      data: {
        success: true,
        data: {
          api_key: url.endsWith('/rotate')
            ? next
            : 'sk-old-visible-key-1234567890',
        },
      },
    }))
    await renderWorkspace()
    await user.click(screen.getByRole('button', { name: 'Reset key' }))
    expect(api.post).not.toHaveBeenCalledWith('/api/workspace/key/rotate')
    const dialog = await screen.findByRole('alertdialog', {
      name: 'Reset API key?',
    })
    await user.click(within(dialog).getByRole('button', { name: 'Reset key' }))
    await waitFor(() =>
      expect(screen.getByLabelText('My API key')).toHaveValue(next)
    )
    await user.click(screen.getByRole('button', { name: 'Copy setup command' }))
    await waitFor(async () =>
      expect(await navigator.clipboard.readText()).toBe(
        buildSetupCommand('windows', next)
      )
    )
    expect(document.body.innerHTML).not.toContain(
      'sk-old-visible-key-1234567890'
    )
  })
  it('creates a missing key only after a click and then loads its full text', async () => {
    data.api_key = null
    const user = userEvent.setup()
    vi.mocked(api.post).mockImplementation(async (url) => {
      if (url === '/api/workspace/key') {
        data = {
          ...data,
          api_key: { id: 4, masked_key: 'sk-****new', status: 1 },
        }
        return { data: { success: true, data: data.api_key } }
      }
      return {
        data: {
          success: true,
          data: { api_key: 'sk-new-visible-key-1234567890' },
        },
      }
    })
    await renderWorkspace()
    expect(api.post).not.toHaveBeenCalled()
    expect(
      screen.getByRole('button', { name: 'Copy setup command' })
    ).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Create my API key' }))
    await waitFor(() =>
      expect(screen.getByLabelText('My API key')).toHaveValue(
        'sk-new-visible-key-1234567890'
      )
    )
  })
  it.each(['rejected', 'invalid-format'])(
    'allows retry after %s key loading without copying a placeholder',
    async (failure) => {
      const user = userEvent.setup()
      if (failure === 'rejected') {
        vi.mocked(api.post).mockRejectedValueOnce(new Error('denied'))
      } else {
        vi.mocked(api.post).mockResolvedValueOnce({
          data: { success: true, data: { api_key: "sk-invalid'; injection" } },
        })
      }
      await renderWorkspace()
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Unable to load API key. Please retry.'
      )
      expect(
        screen.getByRole('button', { name: 'Copy setup command' })
      ).toBeDisabled()
      expect(screen.getByLabelText('My API key')).toHaveValue('sk-****')
      await user.click(screen.getByRole('button', { name: 'Retry' }))
      await waitFor(() =>
        expect(
          screen.getByRole('button', { name: 'Copy setup command' })
        ).toBeEnabled()
      )
    }
  )
  it.each(['windows', 'unix'] as const)(
    'copies exactly the visible %s command without another network request',
    async (platform) => {
      const user = userEvent.setup()
      const secret = 'sk-default-visible-key-1234567890'
      await renderWorkspace()
      if (platform === 'unix') {
        await user.click(screen.getByRole('tab', { name: 'macOS / Linux' }))
      }
      await waitFor(() =>
        expect(screen.getByLabelText('Setup command text')).toHaveValue(
          buildSetupCommand(platform, secret)
        )
      )
      const requests = vi.mocked(api.post).mock.calls.length
      await user.click(
        screen.getByRole('button', { name: 'Copy setup command' })
      )
      await waitFor(async () =>
        expect(await navigator.clipboard.readText()).toBe(
          buildSetupCommand(platform, secret)
        )
      )
      expect(api.post).toHaveBeenCalledTimes(requests)
      expect(await navigator.clipboard.readText()).not.toMatch(/[\r\n]/)
    }
  )
  it('uses the selection-copy fallback when the modern clipboard rejects permission', async () => {
    const user = userEvent.setup()
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(
      new Error('permission denied')
    )
    const fallback = vi.fn(() => true)
    Object.defineProperty(document, 'execCommand', {
      value: fallback,
      configurable: true,
    })
    await renderWorkspace()
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Copy setup command' })
      ).toBeEnabled()
    )
    await user.click(screen.getByRole('button', { name: 'Copy setup command' }))
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeVisible()
    expect(fallback).toHaveBeenCalledWith('copy')
    expect(document.querySelectorAll('textarea')).toHaveLength(1)
  })
  it('keeps the complete command selectable when all automatic copy methods are blocked', async () => {
    const user = userEvent.setup()
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(
      new Error('permission denied')
    )
    Object.defineProperty(document, 'execCommand', {
      value: vi.fn(() => false),
      configurable: true,
    })
    await renderWorkspace()
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Hide command key' })
      ).toBeEnabled()
    )
    await user.click(screen.getByRole('button', { name: 'Hide command key' }))
    await user.click(screen.getByRole('button', { name: 'Copy setup command' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Automatic copy was blocked.'
    )
    expect(
      screen.queryByRole('button', { name: 'Copied' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Select command' })
    ).not.toBeInTheDocument()
    if (screen.queryByRole('button', { name: 'Show command key' })) {
      await user.click(screen.getByRole('button', { name: 'Show command key' }))
    }
    await user.click(
      screen.getByRole('textbox', { name: 'Setup command text' })
    )
    const field = screen.getByLabelText(
      'Setup command text'
    ) as HTMLTextAreaElement
    await waitFor(() =>
      expect(field.selectionEnd - field.selectionStart).toBe(field.value.length)
    )
    expect(field.value).toContain('sk-default-visible-key-1234567890')
  })
  it('discards a pending key after the signed-in account changes', async () => {
    let finishReveal!: (value: unknown) => void
    vi.mocked(api.post).mockReturnValueOnce(
      new Promise((resolve) => {
        finishReveal = resolve
      })
    )
    const write = vi.spyOn(navigator.clipboard, 'writeText')
    await renderWorkspace()
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/api/workspace/key/reveal')
    )
    await act(async () => {
      useAuthStore.getState().auth.setUser({ id: 4, username: 'bob', role: 1 })
      finishReveal({
        data: {
          success: true,
          data: { api_key: 'sk-previous-account-1234567890' },
        },
      })
    })
    expect(write).not.toHaveBeenCalled()
    expect(document.body.innerHTML).not.toContain(
      'sk-previous-account-1234567890'
    )
  })
  it('updates number formatting for all supported languages and invalid-language fallback', async () => {
    for (const language of ['zhCN', 'zhTW', 'en', 'fr', 'ja', 'ru', 'vi']) {
      i18next.addResourceBundle(language, 'translation', {
        'Tokens used': 'Tokens used',
      })
    }
    await renderWorkspace()
    for (const [language, formatted] of Object.entries({
      zhCN: '1,234',
      zhTW: '1,234',
      en: '1,234',
      fr: '1\u202f234',
      ja: '1,234',
      ru: '1\u00a0234',
      vi: '1.234',
      'invalid-locale': '1,234',
    })) {
      await act(async () => {
        await i18next.changeLanguage(language)
      })
      expect(
        screen.getByText(
          (_text, element) =>
            element?.tagName === 'P' && element.textContent === formatted
        )
      ).toBeVisible()
    }
  })
  it.each(['personal', 'team'] as const)(
    'formats large cumulative tokens in the %s overview',
    async (scope) => {
      data.scope = scope
      data.prompt_tokens = 1200000
      data.completion_tokens = 34567
      if (scope === 'team') {
        data.mode = 'owner'
        data.team = {
          id: 1,
          name: 'Team',
          owner_user_id: 3,
          funding_version: 1,
        }
      }
      i18next.addResourceBundle('zhCN', 'translation', {
        'Tokens used': 'Tokens used',
      })
      await i18next.changeLanguage('zhCN')
      await renderWorkspace('1.23百万')
      expect(screen.getByText('1.23百万')).toBeVisible()
    }
  )
})

describe('setup command validation', () => {
  it.each([
    ['short', 'sk-short'],
    ['too long', `sk-${'a'.repeat(257)}`],
    ['shell quote', "sk-validcharacters123'; echo injected"],
    ['shell substitution', 'sk-$(echo injected)1234567890'],
    ['trailing newline', 'sk-validcharacters123\n'],
    ['masked preview', 'sk-••••••••••••••••'],
  ])(
    'rejects a %s key before producing either shell command',
    (_reason, key) => {
      expect(() => buildSetupCommand('windows', key)).toThrow(
        'Invalid API key format'
      )
      expect(() => buildSetupCommand('unix', key)).toThrow(
        'Invalid API key format'
      )
    }
  )
})
