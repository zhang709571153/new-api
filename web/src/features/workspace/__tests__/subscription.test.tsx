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
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18next from 'i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { UserSubscription } from '@/features/subscriptions/types'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import {
  DEFAULT_CURRENCY_CONFIG,
  useSystemConfigStore,
} from '@/stores/system-config-store'

import { AllowanceDialog } from '../allowance-dialog'
import { SubscriptionBalance } from '../subscription-balance'

const now = Date.now() / 1000
const day = 86400
const quota = 500000
const initialConfig = useSystemConfigStore.getState().config
let client: QueryClient
let subscriptions: { subscription: UserSubscription }[]
let ownedTeam: {
  id: number
  name: string
  owner_user_id: number
  funding_version: number
} | null
function subscription(input: Partial<UserSubscription> = {}): UserSubscription {
  return {
    id: 5,
    user_id: 3,
    plan_id: 2,
    status: 'active',
    start_time: now - day,
    end_time: now + 29 * day,
    amount_total: 300 * quota,
    amount_used: 100 * quota,
    weekly_amount: 75 * quota,
    weekly_used: 25 * quota,
    weekly_reset_at: now + 6 * day,
    ...input,
  }
}
beforeEach(() => {
  useAuthStore.getState().auth.setUser({ id: 1, username: 'admin', role: 10 })
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  subscriptions = []
  ownedTeam = null
  useSystemConfigStore
    .getState()
    .setConfig({ currency: { ...DEFAULT_CURRENCY_CONFIG } })
  vi.spyOn(api, 'get').mockImplementation(async () => ({
    data: { success: true, data: subscriptions, team: ownedTeam },
  }))
})
afterEach(async () => {
  client.clear()
  useSystemConfigStore.getState().setConfig(initialConfig)
  await i18next.changeLanguage('en')
})
function renderDialog(
  options: {
    fixedScope?: 'personal' | 'team'
    expectedTeamId?: number
    expectedFundingVersion?: number
  } = {}
) {
  const onClose = vi.fn()
  const onSuccess = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <AllowanceDialog
        {...options}
        user={{ id: 3, username: 'Alice' }}
        onClose={onClose}
        onSuccess={onSuccess}
      />
    </QueryClientProvider>
  )
  return { onClose, onSuccess }
}
describe('admin weekly subscription allowance', () => {
  it('blocks expiry when limits submission acquired the shared lock before either resolver completes', async () => {
    subscriptions = [{ subscription: subscription() }]
    const put = vi
      .spyOn(api, 'put')
      .mockImplementation(() => new Promise(() => {}))
    renderDialog()
    await screen.findByLabelText('Expires at')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save limits' }))
      fireEvent.click(screen.getByRole('button', { name: 'Save expiry' }))
    })
    await waitFor(() => expect(put).toHaveBeenCalled())
    expect(put).toHaveBeenCalledTimes(1)
  })

  it('saving expiry preserves scope, limits and usage and blocks duplicate or conflicting submissions', async () => {
    const original = subscription({
      workspace_team_id: 7,
      end_time: Math.floor(now + 29 * day),
    })
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 1,
    }
    subscriptions = [{ subscription: original }]
    let resolveSave!: (value: unknown) => void
    const put = vi.spyOn(api, 'put').mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve
        })
    )
    const { onSuccess } = renderDialog({
      fixedScope: 'team',
      expectedTeamId: 7,
      expectedFundingVersion: 1,
    })
    const input = await screen.findByLabelText('Expires at')
    const expiry = '2099-01-02T03:04:05'
    fireEvent.change(input, { target: { value: expiry } })
    const save = screen.getByRole('button', { name: 'Save expiry' })
    fireEvent.click(save)
    fireEvent.click(save)
    await waitFor(() => expect(save).toBeDisabled())
    expect(screen.getByRole('button', { name: 'Save limits' })).toBeDisabled()
    expect(put).toHaveBeenCalledTimes(1)
    expect(put).toHaveBeenCalledWith(
      '/api/subscription/admin/users/3/allowance',
      {
        team_id: 7,
        subscription_id: 5,
        expected_end_time: original.end_time,
        end_time: Math.floor(new Date(expiry).getTime() / 1000),
        funding_context: { team_id: 7, funding_version: 1 },
      }
    )
    await act(async () => resolveSave({ data: { success: true } }))
    await waitFor(() => expect(onSuccess).toHaveBeenCalledOnce())
  })

  it('past expiry blocks submission and a server rejection retains the entered date for correction', async () => {
    subscriptions = [{ subscription: subscription() }]
    const put = vi.spyOn(api, 'put').mockResolvedValue({
      data: {
        success: false,
        message: 'Subscription changed; refresh first',
      },
    })
    const { onClose, onSuccess } = renderDialog()
    const input = await screen.findByLabelText('Expires at')
    fireEvent.change(input, { target: { value: '2000-01-01T00:00:00' } })
    await userEvent.click(screen.getByRole('button', { name: 'Save expiry' }))
    expect(
      await screen.findByText('Choose a future expiry time.')
    ).toBeVisible()
    expect(put).not.toHaveBeenCalled()
    fireEvent.change(input, { target: { value: '2099-01-02T03:04:05' } })
    await userEvent.click(screen.getByRole('button', { name: 'Save expiry' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Subscription changed; refresh first'
    )
    expect(input).toHaveValue('2099-01-02T03:04:05.000')
    expect(onClose).not.toHaveBeenCalled()
    expect(onSuccess).not.toHaveBeenCalled()
  })

  it('manually provisions a team only after selecting the team scope and submitting', async () => {
    const post = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ data: { success: true } })
    const put = vi
      .spyOn(api, 'put')
      .mockResolvedValue({ data: { success: true } })
    subscriptions = [{ subscription: subscription() }]
    renderDialog()
    await screen.findByText('Personal allowance: Alice')
    await userEvent.click(screen.getByRole('tab', { name: 'Team' }))
    expect(
      screen.queryByRole('button', { name: 'Save limits' })
    ).not.toBeInTheDocument()
    await userEvent.type(
      screen.getByLabelText('Team name (optional)'),
      'Research'
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'Activate team subscription' })
    )
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        '/api/subscription/admin/users/3/team-provision',
        { name: 'Research', weekly_usd: 50 }
      )
    )
    expect(put).not.toHaveBeenCalled()
  })
  it('sends a standalone zero weekly usage adjustment without rewriting limits or member usage', async () => {
    useAuthStore.getState().auth.setUser({ id: 1, username: 'root', role: 100 })
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 1,
    }
    subscriptions = [{ subscription: subscription({ workspace_team_id: 7 }) }]
    const put = vi
      .spyOn(api, 'put')
      .mockResolvedValue({ data: { success: true } })
    renderDialog({
      fixedScope: 'team',
      expectedTeamId: 7,
      expectedFundingVersion: 1,
    })
    const input = await screen.findByLabelText('Team usage this week (USD)')
    expect(input).toHaveValue(25)
    await userEvent.clear(input)
    await userEvent.type(input, '0')
    await userEvent.click(
      screen.getByRole('button', { name: 'Save weekly usage' })
    )
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        '/api/subscription/admin/users/3/allowance',
        {
          team_id: 7,
          subscription_id: 5,
          weekly_used_usd: 0,
          weekly_reset_at: now + 6 * day,
          funding_context: { team_id: 7, funding_version: 1 },
        }
      )
    )
  })
  it('does not expose team usage adjustment to a normal administrator', async () => {
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 1,
    }
    subscriptions = [{ subscription: subscription({ workspace_team_id: 7 }) }]
    renderDialog({ fixedScope: 'team' })
    expect(
      await screen.findByRole('button', { name: 'Save limits' })
    ).toBeEnabled()
    expect(
      screen.queryByLabelText('Team usage this week (USD)')
    ).not.toBeInTheDocument()
  })
  it('keeps the usage adjustment open and displays settlement conflicts', async () => {
    useAuthStore.getState().auth.setUser({ id: 1, username: 'root', role: 100 })
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 1,
    }
    subscriptions = [{ subscription: subscription({ workspace_team_id: 7 }) }]
    vi.spyOn(api, 'put').mockResolvedValue({
      data: { success: false, message: 'Requests are still settling' },
    })
    const { onClose } = renderDialog({ fixedScope: 'team' })
    await userEvent.click(
      await screen.findByRole('button', { name: 'Save weekly usage' })
    )
    expect(
      await screen.findByText('Requests are still settling')
    ).toHaveAttribute('role', 'alert')
    expect(onClose).not.toHaveBeenCalled()
  })
  it.each([
    [0, 0],
    [0, day],
    [7, 0],
    [7, day],
  ])(
    'blocks duplicate subscriptions in scope %s, including a future start at %s',
    async (teamId, startOffset) => {
      ownedTeam = {
        id: 7,
        name: 'Research',
        owner_user_id: 3,
        funding_version: 1,
      }
      subscriptions = [
        { subscription: subscription({ id: 5, workspace_team_id: teamId }) },
        {
          subscription: subscription({
            id: 6,
            workspace_team_id: teamId,
            start_time: now + startOffset,
          }),
        },
      ]
      renderDialog({
        fixedScope: teamId ? 'team' : 'personal',
        expectedTeamId: 7,
        expectedFundingVersion: 1,
      })
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'The selected account has multiple active subscriptions.'
      )
      expect(
        screen.queryByRole('combobox', { name: 'Subscription' })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole('button', { name: 'Save limits' })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole('button', { name: 'Activate subscription' })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole('button', { name: 'End subscription' })
      ).not.toBeInTheDocument()
    }
  )
  it('blocks cached team data during refresh and carries the funding precondition on creation', async () => {
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 1,
    }
    client.setQueryData(['user-allowance', 3, 1], {
      success: true,
      data: [],
      team: ownedTeam,
    })
    let resolve!: (value: unknown) => void
    vi.mocked(api.get).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    const put = vi
      .spyOn(api, 'put')
      .mockResolvedValue({ data: { success: true } })
    renderDialog({
      fixedScope: 'team',
      expectedTeamId: 7,
      expectedFundingVersion: 1,
    })
    expect(
      await screen.findByRole('button', { name: 'Activate subscription' })
    ).toBeDisabled()
    await act(async () =>
      resolve({ data: { success: true, data: [], team: ownedTeam } })
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'Activate subscription' })
    )
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        '/api/subscription/admin/users/3/allowance',
        {
          team_id: 7,
          weekly_usd: 50,
          funding_context: { team_id: 7, funding_version: 1 },
        }
      )
    )
  })
  it('rejects a stale legacy team shortcut after the team has migrated', async () => {
    ownedTeam = {
      id: 7,
      name: 'Migrated',
      owner_user_id: 3,
      funding_version: 1,
    }
    subscriptions = [{ subscription: subscription({ workspace_team_id: 7 }) }]
    renderDialog({
      fixedScope: 'personal',
      expectedTeamId: 7,
      expectedFundingVersion: 0,
    })
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The team has changed.'
    )
    expect(
      screen.queryByRole('button', { name: 'Activate subscription' })
    ).not.toBeInTheDocument()
  })
  it('edits the exact personal subscription without changing team or cancelled history', async () => {
    subscriptions = [
      {
        subscription: subscription({
          id: 5,
          source: 'cny_order',
          purchase_title: 'Paid One',
          workspace_team_id: 7,
        }),
      },
      {
        subscription: subscription({
          id: 6,
          source: 'cny_order',
          purchase_title: 'Paid Two',
          amount_total: 400 * quota,
        }),
      },
      {
        subscription: subscription({
          id: 7,
          start_time: now + day,
          purchase_title: 'Future',
          status: 'cancelled',
        }),
      },
    ]
    const put = vi
      .spyOn(api, 'put')
      .mockResolvedValue({ data: { success: true } })
    renderDialog({ fixedScope: 'personal' })
    expect(await screen.findByLabelText('Weekly allowance (USD)')).toHaveValue(
      75
    )
    expect(
      screen.queryByRole('combobox', { name: 'Subscription' })
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(
        'Changing limits preserves usage, expiry and payment policy. No payment or renewal is triggered.'
      )
    ).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Save limits' }))
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        '/api/subscription/admin/users/3/allowance',
        { weekly_usd: 75, team_id: 0, subscription_id: 6 }
      )
    )
  })
  it('never falls back to personal editing when the selected team no longer matches', async () => {
    subscriptions = [{ subscription: subscription() }]
    ownedTeam = {
      id: 8,
      name: 'Replacement',
      owner_user_id: 3,
      funding_version: 1,
    }
    renderDialog({ fixedScope: 'team', expectedTeamId: 7 })
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The team has changed.'
    )
    expect(
      screen.queryByRole('button', { name: 'Save limits' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Activate subscription' })
    ).not.toBeInTheDocument()
  })
  it('opens personal limits explicitly even when this account owns a team', async () => {
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 1,
    }
    subscriptions = [
      { subscription: subscription({ id: 5, amount_total: 400 * quota }) },
      { subscription: subscription({ id: 6, workspace_team_id: 7 }) },
    ]
    renderDialog({ fixedScope: 'personal' })
    expect(await screen.findByLabelText('Weekly allowance (USD)')).toHaveValue(
      75
    )
    expect(screen.queryByRole('tab', { name: 'Team' })).not.toBeInTheDocument()
    expect(screen.getByText('Personal allowance: Alice')).toBeVisible()
  })
  it('opens the migrated team allowance and submits its explicit team scope', async () => {
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 1,
    }
    subscriptions = [
      {
        subscription: {
          ...subscription({ amount_total: 120 * quota }),
          workspace_team_id: 7,
        },
      },
    ]
    const put = vi
      .spyOn(api, 'put')
      .mockResolvedValue({ data: { success: true } })
    renderDialog()
    expect(await screen.findByRole('tab', { name: 'Team' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.getByLabelText('Weekly allowance (USD)')).toHaveValue(75)
    await userEvent.click(screen.getByRole('button', { name: 'Save limits' }))
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        '/api/subscription/admin/users/3/allowance',
        { weekly_usd: 75, team_id: 7, subscription_id: 5 }
      )
    )
  })
  it('switches between separate personal and team limits and submits only the selected pool', async () => {
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 1,
    }
    subscriptions = [
      { subscription: subscription({ id: 5, amount_total: 600 * quota }) },
      {
        subscription: {
          ...subscription({ id: 6, amount_total: 120 * quota }),
          workspace_team_id: 7,
        },
      },
    ]
    const put = vi
      .spyOn(api, 'put')
      .mockResolvedValue({ data: { success: true } })
    renderDialog()
    await userEvent.click(await screen.findByRole('tab', { name: 'Personal' }))
    expect(screen.getByLabelText('Weekly allowance (USD)')).toHaveValue(75)
    await userEvent.click(screen.getByRole('button', { name: 'Save limits' }))
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        '/api/subscription/admin/users/3/allowance',
        { weekly_usd: 75, team_id: 0, subscription_id: 5 }
      )
    )
  })
  it('creates an empty team allowance and prevents changing scope during submission', async () => {
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 1,
    }
    let resolve!: (value: { data: { success: boolean } }) => void
    const put = vi.spyOn(api, 'put').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    renderDialog()
    await userEvent.click(
      await screen.findByRole('button', { name: 'Activate subscription' })
    )
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Personal' })).toHaveAttribute(
        'aria-disabled',
        'true'
      )
    )
    fireEvent.click(screen.getByRole('tab', { name: 'Personal' }))
    expect(screen.getByRole('tab', { name: 'Team' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(put).toHaveBeenCalledWith(
      '/api/subscription/admin/users/3/allowance',
      { weekly_usd: 50, team_id: 7 }
    )
    await act(async () => resolve({ data: { success: true } }))
  })
  it('ends only the selected team allowance after confirmation', async () => {
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 1,
    }
    subscriptions = [
      { subscription: subscription({ id: 5 }) },
      { subscription: { ...subscription({ id: 6 }), workspace_team_id: 7 } },
    ]
    const post = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ data: { success: true } })
    renderDialog()
    await userEvent.click(
      await screen.findByRole('button', { name: 'End subscription' })
    )
    const confirm = await screen.findByRole('alertdialog', {
      name: 'End subscription?',
    })
    await userEvent.click(
      within(confirm).getByRole('button', { name: 'End subscription' })
    )
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        '/api/subscription/admin/user_subscriptions/6/invalidate',
        { expected_scope: { user_id: 3, workspace_team_id: 7 } }
      )
    )
  })
  it('starts a new 28-day subscription with only a $50 weekly input', async () => {
    const user = userEvent.setup()
    const put = vi
      .spyOn(api, 'put')
      .mockResolvedValue({ data: { success: true } })
    const callbacks = renderDialog()
    expect(await screen.findByLabelText('Weekly allowance (USD)')).toHaveValue(
      50
    )
    expect(screen.getByLabelText('Weekly allowance (USD)')).toHaveValue(50)
    expect(screen.queryByLabelText(/Monthly allowance/)).not.toBeInTheDocument()
    await user.click(
      screen.getByRole('button', { name: 'Activate subscription' })
    )
    await waitFor(() => expect(callbacks.onSuccess).toHaveBeenCalledOnce())
    expect(put).toHaveBeenCalledWith(
      '/api/subscription/admin/users/3/allowance',
      { weekly_usd: 50, team_id: 0 }
    )
    expect(callbacks.onClose).toHaveBeenCalledOnce()
  })
  it('displays current limits in CNY and sends edited limits in unchanged USD API units', async () => {
    subscriptions = [{ subscription: subscription() }]
    useSystemConfigStore.getState().setConfig({
      currency: {
        ...DEFAULT_CURRENCY_CONFIG,
        quotaDisplayType: 'CNY',
        usdExchangeRate: 7,
      },
    })
    const user = userEvent.setup()
    const put = vi
      .spyOn(api, 'put')
      .mockResolvedValue({ data: { success: true } })
    renderDialog()
    expect(await screen.findByLabelText('Weekly allowance (CNY)')).toHaveValue(
      525
    )
    const weekly = screen.getByLabelText('Weekly allowance (CNY)')
    expect(weekly).toHaveValue(525)
    await user.clear(weekly)
    await user.type(weekly, '350')
    await user.click(screen.getByRole('button', { name: 'Save limits' }))
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        '/api/subscription/admin/users/3/allowance',
        { weekly_usd: 50, team_id: 0, subscription_id: 5 }
      )
    )
  })
  it('offers the default weekly allowance as CNY 350', async () => {
    useSystemConfigStore.getState().setConfig({
      currency: {
        ...DEFAULT_CURRENCY_CONFIG,
        quotaDisplayType: 'CNY',
        usdExchangeRate: 7,
      },
    })
    const put = vi
      .spyOn(api, 'put')
      .mockResolvedValue({ data: { success: true } })
    renderDialog()
    expect(await screen.findByLabelText('Weekly allowance (CNY)')).toHaveValue(
      350
    )
    expect(screen.getByLabelText('Weekly allowance (CNY)')).toHaveValue(350)
    await userEvent.click(
      screen.getByRole('button', { name: 'Activate subscription' })
    )
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        '/api/subscription/admin/users/3/allowance',
        { weekly_usd: 50, team_id: 0 }
      )
    )
  })
  it('keeps edited inputs and the dialog open on a rejected save', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'put').mockResolvedValue({
      data: { success: false, message: 'Allowance update rejected' },
    })
    const callbacks = renderDialog()
    const field = await screen.findByLabelText('Weekly allowance (USD)')
    await user.clear(field)
    await user.type(field, '40')
    await user.click(
      screen.getByRole('button', { name: 'Activate subscription' })
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Allowance update rejected'
    )
    expect(field).toHaveValue(40)
    expect(callbacks.onClose).not.toHaveBeenCalled()
  })
  it('prevents close and duplicate submissions until a pending save completes', async () => {
    const user = userEvent.setup()
    let resolve!: (value: { data: { success: boolean } }) => void
    const put = vi.spyOn(api, 'put').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    const callbacks = renderDialog()
    const button = await screen.findByRole('button', {
      name: 'Activate subscription',
    })
    await user.click(button)
    await waitFor(() => expect(button).toBeDisabled())
    await user.keyboard('{Escape}')
    expect(callbacks.onClose).not.toHaveBeenCalled()
    const form = button.closest('form')
    if (!form) throw new Error('Missing allowance form')
    fireEvent.submit(form)
    expect(put).toHaveBeenCalledOnce()
    await act(async () => resolve({ data: { success: true } }))
    await waitFor(() => expect(callbacks.onClose).toHaveBeenCalledOnce())
  })
  it('rejects a non-positive weekly allowance without sending it', async () => {
    const user = userEvent.setup()
    const put = vi.spyOn(api, 'put')
    renderDialog()
    const field = await screen.findByLabelText('Weekly allowance (USD)')
    await user.clear(field)
    await user.type(field, '0')
    const form = field.closest('form')
    if (!form) throw new Error('Missing allowance form')
    fireEvent.submit(form)
    expect(
      await screen.findByText(
        'Enter a positive weekly allowance within the supported limit.'
      )
    ).toBeVisible()
    expect(put).not.toHaveBeenCalled()
  })
  it('requires confirmation before ending an allowance and preserves both dialogs when the server rejects it', async () => {
    subscriptions = [{ subscription: subscription() }]
    const user = userEvent.setup()
    const post = vi.spyOn(api, 'post').mockResolvedValue({
      data: { success: false, message: 'Cannot end allowance' },
    })
    const callbacks = renderDialog()
    await user.click(
      await screen.findByRole('button', { name: 'End subscription' })
    )
    const confirm = await screen.findByRole('alertdialog', {
      name: 'End subscription?',
    })
    expect(post).not.toHaveBeenCalled()
    await user.click(
      within(confirm).getByRole('button', { name: 'End subscription' })
    )
    expect(await within(confirm).findByRole('alert')).toHaveTextContent(
      'Cannot end allowance'
    )
    expect(callbacks.onClose).not.toHaveBeenCalled()
    expect(post).toHaveBeenCalledWith(
      '/api/subscription/admin/user_subscriptions/5/invalidate',
      { expected_scope: { user_id: 3, workspace_team_id: 0 } }
    )
  })
  it('closes an old cancellation confirmation when the same subscription migrates to another funding scope', async () => {
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 0,
    }
    subscriptions = [{ subscription: subscription() }]
    const post = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ data: { success: true } })
    renderDialog({
      fixedScope: 'personal',
      expectedTeamId: 7,
      expectedFundingVersion: 0,
    })
    await userEvent.click(
      await screen.findByRole('button', { name: 'End subscription' })
    )
    expect(
      await screen.findByRole('alertdialog', { name: 'End subscription?' })
    ).toBeVisible()
    ownedTeam = { ...ownedTeam, funding_version: 1 }
    subscriptions = [{ subscription: subscription({ workspace_team_id: 7 }) }]
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['user-allowance', 3] })
    })
    expect(
      await screen.findByText(
        'The team has changed. Close this dialog and refresh before editing its subscription.'
      )
    ).toBeVisible()
    expect(
      screen.queryByRole('alertdialog', { name: 'End subscription?' })
    ).not.toBeInTheDocument()
    expect(post).not.toHaveBeenCalled()
  })
  it('pins the legacy team context when cancelling a personal subscription from the team shortcut', async () => {
    ownedTeam = {
      id: 7,
      name: 'Research',
      owner_user_id: 3,
      funding_version: 0,
    }
    subscriptions = [{ subscription: subscription() }]
    const post = vi.spyOn(api, 'post').mockResolvedValue({
      data: { success: false, message: 'The team has changed' },
    })
    const callbacks = renderDialog({
      fixedScope: 'personal',
      expectedTeamId: 7,
      expectedFundingVersion: 0,
    })
    await userEvent.click(
      await screen.findByRole('button', { name: 'End subscription' })
    )
    const confirm = await screen.findByRole('alertdialog', {
      name: 'End subscription?',
    })
    await userEvent.click(
      within(confirm).getByRole('button', { name: 'End subscription' })
    )
    expect(await within(confirm).findByRole('alert')).toHaveTextContent(
      'The team has changed'
    )
    expect(post).toHaveBeenCalledWith(
      '/api/subscription/admin/user_subscriptions/5/invalidate',
      {
        expected_scope: { user_id: 3, workspace_team_id: 0 },
        funding_context: { team_id: 7, funding_version: 0 },
      }
    )
    expect(callbacks.onClose).not.toHaveBeenCalled()
  })
})

