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
import { useQuery } from '@tanstack/react-query'
import { isAxiosError, isCancel } from 'axios'
import { useTranslation } from 'react-i18next'

import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { getUserSubscriptions } from '@/features/subscriptions/api'
import type { UserSubscriptionRecord } from '@/features/subscriptions/types'
import { toIntlLocale } from '@/i18n/languages'
import { formatQuota } from '@/lib/format'
import { requireServerSuccess } from '@/lib/server-error-message'
import { useAuthStore } from '@/stores/auth-store'
import { useSystemConfigStore } from '@/stores/system-config-store'

export function SubscriptionSummary(props: {
  subscriptions: UserSubscriptionRecord[]
  showScope?: boolean
}) {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  useSystemConfigStore((state) => state.config.currency)
  const now = Date.now() / 1000
  const active = props.subscriptions.filter(
    ({ subscription }) =>
      subscription.status === 'active' &&
      subscription.start_time <= now &&
      subscription.end_time > now
  )
  if (!active.length) {
    return (
      <span className='text-muted-foreground text-sm'>
        {t('No subscription')}
      </span>
    )
  }
  return (
    <div className='min-w-0 space-y-2 text-xs'>
      {active.map(({ subscription }) => {
        const weekly =
          subscription.weekly_limit_amount ?? subscription.weekly_amount ?? 0
        return (
          <div key={subscription.id} className='space-y-1'>
            <div className='flex flex-wrap items-center gap-x-2 gap-y-1'>
              <span className='max-w-64 font-medium break-words'>
                {subscription.purchase_title || t('Monthly subscription')}
              </span>
              <StatusBadge
                label={t('Active')}
                variant='success'
                copyable={false}
              />
              {props.showScope && (
                <span className='text-muted-foreground'>
                  {(subscription.workspace_team_id ?? 0) > 0
                    ? t('Team')
                    : t('Personal')}
                </span>
              )}
            </div>
            {weekly > 0 && (
              <div className='text-muted-foreground'>
                {t('Weekly remaining')}:{' '}
                {formatQuota(
                  Math.max(0, weekly - (subscription.weekly_used ?? 0))
                )}
              </div>
            )}
            <div className='text-muted-foreground'>
              {t('Expires at')}:{' '}
              <time
                dateTime={new Date(subscription.end_time * 1000).toISOString()}
              >
                {new Date(subscription.end_time * 1000).toLocaleDateString(
                  locale
                )}
              </time>
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function UserSubscriptionSummary(props: { userId: number }) {
  const { t } = useTranslation()
  const viewerId = useAuthStore((state) => state.auth.user?.id)
  const query = useQuery({
    queryKey: ['user-allowance', props.userId, viewerId],
    enabled: Boolean(viewerId),
    queryFn: async ({ signal }) =>
      requireServerSuccess(await getUserSubscriptions(props.userId, signal)),
    staleTime: 30000,
    refetchInterval: 60000,
    retry: (failures, error) => {
      if (failures >= 2 || isCancel(error) || !isAxiosError(error)) return false
      const status = error.response?.status
      return !status || status === 408 || status === 429 || status >= 500
    },
    retryDelay: (attempt, error) => {
      const retryAfter = isAxiosError(error)
        ? error.response?.headers?.['retry-after']
        : undefined
      if (retryAfter !== undefined) {
        const seconds = Number(retryAfter)
        const delay = Number.isFinite(seconds)
          ? seconds * 1000
          : Date.parse(String(retryAfter)) - Date.now()
        if (Number.isFinite(delay) && delay > 0) return delay
      }
      return 1000 * 2 ** attempt
    },
    meta: { errorToast: false },
  })
  if (!viewerId || query.isPending) {
    return (
      <span className='text-muted-foreground text-xs'>{t('Loading...')}</span>
    )
  }
  if (query.isError) {
    return (
      <div className='text-destructive text-xs'>
        <span>{t('Unable to load subscription')}</span>
        <Button
          variant='link'
          size='sm'
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
        >
          {t('Retry')}
        </Button>
      </div>
    )
  }
  return <SubscriptionSummary subscriptions={query.data.data ?? []} showScope />
}
