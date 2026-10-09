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
import { toast } from 'sonner'
import { z } from 'zod'

import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { useAuthStore } from '@/stores/auth-store'

import { nicknameSchema } from '../lib/nickname'

const schema = z.object({ display_name: nicknameSchema })

export function NicknameForm(props: {
  userId: number
  nickname: string
  onSave: (nickname: string) => Promise<unknown>
  onSaved?: () => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { display_name: props.nickname },
  })
  const save = useMutation({
    mutationFn: (values: z.infer<typeof schema>) =>
      props.onSave(values.display_name),
    onSuccess: (_, values) => {
      const auth = useAuthStore.getState().auth
      if (auth.user?.id === props.userId) {
        auth.setUser({ ...auth.user, display_name: values.display_name })
      }
      void client.invalidateQueries({ queryKey: ['workspace-team'] })
      toast.success(t('Nickname updated'))
      form.reset(values)
      props.onSaved?.()
    },
  })
  return (
    <Form {...form}>
      <form
        className='space-y-4'
        onSubmit={form.handleSubmit((values) => save.mutate(values))}
      >
        <FormField
          control={form.control}
          name='display_name'
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('Nickname')}</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  autoComplete='nickname'
                  placeholder={t('Optional')}
                  disabled={save.isPending}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        {save.isError && (
          <p role='alert' className='text-destructive text-sm'>
            {save.error.message}
          </p>
        )}
        <Button
          type='submit'
          disabled={save.isPending || !form.formState.isDirty}
        >
          {t('Save nickname')}
        </Button>
      </form>
    </Form>
  )
}
