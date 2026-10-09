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
import { ExternalLink, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  StaticDataTable,
  type StaticDataTableColumn,
} from '@/components/data-table'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { toIntlLocale } from '@/i18n/languages'
import { formatNumber, formatPercent } from '@/lib/format'

import type { Sub2APIStatus, Sub2APIOverview, Sub2APIAccount } from '../api'

export function Sub2APIManagement(props: {
  status: Sub2APIStatus
  overview?: Sub2APIOverview
  overviewError?: boolean
  refreshing?: boolean
  onRefresh?: () => void
}) {
  const { t } = useTranslation()
  let adminURL: URL | undefined
  try {
    const parsed = new URL(props.status.admin_url)
    const isLocal = ['localhost', '127.0.0.1', '[::1]'].includes(
      parsed.hostname
    )
    if (
      !parsed.username &&
      !parsed.password &&
      !parsed.search &&
      !parsed.hash &&
      (parsed.protocol === 'https:' || (parsed.protocol === 'http:' && isLocal))
    ) {
      adminURL = parsed
    }
  } catch {
    // An incomplete server configuration must never become an unsafe link.
  }

  return (
    <div className='space-y-6'>
      <div className='flex flex-wrap items-start justify-between gap-4'>
        <div className='space-y-2'>
          <h2 className='text-lg font-semibold'>
            {t('Sub2API channel management')}
          </h2>
          <p className='text-muted-foreground max-w-2xl text-sm'>
            {t(
              'Manage upstream accounts, pools and routing in Sub2API. Customer access, plans and billing remain in RealYu.'
            )}
          </p>
          <Badge variant={props.status.configured ? 'secondary' : 'outline'}>
            {props.status.configured
              ? t('Service connection configured')
              : t('Service connection needs configuration')}
          </Badge>
        </div>
        <div className='flex flex-wrap items-center gap-2'>
          {props.onRefresh && (
            <Button
              variant='outline'
              disabled={props.refreshing || !props.status.configured}
              onClick={props.onRefresh}
            >
              <RefreshCw aria-hidden='true' />
              {t('Refresh')}
            </Button>
          )}
          {adminURL ? (
            <Button
              role='link'
              render={
                <a
                  href={adminURL.href}
                  target='_blank'
                  rel='noopener noreferrer'
                />
              }
            >
              {t('Open Sub2API management')}
              <ExternalLink aria-hidden='true' data-icon='inline-end' />
            </Button>
          ) : (
            <p className='text-muted-foreground text-sm'>
              {t(
                'The administrator needs to configure the Sub2API management URL.'
              )}
            </p>
          )}
        </div>
      </div>
      {props.overviewError && (
        <ErrorState
          description={t('Upstream statistics are unavailable.')}
          onRetry={props.onRefresh}
        />
      )}
      {!props.overview && !props.overviewError && props.status.configured && (
        <LoadingState />
      )}
      {props.overview && (
        <>
          <p className='text-muted-foreground text-sm'>
            {t(
              'Today follows the Sub2API server timezone and includes all groups using each account. Request share is calculated within the displayed pool. Missing data is shown as a dash.'
            )}
          </p>
          {props.overview.pools.map((pool) => (
            <Sub2APIAccountPool key={pool.channel_id} pool={pool} />
          ))}
        </>
      )}
    </div>
  )
}

function Sub2APIAccountPool(props: { pool: Sub2APIOverview['pools'][number] }) {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const pool = props.pool
  const total = pool.accounts.reduce(
    (sum, account) => sum + (account.today?.requests ?? 0),
    0
  )
  const complete =
    pool.available &&
    !pool.truncated &&
    pool.accounts.every((account) => account.today !== null)
  const columns: StaticDataTableColumn<Sub2APIAccount>[] = [
    {
      id: 'account',
      header: t('Upstream account'),
      cell: (account) => (
        <div className='max-w-64'>
          <p className='truncate font-medium' title={account.name}>
            {account.name || `#${account.id}`}
          </p>
          <p className='text-muted-foreground text-xs'>
            #{account.id} · {account.platform} · {account.type}
          </p>
        </div>
      ),
    },
    {
      id: 'status',
      header: t('Status'),
      cell: (account) => {
        let label = t('Inactive')
        if (account.status === 'active' && account.schedulable) {
          label = t('Active')
        }
        if (account.status === 'error') {
          label = t('Error')
        }
        return <Badge variant='outline'>{label}</Badge>
      },
    },
    {
      id: 'concurrency',
      header: t('Concurrency'),
      cell: (account) =>
        `${formatNumber(account.current_concurrency, locale)} / ${formatNumber(account.concurrency, locale)}`,
    },
    {
      id: 'five-hour',
      header: t('5-hour usage'),
      cell: (account) =>
        account.five_hour_percent === null
          ? '—'
          : formatPercent(account.five_hour_percent),
    },
    {
      id: 'weekly',
      header: t('7-day usage'),
      cell: (account) =>
        account.weekly_percent === null
          ? '—'
          : formatPercent(account.weekly_percent),
    },
    {
      id: 'requests',
      header: t('Today requests'),
      cell: (account) =>
        account.today ? formatNumber(account.today.requests, locale) : '—',
    },
    {
      id: 'tokens',
      header: t('Today tokens'),
      cell: (account) =>
        account.today ? formatNumber(account.today.tokens, locale) : '—',
    },
    {
      id: 'share',
      header: t('Request share'),
      cell: (account) =>
        complete && total > 0 && account.today
          ? formatPercent((account.today.requests / total) * 100)
          : '—',
    },
  ]
  return (
    <section className='space-y-3'>
      <div className='flex flex-wrap items-center gap-2'>
        <h3 className='font-medium'>
          {pool.group_name || `${t('Group')} #${pool.group_id}`}
        </h3>
        <Badge variant='secondary'>
          {t('Channel')} #{pool.channel_id} · {t('Group')} #{pool.group_id}
        </Badge>
        <span className='text-muted-foreground text-sm'>
          {t('Accounts')}: {formatNumber(pool.accounts.length, locale)}
        </span>
      </div>
      {(!pool.available || pool.truncated) && (
        <p className='text-destructive text-sm'>
          {t(
            'This pool snapshot is incomplete. Open Sub2API management for the full account list.'
          )}
        </p>
      )}
      <StaticDataTable
        columns={columns}
        data={pool.accounts}
        getRowKey={(account) => account.id}
        emptyContent={
          pool.available
            ? t('No accounts')
            : t('Upstream statistics are unavailable.')
        }
        tableProps={{
          'aria-label': pool.group_name || `${t('Group')} #${pool.group_id}`,
        }}
      />
    </section>
  )
}
