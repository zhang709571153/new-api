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
import { HeartPulse } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Dialog } from '@/components/dialog'
import { ErrorState } from '@/components/error-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { AvailabilityHistory } from '@/features/performance-metrics/availability-history'
import {
  formatLatency,
  formatUptimePct,
} from '@/features/performance-metrics/lib/format'
import { toIntlLocale } from '@/i18n/languages'
import { api } from '@/lib/api'
import { formatNumber } from '@/lib/format'
import { requireServerSuccess } from '@/lib/server-error-message'

type ChannelHealth = {
  id: number
  name: string
  routing_state: string
  sample_state: string
  request_count: number
  success_count: number
  success_rate: number | null
  avg_latency_ms: number | null
  series: { ts: number; success_rate: number | null; request_count: number }[]
}
type HealthResponse = {
  success: boolean
  message?: string
  data: {
    hours: number
    observed_at: number
    collection_enabled: boolean
    dropped_samples?: number
    channels: ChannelHealth[]
  }
}

export function ChannelHealthDialog() {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const [open, setOpen] = useState(false)
  const [hours, setHours] = useState(24)
  const query = useQuery({
    queryKey: ['channel-health', hours],
    enabled: open,
    refetchInterval: 30_000,
    retry: false,
    queryFn: async () =>
      requireServerSuccess(
        (
          await api.get<HealthResponse>('/api/channel/health', {
            params: { hours },
          })
        ).data
      ),
  })
  const states: Record<string, string> = {
    enabled: t('Enabled'),
    manually_disabled: t('Manually disabled'),
    automatically_disabled: t('Automatically disabled'),
    quota_exhausted: t('Quota exhausted'),
    unavailable: t('Unavailable'),
  }
  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={t('Channel health')}
      description={t(
        'Observed relay attempts. Retries count per channel; business rejections and client cancellations are excluded.'
      )}
      trigger={
        <Button variant='outline'>
          <HeartPulse />
          {t('Channel health')}
        </Button>
      }
      contentClassName='sm:max-w-4xl'
    >
      <Tabs value={String(hours)} onValueChange={(v) => setHours(Number(v))}>
        <TabsList aria-label={t('Time range')}>
          <TabsTrigger value='24'>{t('24 hours')}</TabsTrigger>
          <TabsTrigger value='168'>{t('7 days')}</TabsTrigger>
        </TabsList>
      </Tabs>
      {query.isPending && <Skeleton className='mt-4 h-40' />}
      {query.isError && <ErrorState onRetry={() => void query.refetch()} />}
      {query.isSuccess && (
        <div className='mt-4 space-y-4'>
          {!query.data.data.collection_enabled && (
            <p role='status'>
              {t(
                'Health collection is disabled. Historical data may be outdated.'
              )}
            </p>
          )}
          {(query.data.data.dropped_samples ?? 0) > 0 && (
            <p role='status'>
              {t(
                'Some health samples could not be recorded since startup. Rates may be incomplete.'
              )}
            </p>
          )}
          <p className='text-muted-foreground text-xs'>
            {t('Last updated')}:{' '}
            {new Date(query.data.data.observed_at * 1000).toLocaleString(
              locale
            )}
          </p>
          {query.data.data.channels.length === 0 && <p>{t('No data')}</p>}
          {query.data.data.channels.map((row) => (
            <article key={row.id} className='space-y-3 rounded-lg border p-4'>
              <div className='flex flex-wrap items-center gap-2'>
                <h3 className='min-w-0 font-medium break-all'>
                  #{row.id} {row.name}
                </h3>
                <Badge
                  variant={
                    row.routing_state === 'enabled' ? 'outline' : 'secondary'
                  }
                >
                  {states[row.routing_state] ?? t('Unavailable')}
                </Badge>
              </div>
              <dl className='grid grid-cols-2 gap-3 text-sm sm:grid-cols-3'>
                <div>
                  <dt className='text-muted-foreground'>{t('Success rate')}</dt>
                  <dd>
                    {row.success_rate == null
                      ? t('No data')
                      : formatUptimePct(row.success_rate)}
                  </dd>
                </div>
                <div>
                  <dt className='text-muted-foreground'>{t('Requests')}</dt>
                  <dd>{formatNumber(row.request_count, locale)}</dd>
                </div>
                <div>
                  <dt className='text-muted-foreground'>
                    {t('Average latency')}
                  </dt>
                  <dd>{formatLatency(row.avg_latency_ms ?? 0)}</dd>
                </div>
              </dl>
              <AvailabilityHistory
                points={row.series}
                hours={hours}
                start={
                  Math.floor(query.data.data.observed_at / 3600) * 3600 -
                  (hours - 1) * 3600
                }
              />
            </article>
          ))}
          <p className='text-muted-foreground text-xs'>
            {t(
              'Gray bars mean no observations, not an outage. Routing status is shown separately from historical success rate.'
            )}
          </p>
        </div>
      )}
    </Dialog>
  )
}
