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
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { formatCNY } from '@/features/billing/format'
import type { UserSubscriptionRecord } from '@/features/subscriptions/types'
import { toIntlLocale } from '@/i18n/languages'
import { formatQuota } from '@/lib/format'

export function SubscriptionBalance(props: {
  subscriptions?: UserSubscriptionRecord[]
}) {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const date = (value: number) => new Date(value * 1000).toLocaleString(locale)
  const [clock, setNow] = useState(() => Date.now() / 1000)
  // A newly purchased subscription can start after the last timer tick.
  const now = Math.max(clock, Date.now() / 1000)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 30000)
    return () => clearInterval(timer)
  }, [])
  return props.subscriptions?.map(({ subscription: sub }) => {
    const money = (quota: number) =>
      sub.purchase_price_cents
        ? formatCNY((quota / 500000) * 7, locale)
        : formatQuota(quota)
    const expired = sub.end_time <= now
    const active = sub.status === 'active' && !expired && sub.start_time <= now
    const remaining = Math.max(0, sub.amount_total - sub.amount_used)
    const weeklyRemaining = sub.weekly_amount
      ? Math.max(
          0,
          (sub.weekly_limit_amount ?? sub.weekly_amount) -
            (sub.weekly_used ?? 0)
        )
      : remaining
    const available = active ? weeklyRemaining : 0
    const weeklyReset = sub.weekly_reset_at ?? 0
    const refreshBeforeExpiry =
      active &&
      sub.weekly_amount &&
      weeklyReset > 0 &&
      weeklyReset < sub.end_time
    const nextRefresh =
      refreshBeforeExpiry && weeklyReset > now ? weeklyReset : null
    let notice: string | null = null
    if (expired) notice = t('Expired')
    else if (!active) notice = t('Inactive')
    else if (!sub.weekly_amount && remaining === 0) {
      notice = t(
        'Subscription allowance used up. Contact your administrator to renew or adjust it.'
      )
    } else if (weeklyRemaining === 0 && refreshBeforeExpiry) {
      notice = t(
        'Weekly allowance used up. It becomes available at the next weekly refresh.'
      )
    } else if (weeklyRemaining === 0) {
      notice = t(
        'Weekly allowance used up. This subscription expires before another weekly refresh.'
      )
    }
    return (
      <Card key={sub.id}>
        <CardHeader>
          <CardTitle>{sub.purchase_title || t('Subscription')}</CardTitle>
        </CardHeader>
        <CardContent className='space-y-4'>
          <p className='font-medium'>
            {t('Available now: {{amount}}', { amount: money(available) })}
          </p>
          {(sub.weekly_amount
            ? [
                {
                  title: t('This week'),
                  total: sub.weekly_limit_amount ?? sub.weekly_amount,
                  used: sub.weekly_used ?? 0,
                },
              ]
            : []
          ).map((item) => (
            <div key={item.title} className='space-y-2'>
              <div className='flex flex-wrap justify-between gap-2 text-sm'>
                <span>
                  {item.title} · {t('Remaining')} {money(available)}
                </span>
                <span className='text-muted-foreground'>
                  {t('Used')} {money(item.used)} / {money(item.total)}
                </span>
              </div>
              <Progress
                aria-label={item.title}
                value={
                  item.total > 0
                    ? Math.max(0, Math.min(100, (item.used / item.total) * 100))
                    : 0
                }
              />
            </div>
          ))}
          {notice && <p className='text-muted-foreground text-sm'>{notice}</p>}
          <p className='text-muted-foreground text-xs'>
            {nextRefresh
              ? `${t('Weekly refresh')}: ${date(nextRefresh)} · `
              : ''}
            {t('Expires')}: {date(sub.end_time)}
          </p>
        </CardContent>
      </Card>
    )
  })
}
