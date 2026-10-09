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
import { act, render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18next from 'i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import {
  DEFAULT_CURRENCY_CONFIG,
  useSystemConfigStore,
} from '@/stores/system-config-store'

import type { WorkspaceMember, WorkspaceTeam } from '../api'
import { MyTeam } from '../my-team'
import { SupplierTeams } from '../supplier-teams'

let client: QueryClient
let team: WorkspaceTeam
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  useAuthStore.getState().auth.setUser({ id: 3, username: 'owner', role: 100 })
  useSystemConfigStore.getState().setConfig({
    currency: {
      ...DEFAULT_CURRENCY_CONFIG,
      quotaDisplayType: 'CNY',
      usdExchangeRate: 1,
    },
  })
  const member = (
    id: number,
    name: string,
    balance: number,
    spent: number,
    prompt: number,
    completion: number
  ): WorkspaceMember => ({
    user_id: id,
    username: name,
    display_name: name,
    is_owner: false,
    status: 1,
    balance_usd: balance,
    allowance_usd: balance,
    used_usd: spent,
    weekly_used_usd: ({ 4: 3, 5: 20, 6: 0 } as Record<number, number>)[id],
    requests: 1,
    prompt_tokens: prompt,
    completion_tokens: completion,
    masked_key: 'sk-****',
  })
  team = {
    team: { id: 1, name: 'Sortable team', owner_user_id: 3 },
    members: [
      member(4, 'Alice', 2, 100, 1, 99),
      member(5, 'Bob', 100, 2, 1, 1),
      member(6, 'Carol', 10, 10, 8, 2),
    ],
  }
  vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data: {
      success: true,
      data: path === '/api/team/workspaces' ? { teams: [team] } : team,
    },
  }))
})
afterEach(async () => {
  client.clear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
  useSystemConfigStore.setState(useSystemConfigStore.getInitialState(), true)
  i18next.removeResourceBundle('zhCN', 'translation')
  await i18next.changeLanguage('en')
})
async function renderTeams(supplier = false, expand = true) {
  const router = createRouter({
    routeTree: createRootRoute({
      component: supplier ? SupplierTeams : MyTeam,
    }),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  await router.load()
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  if (supplier && !expand) {
    await screen.findAllByRole('button', { name: 'Member breakdown' })
    return
  }
  if (supplier) {
    await userEvent.click(
      await screen.findByRole('button', { name: 'Member breakdown' })
    )
  }
  await screen.findByText('Alice')
}
function memberNames(table = screen.getByRole('table')) {
  return within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) =>
      within(row)
        .getAllByRole('cell')[1]
        .textContent?.replaceAll(/Active|Paused|Owner/g, '')
    )
}

