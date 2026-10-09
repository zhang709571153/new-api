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

import { joinWorkspaceTeam } from './api'

const schema = z.object({
  value: z.string().trim().min(1).max(100),
})
export function TeamSetup(props: { onClose: () => void }) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { value: '' },
  })
  const mutation = useMutation({
    mutationFn: (input: z.infer<typeof schema>) =>
      joinWorkspaceTeam(input.value),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['workspace'] })
      void client.invalidateQueries({ queryKey: ['workspace-team'] })
      props.onClose()
    },
  })
  const title = t('Join team')
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !mutation.isPending) props.onClose()
      }}
      title={title}
      description={t(
        'Enter your invitation code. Your personal balance stays separate.'
      )}
    >
      <form
        className='space-y-4'
        onSubmit={form.handleSubmit((input) => mutation.mutate(input))}
      >
        <div className='space-y-2'>
          <Label htmlFor='team-setup-value'>{t('Invitation code')}</Label>
          <Input
            id='team-setup-value'
            maxLength={100}
            aria-invalid={!!form.formState.errors.value}
            {...form.register('value')}
          />
        </div>
        {form.formState.errors.value && (
          <p role='alert' className='text-destructive text-sm'>
            {t('This field is required')}
          </p>
        )}
        {mutation.isError && (
          <p role='alert' className='text-destructive text-sm'>
            {mutation.error.message}
          </p>
        )}
        <Button type='submit' disabled={mutation.isPending} className='w-full'>
          {title}
        </Button>
      </form>
    </Dialog>
  )
}
