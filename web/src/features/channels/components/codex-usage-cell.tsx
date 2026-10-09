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
import { RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { StatusBadge } from '@/components/status-badge'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { toIntlLocale } from '@/i18n/languages'
import { formatNumber } from '@/lib/format'
import { useAuthStore } from '@/stores/auth-store'

import { getCodexUsage } from '../api'
import {
  codexUsagePercent,
  codexUsageWindows,
  codexUsageWindowTitle,
} from '../lib/codex-usage'
import type { Channel } from '../types'
import { CodexLoginDialog } from './dialogs/codex-login-dialog'
import { CodexUsageDialog } from './dialogs/codex-usage-dialog'

const REFRESH_INTERVAL_MS = 5 * 60 * 1000
const MANUAL_REFRESH_COOLDOWN_MS = 30 * 1000

export function CodexUsageCell(props: {
  channel: Channel
  sensitiveVisible: boolean
}) {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const userId = useAuthStore((state) => state.auth.user?.id)
  const sessionId = useAuthStore((state) => state.auth.session?.sid)
  const canUpdateLogin = useAuthStore((state) => state.auth.user?.role === 100)
  const [loginOpen, setLoginOpen] = useState(false)
  const [open, setOpen] = useState(false)
  const [now, setNow] = useState(Date.now)
  const multiKey = props.channel.channel_info.is_multi_key
  const automatic =
    props.channel.status === 1 && !multiKey && props.sensitiveVisible
  const query = useQuery({
    queryKey: ['codex-channel-usage', userId, props.channel.id],
    queryFn: () => getCodexUsage(props.channel.id),
    enabled: automatic,
    staleTime: 2 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
    refetchInterval: automatic ? REFRESH_INTERVAL_MS : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
    retryOnMount: false,
    refetchOnReconnect: false,
    meta: { errorToast: false },
  })
  useEffect(() => {
    const timer = window.setInterval(
      () => setNow(Date.now()),
      MANUAL_REFRESH_COOLDOWN_MS
    )
    return () => window.clearInterval(timer)
  }, [])
  const lastAttempt = Math.max(query.dataUpdatedAt, query.errorUpdatedAt)
  const cooldown =
    lastAttempt > 0 && now - lastAttempt < MANUAL_REFRESH_COOLDOWN_MS
  const refresh = async () => {
    if (
      multiKey ||
      query.isFetching ||
      Date.now() - lastAttempt < MANUAL_REFRESH_COOLDOWN_MS
    ) {
      return
    }
    await query.refetch()
    setNow(Date.now())
  }
  const account =
    !multiKey && !query.isError && query.data?.success
      ? query.data.data
      : undefined
  const email = typeof account?.email === 'string' ? account.email : ''
  const plan = typeof account?.plan_type === 'string' ? account.plan_type : ''
  const windows = codexUsageWindows(query.data?.data)
  const expiredWindow = windows.some(
    ({ window }) =>
      typeof window.reset_at === 'number' &&
      window.reset_at > 0 &&
      window.reset_at * 1000 <= now
  )
  const stale =
    query.dataUpdatedAt > 0 &&
    (now - query.dataUpdatedAt >= REFRESH_INTERVAL_MS || expiredWindow)
  const rateLimit = query.data?.data?.rate_limit as
    | { allowed?: boolean; limit_reached?: boolean }
    | undefined
  const limited =
    rateLimit?.allowed === false || rateLimit?.limit_reached === true
  const failed = query.isError || query.data?.success === false
  const hasData =
    !multiKey &&
    !failed &&
    !stale &&
    query.data?.success === true &&
    windows.length > 0
  let status = t('Not queried')
  if (multiKey) {
    status = t('Separate accounts are required for usage monitoring')
  } else if (query.isFetching) status = t('Updating...')
  else if (
    query.data?.upstream_status === 401 ||
    query.data?.upstream_status === 403
  ) {
    status = t('Credentials need updating')
  } else if (failed) status = t('Failed to fetch usage')
  else if (stale) status = t('Usage data is stale')
  else if (query.data && !hasData) status = t('Usage unavailable')
  else if (hasData && limited) status = t('Limited')
  else if (hasData) status = t('Updated')

  if (!props.sensitiveVisible) {
    return <span className='text-muted-foreground'>••••</span>
  }

  return (
    <div className='w-64 max-w-full min-w-0 space-y-2 py-1 text-xs'>
      <div className='flex flex-wrap items-center justify-between gap-1'>
        <span className='font-medium'>{t('Subscription usage')}</span>
        <StatusBadge
          label={status}
          variant={failed || stale ? 'warning' : 'neutral'}
          size='sm'
          copyable={false}
        />
      </div>
      {(email || plan) && (
        <div className='text-muted-foreground space-y-0.5 break-all'>
          {email && <p>{email}</p>}
          {plan && <p className='capitalize'>{plan}</p>}
        </div>
      )}
      {hasData &&
        windows.map(({ key, window }) => {
          const percent = codexUsagePercent(window.used_percent)
          const title = codexUsageWindowTitle(window, t, locale)
          let resetAt = window.reset_at
          if (
            (!resetAt || !Number.isFinite(resetAt)) &&
            typeof window.reset_after_seconds === 'number' &&
            window.reset_after_seconds > 0
          ) {
            resetAt = query.dataUpdatedAt / 1000 + window.reset_after_seconds
          }
          return (
            <div key={key} className='space-y-1'>
              <div className='font-medium'>{title}</div>
              {percent === null ? (
                <span className='text-muted-foreground'>
                  {t('Usage unavailable')}
                </span>
              ) : (
                <>
                  <div className='flex flex-wrap justify-between gap-x-3 tabular-nums'>
                    <span>
                      {t('Used:')} {formatNumber(percent, locale)}%
                    </span>
                    <span>
                      {t('Remaining:')} {formatNumber(100 - percent, locale)}%
                    </span>
                  </div>
                  <Progress
                    value={percent}
                    aria-label={title}
                    className={
                      percent >= 90
                        ? '[&_[data-slot=progress-indicator]]:bg-amber-500'
                        : ''
                    }
                  />
                </>
              )}
              <div className='text-muted-foreground break-words'>
                {t('Reset at:')}{' '}
                {typeof resetAt === 'number' &&
                Number.isFinite(resetAt) &&
                resetAt > 0
                  ? new Date(resetAt * 1000).toLocaleString(locale)
                  : t('Unknown')}
              </div>
            </div>
          )
        })}
      {lastAttempt > 0 && (
        <p className='text-muted-foreground'>
          {t('Last checked:')} {new Date(lastAttempt).toLocaleString(locale)}
        </p>
      )}
      {!multiKey && (
        <div className='flex flex-wrap items-center gap-1'>
          <Button
            variant='ghost'
            size='sm'
            className='h-7 px-2 text-xs'
            onClick={() => void refresh()}
            disabled={query.isFetching || cooldown}
            aria-label={t('Refresh subscription usage')}
          >
            <RefreshCw className='size-3' />
            {t('Refresh')}
          </Button>
          <Button
            variant='ghost'
            size='sm'
            className='h-7 px-2 text-xs'
            aria-haspopup='dialog'
            onClick={() => {
              setOpen(true)
              if (!query.data) void refresh()
            }}
          >
            {t('Details')}
          </Button>
          {canUpdateLogin && (
            <Button
              variant='outline'
              size='sm'
              className='h-7 px-2 text-xs'
              aria-haspopup='dialog'
              onClick={() => setLoginOpen(true)}
            >
              {t('Update login credentials')}
            </Button>
          )}
        </div>
      )}
      {loginOpen && canUpdateLogin && (
        <CodexLoginDialog
          key={`${userId}-${sessionId}`}
          channel={props.channel}
          onClose={() => setLoginOpen(false)}
        />
      )}
      {open && (
        <CodexUsageDialog
          open
          onOpenChange={setOpen}
          channelId={props.channel.id}
          channelName={props.channel.name}
          response={
            failed || stale
              ? { success: false, message: status }
              : (query.data ?? null)
          }
          onRefresh={refresh}
          isRefreshing={query.isFetching}
          refreshDisabled={cooldown}
        />
      )}
    </div>
  )
}