describe('team member sorting', () => {
  it('shows subscription status and opens team management from the read-only breakdown', async () => {
    await renderTeams(true, false)
    expect(screen.getByText('Monthly subscription')).toBeVisible()
    expect(screen.getByText('No subscription')).toBeVisible()
    expect(
      screen.queryByRole('button', { name: /Edit subscription/ })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Manage team' })).toHaveAttribute(
      'href',
      '/users?team=1'
    )
  })

  it('starts teams collapsed, expands them independently, collapses all and searches members', async () => {
    if (!team.team) throw new Error('Missing team fixture')
    const second = {
      ...team,
      team: { ...team.team, id: 2, name: 'Second team' },
      members: [{ ...team.members[0], username: 'Dora', display_name: 'Dora' }],
    }
    vi.mocked(api.get).mockResolvedValue({
      data: { success: true, data: { teams: [team, second] } },
    })
    await renderTeams(true, false)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    const triggers = screen.getAllByRole('button', { name: 'Member breakdown' })
    await userEvent.click(triggers[0])
    expect(screen.getAllByRole('table')).toHaveLength(1)
    expect(triggers[1]).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(triggers[1])
    expect(screen.getAllByRole('table')).toHaveLength(2)
    await userEvent.click(screen.getByRole('button', { name: 'Collapse all' }))
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    await userEvent.type(
      screen.getByRole('textbox', { name: 'Search teams or members' }),
      'Dora'
    )
    expect(screen.queryByText('Sortable team')).not.toBeInTheDocument()
    expect(screen.getByText('Second team')).toBeVisible()
  })
  it.each([
    {
      column: 'Available balance',
      asc: ['Alice', 'Carol', 'Bob'],
      desc: ['Bob', 'Carol', 'Alice'],
    },
    {
      column: 'Cumulative usage',
      asc: ['Bob', 'Carol', 'Alice'],
      desc: ['Alice', 'Carol', 'Bob'],
    },
    {
      column: 'This week used',
      asc: ['Carol', 'Alice', 'Bob'],
      desc: ['Bob', 'Alice', 'Carol'],
    },
    {
      column: 'Tokens used',
      asc: ['Bob', 'Carol', 'Alice'],
      desc: ['Alice', 'Carol', 'Bob'],
    },
    {
      column: 'Member',
      asc: ['Alice', 'Bob', 'Carol'],
      desc: ['Carol', 'Bob', 'Alice'],
    },
  ])(
    'sorts $column ascending and descending using member values',
    async ({ column, asc, desc }) => {
      const user = userEvent.setup()
      await renderTeams()
      await user.click(screen.getByRole('button', { name: column }))
      await user.click(screen.getByRole('menuitem', { name: 'Asc' }))
      expect(memberNames()).toEqual(asc)
      expect(
        screen.getByRole('columnheader', { name: column })
      ).toHaveAttribute('aria-sort', 'ascending')
      await user.click(screen.getByRole('button', { name: column }))
      await user.click(screen.getByRole('menuitem', { name: 'Desc' }))
      expect(memberNames()).toEqual(desc)
      expect(
        screen.getByRole('columnheader', { name: column })
      ).toHaveAttribute('aria-sort', 'descending')
    }
  )

  it('preserves equal-value order and keeps missing values last in either direction', async () => {
    team.members[2].used_usd = 2
    team.members.push({
      ...team.members[0],
      user_id: 7,
      username: 'Dana',
      display_name: 'Dana',
      used_usd: Number.NaN,
    })
    team.members.push({
      ...team.members[0],
      user_id: 8,
      username: 'Eve',
      display_name: 'Eve',
      used_usd: undefined as unknown as number,
    })
    const user = userEvent.setup()
    await renderTeams()
    for (const direction of ['Asc', 'Desc']) {
      await user.click(screen.getByRole('button', { name: 'Cumulative usage' }))
      await user.click(screen.getByRole('menuitem', { name: direction }))
      const expected =
        direction === 'Asc'
          ? ['Bob', 'Carol', 'Alice', 'Dana', 'Eve']
          : ['Alice', 'Bob', 'Carol', 'Dana', 'Eve']
      expect(memberNames()).toEqual(expected)
    }
    expect(screen.getByRole('row', { name: /Dana/ })).toHaveTextContent('—')
    expect(screen.getByRole('row', { name: /Eve/ })).toHaveTextContent('—')
  })

  it('keeps missing weekly usage last and preserves cumulative totals when the week resets', async () => {
    team.members[1].weekly_used_usd = undefined
    const user = userEvent.setup()
    await renderTeams()
    for (const direction of ['Asc', 'Desc']) {
      await user.click(screen.getByRole('button', { name: 'This week used' }))
      await user.click(screen.getByRole('menuitem', { name: direction }))
      expect(memberNames()).toEqual(
        direction === 'Asc'
          ? ['Carol', 'Alice', 'Bob']
          : ['Alice', 'Carol', 'Bob']
      )
    }
    expect(
      within(screen.getByRole('row', { name: /Bob/ })).getAllByRole('cell')[3]
    ).toHaveTextContent('—')
    team = {
      ...team,
      members: team.members.map((member) => ({
        ...member,
        weekly_used_usd: 0,
      })),
    }
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['workspace-team'] })
    })
    await waitFor(() =>
      expect(
        within(screen.getByRole('row', { name: /Alice/ })).getAllByRole(
          'cell'
        )[3]
      ).toHaveTextContent('¥0')
    )
    const cells = within(
      screen.getByRole('row', { name: /Alice/ })
    ).getAllByRole('cell')
    expect(cells[3]).toHaveTextContent('¥0')
    expect(cells[4]).toHaveTextContent('¥100')
    expect(cells[5]).toHaveTextContent('100')
  })

  it.each([false, true])(
    'formats compact team tokens but sorts their original numeric values (supplier=%s)',
    async (supplier) => {
      team.members[0].prompt_tokens = 100000000
      team.members[0].completion_tokens = 0
      team.members[1].prompt_tokens = 10000
      team.members[1].completion_tokens = 0
      team.members[2].prompt_tokens = 1000000
      team.members[2].completion_tokens = 0
      team.prompt_tokens = 101010000
      team.completion_tokens = 0
      i18next.addResourceBundle('zhCN', 'translation', {
        'Tokens used': 'Tokens used',
      })
      const user = userEvent.setup()
      await renderTeams(supplier)
      await act(async () => {
        await i18next.changeLanguage('zhCN')
      })
      await user.click(screen.getByRole('button', { name: 'Tokens used' }))
      await user.click(screen.getByRole('menuitem', { name: 'Asc' }))
      expect(memberNames()).toEqual(['Bob', 'Carol', 'Alice'])
      expect(screen.getByText('1.00万')).toBeVisible()
      expect(screen.getByText('1.00百万')).toBeVisible()
      expect(screen.getByText('1.00亿')).toBeVisible()
      expect(screen.getByText('1.01亿')).toBeVisible()
    }
  )

  it('edits and removes the selected member after changing sort direction', async () => {
    const user = userEvent.setup()
    const patch = vi
      .spyOn(api, 'patch')
      .mockResolvedValue({ data: { success: true } })
    const remove = vi.spyOn(api, 'delete').mockImplementation(async () => {
      team = {
        ...team,
        members: team.members.filter((member) => member.user_id !== 5),
      }
      return { data: { success: true } }
    })
    await renderTeams()
    await user.click(screen.getByRole('button', { name: 'Available balance' }))
    await user.click(screen.getByRole('menuitem', { name: 'Desc' }))
    expect(memberNames()[0]).toBe('Bob')
    await user.click(
      within(screen.getByRole('row', { name: /Bob/ })).getByRole('button', {
        name: 'Actions',
      })
    )
    await user.click(screen.getByRole('menuitem', { name: 'Set allowance' }))
    const edit = await screen.findByRole('dialog', { name: 'Set allowance' })
    const amount = within(edit).getByLabelText('Remaining allowance (CNY)')
    await user.clear(amount)
    await user.type(amount, '12')
    await user.click(within(edit).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith('/api/workspace/team/members/5', {
        allowance_usd: 12,
      })
    )
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    )
    await user.click(screen.getByRole('button', { name: 'Available balance' }))
    await user.click(screen.getByRole('menuitem', { name: 'Asc' }))
    expect(memberNames()[2]).toBe('Bob')
    await user.click(
      within(screen.getByRole('row', { name: /Bob/ })).getByRole('button', {
        name: 'Actions',
      })
    )
    await user.click(screen.getByRole('menuitem', { name: 'Remove member' }))
    const confirmation = await screen.findByRole('alertdialog', {
      name: 'Remove member',
    })
    expect(confirmation).toHaveTextContent('Bob')
    expect(remove).not.toHaveBeenCalled()
    await user.click(
      within(confirmation).getByRole('button', { name: 'Remove member' })
    )
    await waitFor(() =>
      expect(remove).toHaveBeenCalledWith('/api/workspace/team/members/5', {
        data: { team_id: 1 },
      })
    )
    await waitFor(() => expect(memberNames()).toEqual(['Alice', 'Carol']))
  })

  it('supports sorting in supplier teams without exposing owner actions', async () => {
    const user = userEvent.setup()
    await renderTeams(true)
    await user.click(screen.getByRole('button', { name: 'Tokens used' }))
    await user.click(screen.getByRole('menuitem', { name: 'Desc' }))
    expect(memberNames()).toEqual(['Alice', 'Carol', 'Bob'])
    expect(
      screen.queryByRole('button', { name: 'Remove member' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Set allowance' })
    ).not.toBeInTheDocument()
  })
})
