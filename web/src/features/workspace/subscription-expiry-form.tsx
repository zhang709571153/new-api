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
import { useMutation } from '@tanstack/react-query'
import { useRef } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import { Field, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import type { UserSubscription } from '@/features/subscriptions/types'
import { api } from '@/lib/api'
import dayjs from '@/lib/dayjs'
import {
  getServerErrorMessage,
  requireServerSuccess,
} from '@/lib/server-error-message'

const expirySchema = z.object({
  expiry: z.string().refine((value) => {
    const timestamp = new Date(value).getTime()
    return Number.isFinite(timestamp) && timestamp > Date.now()
  }),
})

export function SubscriptionExpiryForm(props: {
  subscription: UserSubscription
  pending: boolean
  fundingContext?: { team_id: number; funding_version: number }
  onSaved: () => void
  onSubmitStart: () => boolean
  onSubmitEnd: () => void
}) {
  const { t } = useTranslation()
  const submitting = useRef(false)
  const form = useForm<z.infer<typeof expirySchema>>({
    resolver: zodResolver(expirySchema),
    defaultValues: {
      expiry: dayjs
        .unix(props.subscription.end_time)
        .format('YYYY-MM-DDTHH:mm:ss'),
    },
  })
  const save = useMutation({
    mutationFn: async (expiry: string) =>
      requireServerSuccess(
        (
          await api.put(
            `/api/subscription/admin/users/${props.subscription.user_id}/allowance`,
            {
              team_id: props.subscription.workspace_team_id ?? 0,
              subscription_id: props.subscription.id,
              end_time: Math.floor(new Date(expiry).getTime() / 1000),
              expected_end_time: props.subscription.end_time,
              ...(props.fundingContext
                ? { funding_context: props.fundingContext }
                : {}),
            }
          )
        ).data
      ),
    meta: { errorToast: false },
    onSuccess: props.onSaved,
    onSettled: () => {
      submitting.current = false
      props.onSubmitEnd()
    },
  })
  const pending = props.pending || save.isPending
  return (
    <form
      className='space-y-3 border-t pt-4'
      onSubmit={form.handleSubmit(({ expiry }) => {
        if (pending || submitting.current || !props.onSubmitStart()) return
        submitting.current = true
        save.mutate(expiry)
      })}
    >
      <Field data-invalid={Boolean(form.formState.errors.expiry)}>
        <FieldLabel htmlFor='subscription-expiry'>{t('Expires at')}</FieldLabel>
        <Input
          id='subscription-expiry'
          type='datetime-local'
          step='1'
          max='9999-12-31T23:59:59'
          disabled={pending}
          aria-invalid={Boolean(form.formState.errors.expiry)}
          {...form.register('expiry')}
        />
        {form.formState.errors.expiry && (
          <FieldError>{t('Choose a future expiry time.')}</FieldError>
        )}
      </Field>
      {save.error && (
        <p role='alert' className='text-destructive text-sm'>
          {getServerErrorMessage(save.error)}
        </p>
      )}
      <Button variant='outline' type='submit' disabled={pending}>
        {t('Save expiry')}
      </Button>
    </form>
  )
}
