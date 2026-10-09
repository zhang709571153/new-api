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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  UserSubscription,
  UserSubscriptionRecord,
} from '@/features/subscriptions/types'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import {
  useSystemConfigStore,
  DEFAULT_CURRENCY_CONFIG,
} from '@/stores/system-config-store'

import type { WorkspaceTeam } from '../api'
import { MyTeam } from '../my-team'

let client: QueryClient
let data: WorkspaceTeam | null
function updateTeam(changes: Partial<WorkspaceTeam>) {
  if (!data) throw new Error('Missing team fixture')
  data = { ...data, ...changes }
  return data
}
function subscription(
  overrides: Partial<UserSubscription> = {}
): UserSubscriptionRecord {
  const now = Math.floor(Date.now() / 1000)
  return {
    subscription: {
      id: 1,
      user_id: 3,
      workspace_team_id: 1,
      plan_id: 0,
      status: 'active',
      start_time: now - 100,
      end_time: now + 3600,
      amount_total: 1000,
      amount_used: 0,
      weekly_amount: 250,
      weekly_used: 0,
      ...overrides,
    },
  }
}
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  useAuthStore.getState().auth.setUser({ id: 3, username: 'owner', role: 1 })
  data = {
    team: { id: 1, name: 'Design studio', owner_user_id: 3 },
    pool_balance_usd: 10,
    used_usd: 1.5,
    members: [
      {
        user_id: 3,
        username: 'owner',
        display_name: 'Owner',
        is_owner: true,
        status: 1,
        balance_usd: 10,
        allowance_usd: 10,
        used_usd: 1,
        requests: 1,
        prompt_tokens: 10,
        completion_tokens: 5,
        masked_key: 'sk-***own',
      },
      {
        user_id: 4,
        username: 'alice',
        display_name: 'Alice',
        is_owner: false,
        status: 1,
        balance_usd: 2,
        allowance_usd: 2,
        used_usd: 0.5,
        requests: 2,
        prompt_tokens: 20,
        completion_tokens: 10,
        masked_key: 'sk-***mem',
      },
    ],
  }
  vi.spyOn(api, 'get').mockImplementation(async () => ({
    data: { success: true, data },
  }))
})
afterEach(() => {
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
  client.clear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
})
async function renderTeam() {
  const router = createRouter({
    routeTree: createRootRoute({ component: MyTeam }),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  await router.load()
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
async function chooseMemberAction(
  user: ReturnType<typeof userEvent.setup>,
  action: string,
  row?: HTMLElement
) {
  const target = row ?? (await screen.findByText('Alice')).closest('tr')
  if (!target) throw new Error('Missing member row')
  await user.click(within(target).getByRole('button', { name: 'Actions' }))
  await user.click(await screen.findByRole('menuitem', { name: action }))
}

describe('self-service teams', () => {
  it.each([
    { action: 'Edit nickname', title: 'Edit nickname', role: 'dialog' },
    { action: 'Set allowance', title: 'Set allowance', role: 'dialog' },
    { action: 'API key', title: 'Member API key', role: 'dialog' },
    { action: 'Remove member', title: 'Remove member', role: 'alertdialog' },
  ])(
    'moves keyboard focus into $action and back to the member action trigger',
    async ({ action, title, role }) => {
      const user = userEvent.setup()
      await renderTeam()
      const row = (await screen.findByText('Alice')).closest('tr')
      if (!row) throw new Error('Missing member row')
      const trigger = within(row).getByRole('button', { name: 'Actions' })
      trigger.focus()
      await user.keyboard('{Enter}')
      const item = await screen.findByRole('menuitem', { name: action })
      item.focus()
      await user.keyboard('{Enter}')
      const dialog = await screen.findByRole(role, { name: title })
      await waitFor(() =>
        expect(dialog).toContainElement(document.activeElement as HTMLElement)
      )
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
      await user.keyboard('{Escape}')
      await waitFor(() =>
        expect(screen.queryByRole(role)).not.toBeInTheDocument()
      )
      await waitFor(() => expect(trigger).toHaveFocus())
    }
  )
  it('keeps member actions collapsed and opens an accessible action menu', async () => {
    const user = userEvent.setup()
    await renderTeam()
    const row = (await screen.findByText('Alice')).closest('tr')
    if (!row) throw new Error('Missing member row')
    expect(
      within(row).queryByRole('button', { name: 'Edit nickname' })
    ).not.toBeInTheDocument()
    expect(
      within(row).queryByRole('button', { name: 'API key' })
    ).not.toBeInTheDocument()
    await user.click(within(row).getByRole('button', { name: 'Actions' }))
    expect(
      screen.getByRole('menuitem', { name: 'Edit nickname' })
    ).toBeVisible()
    expect(
      screen.getByRole('menuitem', { name: 'Set allowance' })
    ).toBeVisible()
    expect(
      screen.getByRole('menuitem', { name: 'Remove member' })
    ).toBeVisible()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    const ownerRow = screen.getByRole('row', { name: /Owner/ })
    await user.click(within(ownerRow).getByRole('button', { name: 'Actions' }))
    expect(
      screen.getByRole('menuitem', { name: 'Edit nickname' })
    ).toBeVisible()
    expect(screen.getByRole('menuitem', { name: 'API key' })).toBeVisible()
    for (const action of ['Set allowance', 'Pause', 'Remove member']) {
      expect(
        screen.queryByRole('menuitem', { name: action })
      ).not.toBeInTheDocument()
    }
  })
  it('shows a permanent invitation and replaces it only after confirmation', async () => {
    const user = userEvent.setup()
    const post = vi.spyOn(api, 'post').mockImplementation(async (path) => ({
      data: {
        success: true,
        data: {
          code: path.endsWith('/rotate')
            ? 'replacement-code'
            : 'persistent-code',
          expires_at: 0,
        },
      },
    }))
    await renderTeam()
    await user.click(
      await screen.findByRole('button', { name: 'Invite member' })
    )
    expect(await screen.findByText('persistent-code')).toBeVisible()
    expect(screen.queryByText(/1970/)).not.toBeInTheDocument()
    await user.click(
      screen.getByRole('button', { name: 'Replace invitation code' })
    )
    const dialog = await screen.findByRole('alertdialog', {
      name: 'Replace invitation code',
    })
    expect(post).not.toHaveBeenCalledWith('/api/workspace/team/invites/rotate')
    await user.click(
      within(dialog).getByRole('button', { name: 'Replace invitation code' })
    )
    expect(await screen.findByText('replacement-code')).toBeVisible()
    expect(post).toHaveBeenCalledWith('/api/workspace/team/invites/rotate')
  })
  it.each([
    { userId: 3, action: 'Leave and dissolve team' },
    { userId: 4, action: 'Leave team' },
  ])(
    'confirms $action and returns to personal mode',
    async ({ userId, action }) => {
      useAuthStore
        .getState()
        .auth.setUser({ id: userId, username: 'user', role: 1 })
      const user = userEvent.setup()
      const post = vi.spyOn(api, 'post').mockImplementation(async () => {
        data = null
        return { data: { success: true } }
      })
      await renderTeam()
      await user.click(await screen.findByRole('button', { name: action }))
      let dialog = await screen.findByRole('alertdialog', { name: action })
      await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
      expect(post).not.toHaveBeenCalled()
      await user.click(screen.getByRole('button', { name: action }))
      dialog = await screen.findByRole('alertdialog', { name: action })
      await user.click(within(dialog).getByRole('button', { name: action }))
      await waitFor(() =>
        expect(post).toHaveBeenCalledWith('/api/workspace/team/leave', {
          team_id: 1,
        })
      )
      expect(
        await screen.findByRole('button', { name: 'Join team' })
      ).toBeVisible()
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    }
  )

  it('keeps the team and confirmation visible when leaving fails', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'post').mockResolvedValue({
      data: { success: false, message: 'Please refresh' },
    })
    await renderTeam()
    await user.click(
      await screen.findByRole('button', { name: 'Leave and dissolve team' })
    )
    const dialog = await screen.findByRole('alertdialog')
    await user.click(
      within(dialog).getByRole('button', { name: 'Leave and dissolve team' })
    )
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Please refresh'
    )
    expect(screen.getByText('Design studio')).toBeVisible()
  })

  it('lets the owner edit and clear a nickname without changing allowance or status', async () => {
    const user = userEvent.setup()
    const patch = vi
      .spyOn(api, 'patch')
      .mockImplementation(async (_url, body) => {
        if (data) {
          data.members[1].display_name = (
            body as { display_name: string }
          ).display_name
        }
        return { data: { success: true } }
      })
    await renderTeam()
    let row = await screen.findByRole('row', { name: /Alice/ })
    await chooseMemberAction(user, 'Edit nickname', row)
    let dialog = await screen.findByRole('dialog', { name: 'Edit nickname' })
    let input = within(dialog).getByLabelText('Nickname')
    expect(input).toHaveAttribute('placeholder', 'Optional')
    await user.clear(input)
    await user.type(input, ' 小韩 ')
    await user.click(
      within(dialog).getByRole('button', { name: 'Save nickname' })
    )
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith('/api/workspace/team/members/4', {
        display_name: '小韩',
      })
    )
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    )
    row = await screen.findByRole('row', { name: /小韩/ })
    await chooseMemberAction(user, 'Edit nickname', row)
    dialog = await screen.findByRole('dialog', { name: 'Edit nickname' })
    input = within(dialog).getByLabelText('Nickname')
    await user.clear(input)
    await user.click(
      within(dialog).getByRole('button', { name: 'Save nickname' })
    )
    await waitFor(() =>
      expect(patch).toHaveBeenLastCalledWith('/api/workspace/team/members/4', {
        display_name: '',
      })
    )
    expect(await screen.findByText('alice')).toBeVisible()
  })

  it('keeps a failed nickname edit open and hides the action from ordinary members', async () => {
    const user = userEvent.setup()
    const patch = vi
      .spyOn(api, 'patch')
      .mockResolvedValue({ data: { success: false, message: 'Save denied' } })
    await renderTeam()
    const row = await screen.findByRole('row', { name: /Alice/ })
    await chooseMemberAction(user, 'Edit nickname', row)
    const dialog = await screen.findByRole('dialog')
    const input = within(dialog).getByLabelText('Nickname')
    await user.clear(input)
    await user.type(input, '123456789012345678901')
    await user.click(
      within(dialog).getByRole('button', { name: 'Save nickname' })
    )
    expect(
      await within(dialog).findByText('Nickname must be 20 characters or fewer')
    ).toBeVisible()
    expect(patch).not.toHaveBeenCalled()
    await user.clear(input)
    await user.type(input, 'Retry name')
    await user.click(
      within(dialog).getByRole('button', { name: 'Save nickname' })
    )
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Save denied'
    )
    expect(input).toHaveValue('Retry name')
    await user.keyboard('{Escape}')
    useAuthStore.getState().auth.setUser({ id: 4, username: 'alice', role: 1 })
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Actions' })
      ).not.toBeInTheDocument()
    )
  })
  it('offers buying a team subscription or invitation-based joining without direct creation', async () => {
    data = null
    const post = vi.spyOn(api, 'post')
    await renderTeam()
    expect(
      await screen.findByRole('button', { name: 'Join team' })
    ).toBeVisible()
    const purchase = screen.getAllByRole('button', {
      name: 'Buy team subscription',
    })
    expect(purchase).toHaveLength(1)
    expect(purchase[0]).toHaveAttribute('href', '/wallet?scope=team')
    expect(
      screen.getByText(
        'Your team is created after payment succeeds. You can also join with an invitation code.'
      )
    ).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Create team' })
    ).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Team name')).not.toBeInTheDocument()
    expect(post).not.toHaveBeenCalled()
  })
  it('never offers an owner a second team or a join action', async () => {
    await renderTeam()
    await screen.findByText('Design studio')
    expect(
      screen.queryByRole('button', { name: 'Create team' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Join team' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Buy team subscription' })
    ).not.toBeInTheDocument()
  })
  it('hides an already-open join dialog when team ownership arrives', async () => {
    const ownedTeam = data
    data = null
    const user = userEvent.setup()
    const post = vi.spyOn(api, 'post')
    await renderTeam()
    await user.click(await screen.findByRole('button', { name: 'Join team' }))
    const dialog = await screen.findByRole('dialog', { name: 'Join team' })
    expect(dialog).toBeVisible()
    expect(
      within(dialog).queryByRole('button', { name: 'Buy team subscription' })
    ).not.toBeInTheDocument()
    act(() => {
      data = ownedTeam
      client.setQueryData(['workspace-team', 3], ownedTeam)
    })
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: 'Join team' })
      ).not.toBeInTheDocument()
    )
    expect(screen.getByText('Design studio')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Buy team subscription' })
    ).not.toBeInTheDocument()
    expect(post).not.toHaveBeenCalled()
  })
  it('does not offer joining when the workspace already identifies an owner', async () => {
    vi.mocked(api.get).mockImplementation(async (path) => ({
      data: {
        success: true,
        data: path === '/api/workspace/team' ? null : { mode: 'owner' },
      },
    }))
    await renderTeam()
    await waitFor(() =>
      expect(client.getQueryState(['workspace-team', 3])?.status).toBe(
        'success'
      )
    )
    expect(
      screen.queryByRole('button', { name: 'Join team' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Buy team subscription' })
    ).not.toBeInTheDocument()
  })
  it('offers a member one team-purchase link and explains the change after payment', async () => {
    useAuthStore.getState().auth.setUser({ id: 4, username: 'alice', role: 1 })
    await renderTeam()
    await screen.findByText('Design studio')
    const purchase = screen.getAllByRole('button', {
      name: 'Buy team subscription',
    })
    expect(purchase).toHaveLength(1)
    expect(purchase[0]).toHaveAttribute('href', '/wallet?scope=team')
    expect(
      screen.getByText(
        'After payment succeeds, you will leave this team and become the owner of your new team.'
      )
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Leave team' })).toBeVisible()
  })
  it('joins using a supplied invitation code', async () => {
    data = null
    const user = userEvent.setup()
    const post = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ data: { success: true } })
    await renderTeam()
    await user.click(await screen.findByRole('button', { name: 'Join team' }))
    const dialog = await screen.findByRole('dialog', { name: 'Join team' })
    await user.type(
      within(dialog).getByLabelText('Invitation code'),
      'test-invite'
    )
    await user.click(within(dialog).getByRole('button', { name: 'Join team' }))
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('/api/workspace/team/join', {
        code: 'test-invite',
      })
    )
  })
  it('keeps a rejected invitation open and displays the backend error', async () => {
    data = null
    const user = userEvent.setup()
    vi.spyOn(api, 'post').mockResolvedValue({
      data: { success: false, message: 'Team owners cannot join another team' },
    })
    await renderTeam()
    await user.click(await screen.findByRole('button', { name: 'Join team' }))
    const dialog = await screen.findByRole('dialog', { name: 'Join team' })
    await user.type(
      within(dialog).getByLabelText('Invitation code'),
      'rejected-invite'
    )
    await user.click(within(dialog).getByRole('button', { name: 'Join team' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Team owners cannot join another team'
    )
    expect(within(dialog).getByLabelText('Invitation code')).toHaveValue(
      'rejected-invite'
    )
  })
  it('allows owner dissolution with an unexpired subscription and explains the remaining allowance', async () => {
    updateTeam({ has_unexpired_subscription: true })
    await renderTeam()
    await screen.findByText('Design studio')
    expect(
      screen.getByRole('button', { name: 'Leave and dissolve team' })
    ).toBeEnabled()
    await userEvent.click(
      screen.getByRole('button', { name: 'Leave and dissolve team' })
    )
    expect(
      screen.getByText(
        'Any remaining team subscription allowance will no longer be usable. No automatic refund or transfer to personal balance is made. Subscription and billing records are kept.'
      )
    ).toBeVisible()
  })
  it.each([
    { status: 'active', startOffset: -100, endOffset: 3600, blocked: true },
    { status: 'active', startOffset: 3600, endOffset: 7200, blocked: true },
    { status: 'active', startOffset: -3600, endOffset: -1, blocked: false },
    { status: 'cancelled', startOffset: -100, endOffset: 3600, blocked: false },
  ])(
    'handles legacy subscription status=$status start=$startOffset end=$endOffset',
    async ({ status, startOffset, endOffset }) => {
      const now = Math.floor(Date.now() / 1000)
      updateTeam({
        subscriptions: [
          subscription({
            status,
            start_time: now + startOffset,
            end_time: now + endOffset,
          }),
        ],
      })
      await renderTeam()
      await screen.findByText('Design studio')
      const dissolve = screen.queryByRole('button', {
        name: 'Leave and dissolve team',
      })
      expect(dissolve).toBeVisible()
    }
  )
  it('uses the authoritative false flag instead of older subscription detail', async () => {
    updateTeam({
      has_unexpired_subscription: false,
      subscriptions: [subscription()],
    })
    await renderTeam()
    expect(
      await screen.findByRole('button', { name: 'Leave and dissolve team' })
    ).toBeVisible()
  })
  it('keeps owner confirmation available when an unexpired subscription appears without submitting automatically', async () => {
    const user = userEvent.setup()
    const post = vi.spyOn(api, 'post')
    await renderTeam()
    await user.click(
      await screen.findByRole('button', { name: 'Leave and dissolve team' })
    )
    expect(await screen.findByRole('alertdialog')).toBeVisible()
    act(() => {
      client.setQueryData(
        ['workspace-team', 3],
        updateTeam({ has_unexpired_subscription: true })
      )
    })
    expect(screen.getByRole('alertdialog')).toBeVisible()
    expect(post).not.toHaveBeenCalled()
    act(() => {
      client.setQueryData(
        ['workspace-team', 3],
        updateTeam({ has_unexpired_subscription: false })
      )
    })
    expect(screen.getByRole('alertdialog')).toBeVisible()
  })
  it('still lets ordinary members leave a team with an unexpired subscription', async () => {
    updateTeam({ has_unexpired_subscription: true })
    useAuthStore.getState().auth.setUser({ id: 4, username: 'alice', role: 1 })
    const user = userEvent.setup()
    const post = vi.spyOn(api, 'post').mockImplementation(async () => {
      data = null
      return { data: { success: true } }
    })
    await renderTeam()
    await user.click(await screen.findByRole('button', { name: 'Leave team' }))
    const dialog = await screen.findByRole('alertdialog', {
      name: 'Leave team',
    })
    await user.click(within(dialog).getByRole('button', { name: 'Leave team' }))
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('/api/workspace/team/leave', {
        team_id: 1,
      })
    )
    expect(
      await screen.findByRole('button', { name: 'Join team' })
    ).toBeVisible()
  })
  it('lets the owner set remaining allowance and pauses without resubmitting a stale limit', async () => {
    const user = userEvent.setup()
    useSystemConfigStore.getState().setConfig({
      currency: {
        ...DEFAULT_CURRENCY_CONFIG,
        quotaDisplayType: 'CNY',
        usdExchangeRate: 7,
      },
    })
    const patch = vi
      .spyOn(api, 'patch')
      .mockResolvedValue({ data: { success: true } })
    await renderTeam()
    await screen.findByText('Alice')
    await chooseMemberAction(user, 'Set allowance')
    const dialog = await screen.findByRole('dialog', { name: 'Set allowance' })
    const amount = within(dialog).getByLabelText('Remaining allowance (CNY)')
    expect(amount).toHaveValue(14)
    await user.clear(amount)
    await user.type(amount, '36.75')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith('/api/workspace/team/members/4', {
        allowance_usd: 5.25,
      })
    )
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    )
    await chooseMemberAction(user, 'Pause')
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith('/api/workspace/team/members/4', {
        status: 2,
      })
    )
  })
  it('edits the weekly member limit for an independent team without treating it as remaining funds', async () => {
    if (!data?.team) throw new Error('Missing team fixture')
    data.team.funding_version = 1
    data.members[1].allowance_usd = 20
    data.members[1].balance_usd = 3
    data.members[1].weekly_used_usd = 17
    data.members[1].opening_used_usd = 12
    data.members[1].effective_weekly_allowance_usd = 32
    const user = userEvent.setup()
    const patch = vi
      .spyOn(api, 'patch')
      .mockResolvedValue({ data: { success: true } })
    await renderTeam()
    await screen.findByText('Alice')
    expect(
      screen.getByRole('columnheader', { name: 'Weekly remaining' })
    ).toBeVisible()
    await chooseMemberAction(user, 'Member weekly allowance')
    const dialog = await screen.findByRole('dialog', {
      name: 'Member weekly allowance',
    })
    const amount = within(dialog).getByLabelText(
      'Member weekly allowance (USD)'
    )
    expect(amount).toHaveValue(20)
    await user.clear(amount)
    await user.type(amount, '30')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith('/api/workspace/team/members/4', {
        allowance_usd: 30,
      })
    )
  })
  it('shows shared balance and every member usage to members without owner controls', async () => {
    useAuthStore.getState().auth.setUser({ id: 4, username: 'alice', role: 1 })
    await renderTeam()
    await screen.findByText('Alice')
    expect(screen.getByText('Shared balance')).toBeVisible()
    expect(screen.getAllByText('Cumulative usage')[0]).toBeVisible()
    expect(screen.getByText('Usage by member')).toBeVisible()
    expect(screen.getByText('$1.5')).toBeVisible()
    expect(screen.getByText('$0.5')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Invite member' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Set allowance' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'API key' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Pause' })
    ).not.toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(3)
  })
  it('loads a member key when opened, supports masking, and clears it when closed', async () => {
    const user = userEvent.setup()
    const secret = 'sk-member-secret-1234567890'
    const post = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ data: { success: true, data: { api_key: secret } } })
    await renderTeam()
    const row = (await screen.findByText('Alice')).closest('tr')
    if (!row) throw new Error('Missing member row')
    await chooseMemberAction(user, 'API key', row)
    await waitFor(() =>
      expect(
        screen.getByLabelText('Member API key', { selector: 'input' })
      ).toHaveValue(secret)
    )
    await user.click(screen.getByRole('button', { name: 'Hide key' }))
    expect(
      screen.getByLabelText('Member API key', { selector: 'input' })
    ).toHaveValue('sk-****')
    await user.click(screen.getByRole('button', { name: 'Show key' }))
    expect(
      screen.getByLabelText('Member API key', { selector: 'input' })
    ).toHaveValue(secret)
    await user.keyboard('{Escape}')
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    )
    expect(document.body.innerHTML).not.toContain(secret)
    await chooseMemberAction(user, 'API key', row)
    await waitFor(() =>
      expect(
        screen.getByLabelText('Member API key', { selector: 'input' })
      ).toHaveValue(secret)
    )
    expect(post).toHaveBeenCalledWith(
      '/api/workspace/team/members/4/key/reveal'
    )
  })
  it('removes a member only after confirmation and keeps the owner row intact', async () => {
    const user = userEvent.setup()
    const remove = vi.spyOn(api, 'delete').mockImplementation(async () => {
      if (!data) throw new Error('Missing team')
      data = {
        ...data,
        members: data.members.filter((member) => member.user_id !== 4),
      }
      return { data: { success: true } }
    })
    await renderTeam()
    const row = (await screen.findByText('Alice')).closest('tr')
    if (!row) throw new Error('Missing member row')
    expect(
      screen.queryByRole('button', { name: 'Remove member' })
    ).not.toBeInTheDocument()
    await chooseMemberAction(user, 'Remove member', row)
    let dialog = await screen.findByRole('alertdialog', {
      name: 'Remove member',
    })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(remove).not.toHaveBeenCalled()
    await chooseMemberAction(user, 'Remove member', row)
    dialog = await screen.findByRole('alertdialog', { name: 'Remove member' })
    expect(dialog).toHaveTextContent('Alice')
    await user.click(
      within(dialog).getByRole('button', { name: 'Remove member' })
    )
    await waitFor(() =>
      expect(screen.queryByText('Alice')).not.toBeInTheDocument()
    )
    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: 'Design studio' })
      ).toHaveFocus()
    )
    expect(remove).toHaveBeenCalledWith('/api/workspace/team/members/4', {
      data: { team_id: 1 },
    })
    expect(
      screen.getByRole('button', { name: 'Leave and dissolve team' })
    ).toBeVisible()
  })
  it('shows failed team loading instead of offering creation on an uncertain membership', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('offline'))
    await renderTeam()
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Create team' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Buy team subscription' })
    ).not.toBeInTheDocument()
  })
})
