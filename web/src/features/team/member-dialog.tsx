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
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { z } from 'zod'

import { CopyButton } from '@/components/copy-button'
import { Dialog } from '@/components/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  getCurrencyDisplay,
  getCurrencyLabel,
  formatLocalCurrencyAmount,
} from '@/lib/currency'

import { createTeamMember, type NewTeamMember } from './api'
import { MAX_TEAM_QUOTA, pointsToQuota } from './quota'

const memberSchema = (quotaPerUnit: number) =>
  z.object({
    username: z
      .string()
      .trim()
      .min(2)
      .max(20)
      .regex(/^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,19}$/),
    display_name: z.string().trim().min(1).max(20),
    points: z
      .number()
      .positive()
      .max(MAX_TEAM_QUOTA / quotaPerUnit)
      .refine((points) => pointsToQuota(points, quotaPerUnit) !== null),
  })
type MemberForm = z.infer<ReturnType<typeof memberSchema>>

export function MemberDialog(props: {
  open: boolean
  onOpenChange: (value: boolean) => void
  quotaPerUnit: number
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { meta } = getCurrencyDisplay()
  const unitsPerAmount =
    meta.kind === 'tokens' ? 1 : props.quotaPerUnit / meta.exchangeRate
  const [created, setCreated] = useState<NewTeamMember | null>(null)
  const form = useForm<MemberForm>({
    resolver: zodResolver(memberSchema(unitsPerAmount)),
    defaultValues: {
      username: '',
      display_name: '',
      points: (10 * props.quotaPerUnit) / unitsPerAmount,
    },
  })
  const mutation = useMutation({
    gcTime: 0,
    mutationFn: (values: MemberForm) => {
      const quota = pointsToQuota(values.points, unitsPerAmount)
      if (quota === null) {
        throw new Error(
          t('Enter an amount between {{min}} and {{max}}.', {
            min: formatLocalCurrencyAmount(0),
            max: formatLocalCurrencyAmount(MAX_TEAM_QUOTA / unitsPerAmount),
          })
        )
      }
      return createTeamMember({
        username: values.username,
        display_name: values.display_name,
        quota,
        models: [
          'gpt-6-luna',
          'gpt-6-sol',
          'gpt-6-astra',
          'gpt-image-2',
          'gpt-5.6-luna',
        ],
      })
    },
    onSuccess: (data) => {
      setCreated(data)
      void queryClient.invalidateQueries({ queryKey: ['team-overview'] })
    },
  })
  const close = () => {
    props.onOpenChange(false)
    setCreated(null)
    form.reset()
    mutation.reset()
  }
  const credentials = created
    ? `${t('Username')}: ${created.user.username}\nAPI Key: ${created.api_key}\nAPI URL: https://api.realyu.fun/v1`
    : ''

  return (
    <Dialog
      title={created ? t('Your member is ready') : t('Add team member')}
      description={
        created
          ? t(
              'Save this personal API key now. It is shown only once. Share it privately with this member.'
            )
          : t(
              'Create an API-only member with a personal key and a shared allowance.'
            )
      }
      open={props.open}
      onOpenChange={(open) => {
        if (!open && !mutation.isPending) close()
      }}
    >
      {created ? (
        <div className='space-y-4'>
          <pre className='bg-muted rounded-lg p-4 text-xs leading-relaxed break-all whitespace-pre-wrap'>
            {credentials}
          </pre>
          <CopyButton
            className='w-full'
            size='default'
            variant='default'
            value={credentials}
            aria-label={t('Copy credentials')}
          >
            {t('Copy credentials')}
          </CopyButton>
          <Button variant='outline' className='w-full' onClick={close}>
            {t('I have saved the credentials')}
          </Button>
        </div>
      ) : (
        <form
          className='space-y-4'
          onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
        >
          <div className='space-y-2'>
            <Label htmlFor='team-username'>{t('Username')}</Label>
            <Input
              id='team-username'
              aria-invalid={Boolean(form.formState.errors.username)}
              autoComplete='off'
              {...form.register('username')}
            />
            <p className='text-muted-foreground text-xs'>
              {t(
                '2–20 letters, numbers, dots, underscores, or hyphens. Start with a letter or number.'
              )}
            </p>
            {form.formState.errors.username && (
              <p role='alert' className='text-destructive text-xs'>
                {t('Please enter a valid username.')}
              </p>
            )}
          </div>
          <div className='space-y-2'>
            <Label htmlFor='team-display-name'>{t('Display Name')}</Label>
            <Input
              id='team-display-name'
              aria-invalid={Boolean(form.formState.errors.display_name)}
              {...form.register('display_name')}
            />
            {form.formState.errors.display_name && (
              <p role='alert' className='text-destructive text-xs'>
                {t('Enter a name of up to 20 characters.')}
              </p>
            )}
          </div>
          <div className='space-y-2'>
            <Label htmlFor='team-points'>
              {t('Initial allowance')} ({getCurrencyLabel()})
            </Label>
            <Input
              id='team-points'
              aria-invalid={Boolean(form.formState.errors.points)}
              type='number'
              min='0.0001'
              max={MAX_TEAM_QUOTA / unitsPerAmount}
              step='0.0001'
              {...form.register('points', { valueAsNumber: true })}
            />
            {form.formState.errors.points && (
              <p role='alert' className='text-destructive text-xs'>
                {t('Enter an amount between {{min}} and {{max}}.', {
                  min: formatLocalCurrencyAmount(0),
                  max: formatLocalCurrencyAmount(
                    MAX_TEAM_QUOTA / unitsPerAmount
                  ),
                })}
              </p>
            )}
          </div>
          <p className='text-muted-foreground text-xs'>
            {t(
              'Includes Luna, Sol, Astra, and image generation. Allowance is managed by the member account and shared across its keys.'
            )}
          </p>
          {mutation.isError && (
            <p role='alert' className='text-destructive text-sm'>
              {mutation.error.message}
            </p>
          )}
          <Button
            type='submit'
            className='w-full'
            disabled={mutation.isPending}
          >
            {mutation.isPending ? t('Creating...') : t('Create member and key')}
          </Button>
        </form>
      )}
    </Dialog>
  )
}