describe('subscription balance', () => {
  it('does not offer a new allowance while the subscription lookup has failed', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('offline'))
    renderDialog()
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Activate subscription' })
    ).not.toBeInTheDocument()
  })
  it('shows the actual weekly limit and its reset time while both periods remain usable', () => {
    render(
      <SubscriptionBalance subscriptions={[{ subscription: subscription() }]} />
    )
    expect(screen.getByText(/Weekly refresh:/)).toBeVisible()
    expect(screen.getByText('Available now: $50')).toBeVisible()
    expect(screen.queryByText('This month')).not.toBeInTheDocument()
    expect(screen.queryByText('Total Quota')).not.toBeInTheDocument()
    expect(screen.getAllByRole('progressbar')).toHaveLength(1)
    expect(
      screen.queryByText(
        'Unused weekly allowance expires at each refresh and does not roll over.'
      )
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('progressbar', { name: 'This week' })
    ).toHaveAttribute('aria-valuenow', '33.33333333333333')
  })
  it('never presents expiry as a weekly refresh in the final period', () => {
    render(
      <SubscriptionBalance
        subscriptions={[
          {
            subscription: subscription({
              end_time: now + 2 * day,
              weekly_reset_at: now + 6 * day,
            }),
          },
        ]}
      />
    )
    expect(screen.queryByText(/Weekly refresh:/)).not.toBeInTheDocument()
    expect(screen.getByText(/Expires:/)).toBeVisible()
  })
  it('explains that exhausted quota in the last week cannot refresh before expiry', () => {
    render(
      <SubscriptionBalance
        subscriptions={[
          {
            subscription: subscription({
              end_time: now + 2 * day,
              weekly_reset_at: now + 6 * day,
              weekly_used: 75 * quota,
            }),
          },
        ]}
      />
    )
    expect(
      screen.getByText(
        'Weekly allowance used up. This subscription expires before another weekly refresh.'
      )
    ).toBeVisible()
    expect(screen.queryByText(/Weekly refresh:/)).not.toBeInTheDocument()
  })
  it('treats expired allowances as unusable even if unused quota remains', () => {
    render(
      <SubscriptionBalance
        subscriptions={[{ subscription: subscription({ end_time: now - 1 }) }]}
      />
    )
    expect(screen.getByText('Available now: $0')).toBeVisible()
    expect(screen.getByText('Expired')).toBeVisible()
    expect(screen.queryByText(/Weekly refresh:/)).not.toBeInTheDocument()
  })
  it('explains a weekly limit separately from exhausted subscription allowance', () => {
    render(
      <SubscriptionBalance
        subscriptions={[
          { subscription: subscription({ weekly_used: 75 * quota }) },
        ]}
      />
    )
    expect(
      screen.getByText(
        'Weekly allowance used up. It becomes available at the next weekly refresh.'
      )
    ).toBeVisible()
    expect(screen.getByText('Available now: $0')).toBeVisible()
  })
  it('does not impose a hidden total cap on weekly subscriptions after an administrative adjustment', () => {
    render(
      <SubscriptionBalance
        subscriptions={[
          {
            subscription: subscription({
              amount_used: 350 * quota,
              weekly_used: 0,
            }),
          },
        ]}
      />
    )
    expect(screen.getByText('Available now: $75')).toBeVisible()
    expect(screen.getByText(/Weekly refresh:/)).toBeVisible()
    expect(
      screen.queryByText(/Subscription allowance used up/)
    ).not.toBeInTheDocument()
  })
  it('preserves the total cap only for legacy subscriptions without a weekly limit', () => {
    render(
      <SubscriptionBalance
        subscriptions={[
          {
            subscription: subscription({
              amount_used: 300 * quota,
              weekly_amount: 0,
            }),
          },
        ]}
      />
    )
    expect(screen.getByText(/Subscription allowance used up/)).toBeVisible()
    expect(screen.getByText('Available now: $0')).toBeVisible()
  })
  it.each(['zhCN', 'zhTW', 'en', 'fr', 'ja', 'vi', 'ru', 'invalid_locale'])(
    'formats dates safely when the interface uses %s',
    async (language) => {
      await i18next.changeLanguage(language)
      expect(() =>
        render(
          <SubscriptionBalance
            subscriptions={[{ subscription: subscription() }]}
          />
        )
      ).not.toThrow()
    }
  )
})
