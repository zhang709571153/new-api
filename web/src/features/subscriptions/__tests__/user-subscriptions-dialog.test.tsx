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
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'

import { UserSubscriptionsDialog } from '../components/dialogs/user-subscriptions-dialog'

beforeEach(() => {
  const now = Math.floor(Date.now() / 1000)
  const subscriptions = [
    {
      subscription: {
        id: 11,
        user_id: 3,
        plan_id: 2,
        workspace_team_id: 0,
        status: 'active',
        start_time: now - 60,
        end_time: now + 86400,
        amount_total: 100,
        amount_used: 20,
      },
    },
    {
      subscription: {
        id: 12,
        user_id: 3,
        plan_id: 2,
        workspace_team_id: 7,
        status: 'active',
        start_time: now - 60,
        end_time: now + 86400,
        amount_total: 100,
        amount_used: 40,
      },
    },
  ]
  vi.spyOn(api, 'get').mockImplementation(async (url) => ({
    data: {
      success: true,
      data:
        url === '/api/subscription/admin/plans'
          ? [{ plan: { id: 2, title: 'Shared plan', price_amount: 0 } }]
          : subscriptions,
    },
  }))
})

function showDialog() {
  render(
    <UserSubscriptionsDialog
      open
      onOpenChange={vi.fn()}
      user={{ id: 3, username: 'Owner' }}
    />
  )
}

describe('subscription management with one plan in two funding pools', () => {
  it('disables personal-plan reset and permanent deletion on a team row', async () => {
    const user = userEvent.setup()
    const post = vi.spyOn(api, 'post')
    const remove = vi.spyOn(api, 'delete')
    showDialog()
    const row = await screen.findByRole('row', { name: /Team #7/ })
    await user.click(within(row).getByRole('button', { name: 'Actions' }))
    const reset = await screen.findByRole('menuitem', { name: 'Reset quota' })
    expect(reset).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('menuitem', { name: 'Delete' })).toHaveAttribute(
      'aria-disabled',
      'true'
    )
    await user.click(reset)
    expect(post).not.toHaveBeenCalled()
    expect(remove).not.toHaveBeenCalled()
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('keeps the personal-row reset bound to the personal user and plan', async () => {
    const user = userEvent.setup()
    const post = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ data: { success: true, data: { reset_count: 1 } } })
    showDialog()
    const row = await screen.findByRole('row', { name: /Personal/ })
    await user.click(within(row).getByRole('button', { name: 'Actions' }))
    await user.click(
      await screen.findByRole('menuitem', { name: 'Reset quota' })
    )
    const confirm = await screen.findByRole('alertdialog', {
      name: 'Reset subscription quota',
    })
    await user.click(
      within(confirm).getByRole('button', { name: 'Reset quota' })
    )
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        '/api/subscription/admin/users/3/subscriptions/reset',
        { plan_id: 2, advance_reset_time: true }
      )
    )
  })

  it('keeps cancellation bound to the selected team subscription ID', async () => {
    const user = userEvent.setup()
    const post = vi
      .spyOn(api, 'post')
      .mockResolvedValue({ data: { success: true } })
    showDialog()
    const row = await screen.findByRole('row', { name: /Team #7/ })
    await user.click(within(row).getByRole('button', { name: 'Actions' }))
    await user.click(
      await screen.findByRole('menuitem', { name: 'Invalidate' })
    )
    const confirm = await screen.findByRole('alertdialog', {
      name: 'Confirm invalidate',
    })
    await user.click(within(confirm).getByRole('button', { name: 'Continue' }))
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        '/api/subscription/admin/user_subscriptions/12/invalidate',
        { expected_scope: { user_id: 3, workspace_team_id: 7 } }
      )
    )
  })
})
