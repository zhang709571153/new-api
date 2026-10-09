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
import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import { Field, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import type { UserSubscription } from '@/features/subscriptions/types'
import { getCurrencyLabel } from '@/lib/currency'
import { parseQuotaFromDollars, quotaUnitsToDollars } from '@/lib/format'
import { getServerErrorMessage } from '@/lib/server-error-message'
import { useSystemConfigStore } from '@/stores/system-config-store'

export function WeeklyUsageForm(props: {
  subscription: UserSubscription
  pending: boolean
  error: Error | null
  onSubmit: (weeklyUsedUSD: number) => void
}) {
  const { t } = useTranslation()
  const quotaPerUnit = useSystemConfigStore(
    (state) => state.config.currency.quotaPerUnit
  )
  const maximum = quotaUnitsToDollars(1000000 * quotaPerUnit)
  const form = useForm<{ amount: number }>({
    resolver: zodResolver(z.object({ amount: z.number().min(0).max(maximum) })),
    defaultValues: {
      amount: quotaUnitsToDollars(props.subscription.weekly_used ?? 0),
    },
  })
  return (
    <form
      className='space-y-3 border-t pt-4'
      onSubmit={form.handleSubmit(({ amount }) =>
        props.onSubmit(parseQuotaFromDollars(amount) / quotaPerUnit)
      )}
    >
      <Field data-invalid={!!form.formState.errors.amount}>
        <FieldLabel htmlFor='team-weekly-used'>
          {t('Team usage this week')} ({getCurrencyLabel()})
        </FieldLabel>
        <Input
          id='team-weekly-used'
          type='number'
          min={0}
          max={maximum}
          step='0.01'
          disabled={props.pending}
          aria-invalid={!!form.formState.errors.amount}
          {...form.register('amount', { valueAsNumber: true })}
        />
        {form.formState.errors.amount && (
          <FieldError>
            {t('Enter a non-negative amount within the supported limit.')}
          </FieldError>
        )}
      </Field>
      <p className='text-muted-foreground text-xs'>
        {t(
          'This changes only the team usage counter for the current week. Member weekly usage and all cumulative usage stay unchanged. Ongoing requests must settle first.'
        )}
      </p>
      {props.error && (
        <p role='alert' className='text-destructive text-sm'>
          {getServerErrorMessage(props.error)}
        </p>
      )}
      <Button type='submit' variant='outline' disabled={props.pending}>
        {t('Save weekly usage')}
      </Button>
    </form>
  )
}
