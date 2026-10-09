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
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useForm } from 'react-hook-form'
import { describe, expect, it, vi } from 'vitest'

import { Button } from '@/components/ui/button'
import { Form } from '@/components/ui/form'

import { CNYPlanFields } from '../components/cny-plan-fields'
import {
  formValuesToPlanPayload,
  PLAN_FORM_DEFAULTS,
  type PlanFormValues,
} from '../lib/plan-form'
import type { PlanPayload } from '../types'

function Editor(props: {
  scope?: 'personal' | 'team'
  editing?: boolean
  onSave: (payload: PlanPayload) => void
}) {
  const form = useForm<PlanFormValues>({
    defaultValues: {
      ...PLAN_FORM_DEFAULTS,
      title: 'Example',
      weekly_amount: 100,
      price_amount: 200,
      funding_scope: props.scope ?? 'personal',
    },
  })
  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit((values) =>
          props.onSave(formValuesToPlanPayload(values))
        )}
      >
        <CNYPlanFields scopeReadOnly={props.editing} />
        <Button type='submit'>Save changes</Button>
      </form>
    </Form>
  )
}

describe('admin CNY subscription scope', () => {
  it('selects a new team plan and removes the personal PAYGO option before submitting', async () => {
    const user = userEvent.setup()
    const save = vi.fn()
    render(<Editor onSave={save} />)
    expect(
      screen.getByRole('switch', {
        name: 'Use PAYGO after weekly allowance is exhausted',
      })
    ).toBeChecked()
    await user.click(
      screen.getByRole('combobox', { name: 'Subscription type' })
    )
    await user.click(
      await screen.findByRole('option', { name: 'Team subscription' })
    )
    expect(
      screen.queryByRole('switch', {
        name: 'Use PAYGO after weekly allowance is exhausted',
      })
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        plan: expect.objectContaining({
          funding_scope: 'team',
          allow_wallet_overflow: false,
        }),
      })
    )
  })

  it.each([
    {
      scope: 'personal' as const,
      label: 'Personal subscription',
      overflow: true,
    },
    { scope: 'team' as const, label: 'Team subscription', overflow: false },
  ])(
    'keeps an existing $scope plan scope read-only',
    async ({ scope, label, overflow }) => {
      const user = userEvent.setup()
      const save = vi.fn()
      render(<Editor scope={scope} editing onSave={save} />)
      expect(
        screen.getByRole('textbox', { name: 'Subscription type' })
      ).toHaveValue(label)
      expect(
        screen.getByRole('textbox', { name: 'Subscription type' })
      ).toHaveAttribute('readonly')
      expect(
        screen.queryByRole('combobox', { name: 'Subscription type' })
      ).not.toBeInTheDocument()
      expect(
        screen.getByText(
          'The subscription type cannot be changed after the plan is created.'
        )
      ).toBeVisible()
      const toggle = screen.queryByRole('switch', {
        name: 'Use PAYGO after weekly allowance is exhausted',
      })
      if (overflow) expect(toggle).toBeChecked()
      else expect(toggle).not.toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: 'Save changes' }))
      await waitFor(() =>
        expect(save).toHaveBeenCalledWith({
          plan: expect.objectContaining({
            funding_scope: scope,
            allow_wallet_overflow: overflow,
          }),
        })
      )
    }
  )
})
