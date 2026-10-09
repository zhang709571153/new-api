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
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'

export function PaygoForm(props: {
  disabled: boolean
  onSubmit: (amount: number) => void
}) {
  const { t } = useTranslation()
  const form = useForm({
    resolver: zodResolver(
      z.object({
        amount: z
          .string()
          .refine(
            (value) =>
              z
                .number()
                .min(0.01)
                .max(10000)
                .multipleOf(0.01)
                .safeParse(Number(value)).success
          ),
      })
    ),
    defaultValues: { amount: '50' },
  })
  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit((values) =>
          props.onSubmit(Number(values.amount))
        )}
        className='space-y-4'
      >
        <div className='flex flex-wrap gap-2'>
          {[50, 200, 300, 500, 1000].map((amount) => (
            <Button
              key={amount}
              type='button'
              variant={
                Number(form.watch('amount')) === amount ? 'default' : 'outline'
              }
              aria-pressed={Number(form.watch('amount')) === amount}
              disabled={props.disabled}
              onClick={() =>
                form.setValue('amount', String(amount), {
                  shouldValidate: true,
                })
              }
            >
              ¥{amount}
            </Button>
          ))}
        </div>
        <FormField
          control={form.control}
          name='amount'
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('Top-up amount (CNY)')}</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  type='number'
                  min={0.01}
                  max={10000}
                  step={0.01}
                  disabled={props.disabled}
                  onChange={(e) => field.onChange(e.target.value)}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type='submit' disabled={props.disabled}>
          {t('Top up')}
        </Button>
      </form>
    </Form>
  )
}
