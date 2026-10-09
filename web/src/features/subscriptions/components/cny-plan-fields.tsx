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
import { useFormContext } from 'react-hook-form'
import { useTranslation } from 'react-i18next'

import {
  SideDrawerSection,
  sideDrawerSwitchItemClassName,
} from '@/components/drawer-layout'
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'

import type { PlanFormValues } from '../lib/plan-form'

export function CNYPlanFields(props: { scopeReadOnly?: boolean }) {
  const { t } = useTranslation()
  const form = useFormContext<PlanFormValues>()
  const isTeam = form.watch('funding_scope') === 'team'
  const scopeOptions = [
    { value: 'personal', label: t('Personal subscription') },
    { value: 'team', label: t('Team subscription') },
  ]
  return (
    <SideDrawerSection>
      <p className='text-muted-foreground text-sm'>
        {t(
          'Plans last 28 days with a weekly allowance that does not roll over. Edits apply to future purchases only.'
        )}
      </p>
      <FormField
        control={form.control}
        name='funding_scope'
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t('Subscription type')}</FormLabel>
            {props.scopeReadOnly ? (
              <FormControl>
                <Input
                  readOnly
                  value={
                    isTeam ? t('Team subscription') : t('Personal subscription')
                  }
                />
              </FormControl>
            ) : (
              <Select
                items={scopeOptions}
                value={field.value}
                onValueChange={(value) => {
                  if (value !== 'personal' && value !== 'team') return
                  field.onChange(value)
                  if (value === 'team') {
                    form.setValue('allow_wallet_overflow', false, {
                      shouldDirty: true,
                    })
                  }
                }}
              >
                <FormControl>
                  <SelectTrigger className='w-full'>
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent alignItemWithTrigger={false}>
                  {scopeOptions.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {props.scopeReadOnly && (
              <FormDescription>
                {t(
                  'The subscription type cannot be changed after the plan is created.'
                )}
              </FormDescription>
            )}
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name='title'
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t('Plan Title')}</FormLabel>
            <FormControl>
              <Input {...field} maxLength={128} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name='subtitle'
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t('Plan Subtitle')}</FormLabel>
            <FormControl>
              <Input {...field} maxLength={255} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name='price_amount'
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t('Price (CNY / 28 days)')}</FormLabel>
            <FormControl>
              <Input
                {...field}
                type='number'
                min={0.01}
                max={9999}
                step={0.01}
                onChange={(e) => field.onChange(Number(e.target.value))}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name='weekly_amount'
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t('Weekly allowance (CNY)')}</FormLabel>
            <FormControl>
              <Input
                {...field}
                type='number'
                min={0.01}
                step={0.01}
                onChange={(e) => field.onChange(Number(e.target.value))}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name='sort_order'
        render={({ field }) => (
          <FormItem>
            <FormLabel>{t('Sort Order')}</FormLabel>
            <FormControl>
              <Input
                {...field}
                type='number'
                onChange={(e) => field.onChange(Number(e.target.value))}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name='enabled'
        render={({ field }) => (
          <FormItem className={sideDrawerSwitchItemClassName()}>
            <FormLabel>{t('Enabled Status')}</FormLabel>
            <FormControl>
              <Switch checked={field.value} onCheckedChange={field.onChange} />
            </FormControl>
          </FormItem>
        )}
      />
      <FormField
        control={form.control}
        name='allow_balance_pay'
        render={({ field }) => (
          <FormItem className={sideDrawerSwitchItemClassName()}>
            <FormLabel>{t('Allow Balance Payment')}</FormLabel>
            <FormControl>
              <Switch checked={field.value} onCheckedChange={field.onChange} />
            </FormControl>
          </FormItem>
        )}
      />
      {!isTeam && (
        <FormField
          control={form.control}
          name='allow_wallet_overflow'
          render={({ field }) => (
            <FormItem className={sideDrawerSwitchItemClassName()}>
              <FormLabel>
                {t('Use PAYGO after weekly allowance is exhausted')}
              </FormLabel>
              <FormControl>
                <Switch
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              </FormControl>
            </FormItem>
          )}
        />
      )}
    </SideDrawerSection>
  )
}
