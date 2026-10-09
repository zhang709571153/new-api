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

import { Button } from '@/components/ui/button'
import { Field, FieldLabel, FieldError } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { api } from '@/lib/api'
import {
  requireServerSuccess,
  getServerErrorMessage,
} from '@/lib/server-error-message'

const schema = z.object({ name: z.string().trim().min(1).max(100) })
export function TeamNameForm(props: {
  teamId: number
  name: string
  admin?: boolean
  onSaved?: () => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { name: props.name },
  })
  const save = useMutation({
    mutationFn: async (input: z.infer<typeof schema>) =>
      requireServerSuccess(
        (
          await api.patch(
            props.admin
              ? `/api/team/workspaces/${props.teamId}`
              : '/api/workspace/team',
            { ...input, team_id: props.teamId }
          )
        ).data
      ),
    onSuccess: (_, input) => {
      for (const key of [
        'workspace-team',
        'supplier-teams',
        'admin-workspace-team',
      ]) {
        void client.invalidateQueries({ queryKey: [key] })
      }
      form.reset(input)
      props.onSaved?.()
    },
  })
  return (
    <form
      className='flex flex-wrap items-end gap-3'
      onSubmit={form.handleSubmit((input) => {
        if (!save.isPending) save.mutate(input)
      })}
    >
      <Field
        className='min-w-40 flex-1'
        data-invalid={Boolean(form.formState.errors.name)}
      >
        <FieldLabel htmlFor='team-name'>{t('Team name')}</FieldLabel>
        <Input
          id='team-name'
          disabled={save.isPending}
          {...form.register('name')}
        />
        {form.formState.errors.name && (
          <FieldError>{t('Enter a team name of 1–100 characters.')}</FieldError>
        )}
      </Field>
      <Button
        type='submit'
        disabled={save.isPending || !form.formState.isDirty}
      >
        {t('Save')}
      </Button>
      {save.error && (
        <p role='alert' className='text-destructive w-full text-sm'>
          {getServerErrorMessage(save.error)}
        </p>
      )}
    </form>
  )
}
