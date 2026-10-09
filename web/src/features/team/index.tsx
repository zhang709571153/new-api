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
import { Link } from '@tanstack/react-router'
import {
  type ColumnDef,
  type SortingState,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { RefreshCw, Users } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { DataTableColumnHeader, StaticDataTable } from '@/components/data-table'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { SectionPageLayout } from '@/components/layout'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { UserSubscriptionSummary } from '@/features/workspace/subscription-summary'
import { SupplierTeams } from '@/features/workspace/supplier-teams'
import { toIntlLocale } from '@/i18n/languages'
import { hasPermission } from '@/lib/admin-permissions'
import { formatNumber } from '@/lib/format'
import { useAuthStore } from '@/stores/auth-store'

import { formatPoints, getTeamOverview, type TeamMember } from './api'

export function SupplierOverview() {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const user = useAuthStore((state) => state.auth.user)
  const [days, setDays] = useState(1)
  const [search, setSearch] = useState('')
  const [view, setView] = useState('accounts')
  const overview = useQuery({
    queryKey: ['team-overview', days, user?.id],
    enabled: Boolean(user?.id) && hasPermission(user, 'team', 'read'),
    queryFn: () => getTeamOverview(days),
    refetchInterval: 30000,
  })
  const quotaPerUnit = overview.data?.quota_per_unit || 500000
  const totalQuota = overview.data?.totals.quota ?? 0
  const members = useMemo(
    () =>
      (overview.data?.members ?? []).filter((member) =>
        `${member.id} ${member.username} ${member.display_name}`
          .toLowerCase()
          .includes(search.toLowerCase())
      ),
    [overview.data?.members, search]
  )
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'spending', desc: true },
  ])
  const columns = useMemo<ColumnDef<TeamMember>[]>(
    () => [
      { id: 'id', header: t('ID'), accessorFn: (row) => row.id },
      {
        id: 'member',
        header: t('Member'),
        accessorFn: (row) => row.display_name || row.username,
        sortingFn: 'text',
      },
      {
        id: 'spending',
        header: t('Period spending'),
        accessorFn: (row) => row.period_quota,
      },
      {
        id: 'requests',
        header: t('Requests'),
        accessorFn: (row) => row.period_requests,
      },
      {
        id: 'tokens',
        header: t('Tokens'),
        accessorFn: (row) => row.prompt_tokens + row.completion_tokens,
      },
      {
        id: 'balance',
        header: t('PAYGO balance'),
        accessorFn: (row) => row.quota,
      },
      {
        id: 'subscription',
        header: t('Monthly subscription'),
        enableSorting: false,
      },
      {
        id: 'last_request',
        header: t('Last request'),
        accessorFn: (row) => row.last_request_at,
      },
    ],
    [t]
  )
  const table = useReactTable({
    data: members,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    defaultColumn: { sortingFn: 'basic' },
    enableHiding: false,
    getRowId: (row) => String(row.id),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  return (
    <SectionPageLayout>
      <SectionPageLayout.Title>{t('Customer usage')}</SectionPageLayout.Title>
      <SectionPageLayout.Content>
        <div className='max-w-full min-w-0 space-y-5'>
          <Tabs value={view} onValueChange={setView}>
            <TabsList aria-label={t('Usage view')}>
              <TabsTrigger value='accounts'>{t('Account usage')}</TabsTrigger>
              <TabsTrigger value='teams'>{t('Team breakdown')}</TabsTrigger>
            </TabsList>
            <TabsContent value='teams' className='mt-4'>
              <SupplierTeams />
            </TabsContent>
            <TabsContent value='accounts' className='mt-4 space-y-5'>
              <div className='flex flex-wrap items-center justify-between gap-3'>
                <Tabs
                  className='max-w-full min-w-0'
                  value={String(days)}
                  onValueChange={(value) => setDays(Number(value))}
                >
                  <TabsList className='h-auto max-w-full flex-wrap justify-start'>
                    <TabsTrigger value='1'>{t('Last 24 hours')}</TabsTrigger>
                    <TabsTrigger value='7'>{t('Last 7 days')}</TabsTrigger>
                    <TabsTrigger value='30'>{t('Last 30 days')}</TabsTrigger>
                  </TabsList>
                </Tabs>
                <div className='flex max-w-full min-w-0 flex-wrap gap-2'>
                  <Button
                    variant='outline'
                    onClick={() => void overview.refetch()}
                    disabled={overview.isFetching}
                    aria-label={t('Refresh')}
                  >
                    <RefreshCw className='size-4' />
                  </Button>
                  <Button
                    variant='outline'
                    role='link'
                    render={<Link to='/users' />}
                  >
                    {t('Manage users')}
                  </Button>
                </div>
              </div>
              {overview.isError ? (
                <ErrorState
                  title={t(
                    'Unable to load usage. Please refresh and try again.'
                  )}
                  description={overview.error.message}
                  onRetry={() => void overview.refetch()}
                />
              ) : (
                <>
                  <div className='grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-3'>
                    {[
                      {
                        label: t('Users'),
                        value: String(overview.data?.members.length ?? 0),
                      },
                      {
                        label: t('Period spending'),
                        value: formatPoints(totalQuota, quotaPerUnit, locale),
                      },
                      {
                        label: t('Requests in this period'),
                        value: formatNumber(
                          overview.data?.totals.requests ?? 0,
                          locale
                        ),
                      },
                    ].map((item) => (
                      <div
                        key={item.label}
                        className='bg-card min-w-0 rounded-xl border p-5'
                      >
                        <p className='text-muted-foreground text-sm'>
                          {item.label}
                        </p>
                        {overview.isPending ? (
                          <Skeleton className='mt-3 h-8 w-24' />
                        ) : (
                          <p className='mt-3 text-2xl font-semibold tabular-nums'>
                            {item.value}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                  <section className='bg-card max-w-full min-w-0 overflow-hidden rounded-xl border'>
                    <div className='flex flex-wrap items-center justify-between gap-3 border-b p-4'>
                      <div className='min-w-0'>
                        <h2 className='font-semibold'>
                          {t('Billing accounts')}
                        </h2>
                      </div>
                      <div className='w-full min-w-0 sm:w-auto'>
                        <Label htmlFor='team-search' className='sr-only'>
                          {t('Search members')}
                        </Label>
                        <Input
                          id='team-search'
                          className='w-full sm:w-64'
                          placeholder={t('Search members')}
                          value={search}
                          onChange={(event) => setSearch(event.target.value)}
                        />
                      </div>
                    </div>
                    <StaticDataTable>
                      <TableHeader>
                        <TableRow>
                          {table.getFlatHeaders().map((header) => (
                            <TableHead
                              key={header.id}
                              aria-sort={
                                {
                                  asc: 'ascending',
                                  desc: 'descending',
                                  false: 'none',
                                }[String(header.column.getIsSorted())] as
                                  | 'ascending'
                                  | 'descending'
                                  | 'none'
                              }
                            >
                              <DataTableColumnHeader
                                column={header.column}
                                title={header.column.columnDef.header as string}
                              />
                            </TableHead>
                          ))}
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {table
                          .getRowModel()
                          .rows.map(({ original: member }) => {
                            const share =
                              totalQuota > 0
                                ? (member.period_quota / totalQuota) * 100
                                : 0
                            return (
                              <TableRow key={member.id}>
                                <TableCell className='text-muted-foreground tabular-nums'>
                                  {member.id}
                                </TableCell>
                                <TableCell>
                                  <div className='font-medium'>
                                    <Link
                                      to='/usage-logs/$section'
                                      params={{ section: 'common' }}
                                      search={{
                                        scope: 'all',
                                        username: member.username,
                                        type: ['2'],
                                        startTime:
                                          (overview.data?.start_timestamp ??
                                            0) * 1000,
                                        endTime:
                                          (overview.data?.end_timestamp ?? 0) *
                                            1000 -
                                          1,
                                      }}
                                      className='text-primary hover:underline'
                                    >
                                      {member.display_name || member.username}
                                    </Link>
                                  </div>
                                  <div className='text-muted-foreground mt-1 text-xs'>
                                    {member.username} ·{' '}
                                    {member.status === 1
                                      ? t('Active')
                                      : t('Paused')}
                                  </div>
                                </TableCell>
                                <TableCell className='tabular-nums'>
                                  <span className='font-semibold'>
                                    {formatPoints(
                                      member.period_quota,
                                      quotaPerUnit,
                                      locale
                                    )}
                                  </span>
                                  <p className='text-muted-foreground mt-1 text-xs'>
                                    {t('{{count}} requests', {
                                      count: member.period_requests,
                                    })}{' '}
                                    · {share.toFixed(1)}%
                                  </p>
                                </TableCell>
                                <TableCell className='tabular-nums'>
                                  {formatNumber(member.period_requests, locale)}
                                </TableCell>
                                <TableCell className='tabular-nums'>
                                  {formatNumber(
                                    member.prompt_tokens +
                                      member.completion_tokens,
                                    locale
                                  )}
                                </TableCell>
                                <TableCell className='tabular-nums'>
                                  {formatPoints(
                                    member.quota,
                                    quotaPerUnit,
                                    locale
                                  )}
                                </TableCell>
                                <TableCell>
                                  <UserSubscriptionSummary userId={member.id} />
                                </TableCell>
                                <TableCell className='text-muted-foreground text-xs whitespace-nowrap'>
                                  {member.last_request_at > 0
                                    ? new Date(
                                        member.last_request_at * 1000
                                      ).toLocaleString(locale)
                                    : '—'}
                                </TableCell>
                              </TableRow>
                            )
                          })}
                        {!overview.isPending && members.length === 0 && (
                          <TableRow>
                            <TableCell
                              colSpan={8}
                              className='py-12 text-center'
                            >
                              <EmptyState
                                icon={Users}
                                title={t('No members to display')}
                                description={t(
                                  'Members and their recorded usage will appear here.'
                                )}
                                className='min-h-32'
                              />
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </StaticDataTable>
                  </section>
                </>
              )}
            </TabsContent>
          </Tabs>
          <div className='text-muted-foreground flex flex-wrap items-center justify-between gap-3 text-sm'>
            <div className='flex max-w-full min-w-0 flex-wrap gap-3'>
              <Button
                variant='link'
                className='px-0'
                role='link'
                render={<Link to='/users' />}
              >
                {t('Advanced user management')}
              </Button>
              <Button
                variant='link'
                className='px-0'
                role='link'
                render={
                  <Link
                    to='/usage-logs/$section'
                    params={{ section: 'common' }}
                  />
                }
              >
                {t('Usage Logs')}
              </Button>
            </div>
          </div>
        </div>
      </SectionPageLayout.Content>
    </SectionPageLayout>
  )
}
