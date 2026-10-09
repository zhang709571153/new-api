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
import { describe, expect, it } from 'vitest'

import {
  formValuesToPlanPayload,
  planToFormValues,
  PLAN_FORM_DEFAULTS,
} from '../lib/plan-form'
import type { SubscriptionPlan } from '../types'

describe('CNY catalog contract', () => {
  it('saves weekly CNY as quota and fixes the period at four weeks without group discounts', () => {
    const value = formValuesToPlanPayload({
      ...PLAN_FORM_DEFAULTS,
      title: 'Ultra',
      price_amount: 1799,
      weekly_amount: 580,
      upgrade_group: 'discount',
      quota_reset_period: 'daily',
    })
    expect(value.plan.currency).toBe('CNY')
    expect(value.plan.funding_scope).toBe('personal')
    expect(value.plan.weekly_amount).toBe(Math.floor((580 / 7) * 500000))
    expect(value.plan.total_amount).toBe(Number(value.plan.weekly_amount) * 4)
    expect(value.plan.duration_unit).toBe('day')
    expect(value.plan.duration_value).toBe(28)
    expect(value.plan.quota_reset_period).toBe('never')
    expect(value.plan.upgrade_group).toBe('')
  })
  it('round-trips an existing plan without reinterpreting CNY as USD', () => {
    const payload = formValuesToPlanPayload({
      ...PLAN_FORM_DEFAULTS,
      title: 'Lite',
      price_amount: 99,
      weekly_amount: 27,
    })
    const values = planToFormValues({
      ...payload.plan,
      id: 1,
    } as SubscriptionPlan)
    expect(values.price_amount).toBe(99)
    expect(values.weekly_amount).toBe(27)
    expect(formValuesToPlanPayload(values).plan).toEqual(payload.plan)
  })
  it('never enables PAYGO for team plans, including stale form state', () => {
    const payload = formValuesToPlanPayload({
      ...PLAN_FORM_DEFAULTS,
      funding_scope: 'team',
      title: 'Team',
      price_amount: 445,
      weekly_amount: 350,
      allow_wallet_overflow: true,
    })
    expect(payload.plan).toMatchObject({
      currency: 'CNY',
      funding_scope: 'team',
      allow_wallet_overflow: false,
      duration_unit: 'day',
      duration_value: 28,
      weekly_amount: 25000000,
      total_amount: 100000000,
    })
    const values = planToFormValues({
      ...payload.plan,
      id: 9,
    } as SubscriptionPlan)
    expect(values.funding_scope).toBe('team')
    expect(values.allow_wallet_overflow).toBe(false)
    expect(values.weekly_amount).toBe(350)
    expect(formValuesToPlanPayload(values).plan).toEqual(payload.plan)
  })
  it.each([false, true])(
    'preserves the personal PAYGO setting %s',
    (overflow) => {
      const payload = formValuesToPlanPayload({
        ...PLAN_FORM_DEFAULTS,
        title: 'Personal',
        weekly_amount: 50,
        price_amount: 100,
        allow_wallet_overflow: overflow,
      })
      expect(payload.plan.funding_scope).toBe('personal')
      expect(payload.plan.allow_wallet_overflow).toBe(overflow)
    }
  )
  it.each(['personal', 'team'] as const)(
    'locks an existing %s plan against stale scope changes',
    (scope) => {
      const payload = formValuesToPlanPayload(
        {
          ...PLAN_FORM_DEFAULTS,
          funding_scope: scope === 'team' ? 'personal' : 'team',
          title: 'Edited',
          weekly_amount: 100,
          price_amount: 200,
          allow_wallet_overflow: true,
        },
        { currency: 'CNY', funding_scope: scope }
      )
      expect(payload.plan.funding_scope).toBe(scope)
      expect(payload.plan.allow_wallet_overflow).toBe(scope === 'personal')
    }
  )
  it('keeps legacy plans personal when scope was not stored', () => {
    const payload = formValuesToPlanPayload(
      {
        ...PLAN_FORM_DEFAULTS,
        title: 'Legacy CNY',
        funding_scope: 'team',
        weekly_amount: 50,
        price_amount: 100,
      },
      { currency: 'CNY' }
    )
    expect(payload.plan.funding_scope).toBe('personal')
    const values = planToFormValues({
      ...payload.plan,
      funding_scope: undefined,
      id: 1,
    } as SubscriptionPlan)
    expect(values.funding_scope).toBe('personal')
  })
  it('normalizes legacy USD to personal and preserves USD quota conversion', () => {
    const payload = formValuesToPlanPayload({
      ...PLAN_FORM_DEFAULTS,
      currency: 'USD',
      funding_scope: 'team',
      title: 'Legacy USD',
      total_amount: 80,
    })
    expect(payload.plan.funding_scope).toBe('personal')
    const values = planToFormValues({
      ...payload.plan,
      funding_scope: 'team',
      id: 2,
    } as SubscriptionPlan)
    expect(values.currency).toBe('USD')
    expect(values.funding_scope).toBe('personal')
    expect(values.total_amount).toBe(80)
  })
  it('normalizes old team overflow state before rendering the editor', () => {
    const values = planToFormValues({
      ...formValuesToPlanPayload({
        ...PLAN_FORM_DEFAULTS,
        title: 'Team',
        funding_scope: 'team',
        price_amount: 100,
        weekly_amount: 50,
      }).plan,
      id: 1,
      allow_wallet_overflow: true,
    } as SubscriptionPlan)
    expect(values.funding_scope).toBe('team')
    expect(values.allow_wallet_overflow).toBe(false)
  })
})
