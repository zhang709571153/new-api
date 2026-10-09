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
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { z } from 'zod'

import { Dialog } from '@/components/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  formatCurrencyFromUSD,
  getCurrencyLabel,
  getCurrencyDisplay,
} from '@/lib/currency'
import { parseQuotaFromDollars, quotaUnitsToDollars } from '@/lib/format'

import { updateWorkspaceMember, type WorkspaceMember } from './api'

const schema = (maximum: number) =>
  z.object({ amount: z.number().min(0).max(maximum) })
type MemberInput = z.infer<ReturnType<typeof schema>>
export function MemberSettings(props: {
  member: WorkspaceMember
  weeklyAllowance?: boolean
  adminTeamId?: number
  onClose: () => void
  finalFocus?: React.ComponentProps<typeof Dialog>['finalFocus']
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const { config } = getCurrencyDisplay()
  const maximum = quotaUnitsToDollars(4000 * config.quotaPerUnit)
  const form = useForm<MemberInput>({
    resolver: zodResolver(schema(maximum)),
    defaultValues: {
      amount: quotaUnitsToDollars(
        props.member.allowance_usd * config.quotaPerUnit
      ),
    },
  })
  const mutation = useMutation({
    mutationFn: (input: MemberInput) =>
      updateWorkspaceMember(
        props.member.user_id,
        {
          allowance_usd:
            parseQuotaFromDollars(input.amount) / config.quotaPerUnit,
        },
        props.adminTeamId
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['workspace-team'] })
      void client.invalidateQueries({ queryKey: ['supplier-teams'] })
      void client.invalidateQueries({ queryKey: ['admin-workspace-team'] })
      props.onClose()
    },
  })
  return (
    <Dialog
      open
      finalFocus={props.finalFocus}
      onOpenChange={(open) => {
        if (!open && !mutation.isPending) props.onClose()
      }}
      title={
        props.weeklyAllowance
          ? t('Member weekly allowance')
          : t('Set allowance')
      }
      description={props.member.display_name || props.member.username}
    >
      <form
        className='space-y-4'
        onSubmit={form.handleSubmit((input) => mutation.mutate(input))}
      >
        <div className='space-y-2'>
          <Label htmlFor='member-amount'>
            {props.weeklyAllowance
              ? t('Member weekly allowance')
              : t('Remaining allowance')}{' '}
            ({getCurrencyLabel()})
          </Label>
          <Input
            id='member-amount'
            type='number'
            min='0'
            max={maximum}
            step='0.01'
            aria-invalid={!!form.formState.errors.amount}
            {...form.register('amount', { valueAsNumber: true })}
          />
        </div>
        {form.formState.errors.amount && (
          <p role='alert' className='text-destructive text-sm'>
            {t('Enter an amount between {{min}} and {{max}}.', {
              min: formatCurrencyFromUSD(0),
              max: formatCurrencyFromUSD(4000),
            })}
          </p>
        )}
        {mutation.isError && (
          <p role='alert' className='text-destructive text-sm'>
            {mutation.error.message}
          </p>
        )}
        <Button type='submit' disabled={mutation.isPending} className='w-full'>
          {t('Save')}
        </Button>
      </form>
    </Dialog>
  )
}
