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
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Dialog } from '@/components/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { adjustUserQuota } from '@/features/users/api'
import { toIntlLocale } from '@/i18n/languages'
import {
  getCurrencyDisplay,
  getCurrencyLabel,
  formatLocalCurrencyAmount,
} from '@/lib/currency'

import { formatPoints, type TeamMember } from './api'
import { MAX_TEAM_QUOTA, pointsToQuota } from './quota'

export function AllowanceDialog(props: {
  member: TeamMember
  quotaPerUnit: number
  onClose: () => void
}) {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const queryClient = useQueryClient()
  const { meta } = getCurrencyDisplay()
  const unitsPerAmount =
    meta.kind === 'tokens' ? 1 : props.quotaPerUnit / meta.exchangeRate
  const [amount, setAmount] = useState(
    String((10 * props.quotaPerUnit) / unitsPerAmount)
  )
  const maxPoints = Math.max(
    0,
    (MAX_TEAM_QUOTA - props.member.quota) / unitsPerAmount
  )
  const quotaToAdd = pointsToQuota(
    Number(amount),
    unitsPerAmount,
    props.member.quota
  )
  const mutation = useMutation({
    mutationFn: async () => {
      if (quotaToAdd === null) {
        throw new Error(
          t('Enter an amount between {{min}} and {{max}}.', {
            min: formatLocalCurrencyAmount(0),
            max: formatLocalCurrencyAmount(maxPoints),
          })
        )
      }
      const response = await adjustUserQuota({
        id: props.member.id,
        action: 'add_quota',
        mode: 'add',
        value: quotaToAdd,
      })
      if (!response.success) {
        throw new Error(response.message || t('Failed to adjust quota'))
      }
    },
    onSuccess: () => {
      toast.success(t('Quota adjusted successfully'))
      void queryClient.invalidateQueries({ queryKey: ['team-overview'] })
      props.onClose()
    },
  })
  return (
    <Dialog
      title={`${t('Add PAYGO balance')} · ${props.member.display_name || props.member.username}`}
      description={`${t('PAYGO balance')}: ${formatPoints(props.member.quota, props.quotaPerUnit, locale)}`}
      open
      onOpenChange={(open) => {
        if (!open && !mutation.isPending) props.onClose()
      }}
    >
      <form
        className='space-y-4'
        onSubmit={(event) => {
          event.preventDefault()
          if (quotaToAdd !== null) mutation.mutate()
        }}
      >
        <div className='space-y-2'>
          <Label htmlFor='allowance-points'>
            {t('Amount')} ({getCurrencyLabel()})
          </Label>
          <Input
            id='allowance-points'
            aria-invalid={quotaToAdd === null}
            type='number'
            min='0.0001'
            max={maxPoints}
            step='0.0001'
            required
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </div>
        {quotaToAdd === null && (
          <p role='alert' className='text-destructive text-sm'>
            {t('Enter an amount between {{min}} and {{max}}.', {
              min: formatLocalCurrencyAmount(0),
              max: formatLocalCurrencyAmount(maxPoints),
            })}
          </p>
        )}
        <p className='text-muted-foreground text-sm'>
          {t(
            'Adds personal PAYGO balance only. Team funds and subscription limits are unchanged.'
          )}
        </p>
        {mutation.isError && (
          <p role='alert' className='text-destructive text-sm'>
            {mutation.error.message}
          </p>
        )}
        <Button
          type='submit'
          disabled={mutation.isPending || quotaToAdd === null}
          className='w-full'
        >
          {t('Add PAYGO balance')}
        </Button>
      </form>
    </Dialog>
  )
}
