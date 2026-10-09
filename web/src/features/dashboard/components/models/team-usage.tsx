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
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ErrorState } from '@/components/error-state'
import { Card, CardContent } from '@/components/ui/card'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import type { WorkspaceTeam } from '@/features/workspace/api'
import { api } from '@/lib/api'
import { formatNumber, formatQuota } from '@/lib/format'
import { requireServerSuccess } from '@/lib/server-error-message'
import { computeTimeRange } from '@/lib/time'
import { useAuthStore } from '@/stores/auth-store'

import { getDefaultDays, calculateDashboardStats } from '../../lib'
import type {
  DashboardFilters,
  QuotaDataItem,
  UsageMetric,
  ModelAnalyticsChartTab,
} from '../../types'
import { ModelCharts } from './model-charts'

export function TeamUsage({
  filters,
  team,
  member: selectedMember,
  onMemberChange,
  adminTeamId,
  defaultMetric,
  defaultChartTab,
}: {
  filters: DashboardFilters
  team: WorkspaceTeam
  member?: string
  onMemberChange?: (member: string) => void
  adminTeamId?: number
  defaultMetric?: UsageMetric
  defaultChartTab?: ModelAnalyticsChartTab
}) {
  const { t } = useTranslation()
  const userId = useAuthStore((state) => state.auth.user?.id)
  const [localMember, setLocalMember] = useState('all')
  const member = selectedMember ?? localMember
  const setMember = onMemberChange ?? setLocalMember
  const [dimension, setDimension] = useState('members')
  const range = computeTimeRange(
    getDefaultDays(filters.time_granularity),
    filters.start_timestamp,
    filters.end_timestamp
  )
  const query = useQuery({
    queryKey: [
      'workspace-team-trends',
      userId,
      team.team?.id,
      adminTeamId,
      range,
    ],
    queryFn: async () =>
      requireServerSuccess(
        (
          await api.get<{ success: boolean; data: QuotaDataItem[] }>(
            adminTeamId
              ? `/api/team/workspaces/${adminTeamId}/usage`
              : '/api/workspace/team/usage',
            { params: range }
          )
        ).data
      ).data,
    refetchInterval: 30000,
  })
  const names = new Map(
    team.members.map((row) => [row.user_id, row.display_name || row.username])
  )
  for (const row of query.data ?? []) {
    if (row.user_id && !names.has(row.user_id)) {
      names.set(row.user_id, `${row.username} (${t('Former member')})`)
    }
  }
  const selected = (query.data ?? []).filter(
    (row) => member === 'all' || String(row.user_id) === member
  )
  const charts =
    dimension === 'members'
      ? selected.map((row) => ({
          ...row,
          model_name: `${names.get(row.user_id ?? 0) ?? row.username} (#${row.user_id})`,
        }))
      : selected
  const stats = calculateDashboardStats(selected)
  return (
    <div className='space-y-4'>
      <div className='flex flex-wrap items-center gap-3'>
        <NativeSelect
          aria-label={t('Member')}
          value={member}
          onChange={(event) => setMember(event.target.value)}
        >
          <NativeSelectOption value='all'>
            {t('All members')}
          </NativeSelectOption>
          {[...names].map(([id, name]) => (
            <NativeSelectOption key={id} value={String(id)}>
              {name}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <NativeSelect
          aria-label={t('Compare by')}
          value={dimension}
          onChange={(event) => setDimension(event.target.value)}
        >
          <NativeSelectOption value='members'>
            {t('By member')}
          </NativeSelectOption>
          <NativeSelectOption value='models'>
            {t('By model')}
          </NativeSelectOption>
        </NativeSelect>
      </div>
      {query.isError ? (
        <ErrorState onRetry={() => void query.refetch()} />
      ) : (
        <>
          <div className='grid gap-3 sm:grid-cols-3'>
            {[
              [t('Total spent'), formatQuota(stats.totalQuota)],
              [t('Requests'), formatNumber(stats.totalCount)],
              [t('Tokens used'), formatNumber(stats.totalTokens)],
            ].map(([label, value]) => (
              <Card key={label}>
                <CardContent className='p-4'>
                  <p className='text-muted-foreground text-sm'>{label}</p>
                  {query.isPending ? (
                    <Skeleton className='mt-2 h-6 w-20' />
                  ) : (
                    <p className='mt-2 text-xl font-semibold'>{value}</p>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
          <ModelCharts
            defaultMetric={defaultMetric}
            defaultChartTab={defaultChartTab}
            data={charts}
            loading={query.isPending}
            timeGranularity={filters.time_granularity}
            title={t(
              dimension === 'members' ? 'Member usage trends' : 'Usage trends'
            )}
          />
        </>
      )}
    </div>
  )
}
