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
import { getRouteApi, Link, useNavigate } from '@tanstack/react-router'
import { Eye, EyeOff } from 'lucide-react'
import { useState, useCallback, useMemo, lazy, Suspense } from 'react'
import { useTranslation } from 'react-i18next'

import { ErrorState } from '@/components/error-state'
import { SectionPageLayout } from '@/components/layout'
import { FadeIn } from '@/components/page-transition'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { getWorkspaceTeam, type WorkspaceTeam } from '@/features/workspace/api'
import { api } from '@/lib/api'
import { ROLE } from '@/lib/roles'
import { requireServerSuccess } from '@/lib/server-error-message'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'

import { ModelsChartPreferences } from './components/models/models-chart-preferences'
import { ModelsFilter } from './components/models/models-filter-dialog'
import { TeamUsage } from './components/models/team-usage'
import { OverviewDashboard } from './components/overview/overview-dashboard'
import { WorkspaceOverview } from './components/overview/workspace-overview'
import { DEFAULT_TIME_GRANULARITY } from './constants'
import {
  buildDefaultDashboardFilters,
  getDefaultDays,
  getSavedChartPreferences,
  getSavedGranularity,
  saveChartPreferences,
} from './lib'
import {
  type DashboardSectionId,
  DASHBOARD_DEFAULT_SECTION,
  DASHBOARD_SECTION_IDS,
} from './section-registry'
import type {
  DashboardChartPreferences,
  DashboardFilters,
  QuotaDataItem,
  UserChartsFilters,
} from './types'

const route = getRouteApi('/_authenticated/dashboard/$section')

const LOG_STAT_CARD_FALLBACK_KEYS = [
  'count',
  'quota',
  'tokens',
  'average-rpm',
  'average-tpm',
] as const
const PERFORMANCE_METRIC_FALLBACK_KEYS = [
  'success-rate',
  'average-latency',
  'throughput',
] as const
const PERFORMANCE_MODEL_FALLBACK_KEYS = [
  'primary-model',
  'secondary-model',
] as const

const LazyLogStatCards = lazy(() =>
  import('./components/models/log-stat-cards').then((m) => ({
    default: m.LogStatCards,
  }))
)

const LazyModelCharts = lazy(() =>
  import('./components/models/model-charts').then((m) => ({
    default: m.ModelCharts,
  }))
)

const LazyPerformanceOverview = lazy(() =>
  import('./components/models/performance-overview').then((m) => ({
    default: m.PerformanceOverview,
  }))
)

const LazyUserCharts = lazy(() =>
  import('./components/users/user-charts').then((m) => ({
    default: m.UserCharts,
  }))
)

const LazyFlowCharts = lazy(() =>
  import('./components/flow/flow-charts').then((m) => ({
    default: m.FlowCharts,
  }))
)

function LogStatCardsFallback() {
  return (
    <div className='overflow-hidden rounded-lg border'>
      <div className='divide-border/60 grid grid-cols-2 divide-x sm:grid-cols-3 lg:grid-cols-5'>
        {LOG_STAT_CARD_FALLBACK_KEYS.map((key, index) => (
          <div
            key={key}
            className={cn(
              'px-2.5 py-1.5 sm:px-5 sm:py-4',
              index === LOG_STAT_CARD_FALLBACK_KEYS.length - 1 &&
                'col-span-2 sm:col-span-1'
            )}
          >
            <div className='flex items-center gap-1.5 sm:gap-2'>
              <Skeleton className='size-4 rounded-sm sm:size-7 sm:rounded-md' />
              <Skeleton className='h-4 w-16' />
            </div>
            <Skeleton className='mt-1 h-5 w-16 sm:mt-2 sm:h-7 sm:w-20' />
            <Skeleton className='mt-1 hidden h-3.5 w-28 md:block' />
          </div>
        ))}
      </div>
    </div>
  )
}

function ModelChartsFallback() {
  return (
    <div className='overflow-hidden rounded-lg border'>
      <div className='flex items-center justify-between border-b px-4 py-3 sm:px-5'>
        <Skeleton className='h-5 w-32' />
        <Skeleton className='h-8 w-72' />
      </div>
      <div className='h-96 p-2'>
        <Skeleton className='h-full w-full' />
      </div>
    </div>
  )
}

function PerformanceOverviewFallback() {
  return (
    <div className='overflow-hidden rounded-lg border'>
      <div className='flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-5'>
        <div className='flex items-center gap-2'>
          <Skeleton className='h-4 w-24' />
        </div>
        {PERFORMANCE_METRIC_FALLBACK_KEYS.map((key) => (
          <div key={key} className='flex items-center gap-1.5'>
            <Skeleton className='h-3 w-14' />
            <Skeleton className='h-4 w-16' />
          </div>
        ))}
        <div className='ml-auto flex items-center gap-2'>
          {PERFORMANCE_MODEL_FALLBACK_KEYS.map((key) => (
            <Skeleton key={key} className='h-5 w-28 rounded-full' />
          ))}
        </div>
      </div>
    </div>
  )
}

const SECTION_META: Record<DashboardSectionId, { titleKey: string }> = {
  overview: { titleKey: 'My workspace' },
  service: { titleKey: 'Service information and announcements' },
  models: {
    titleKey: 'Usage trends',
  },
  flow: {
    titleKey: 'Flow',
  },
  users: {
    titleKey: 'User Analytics',
  },
}

export function Dashboard() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const params = route.useParams()
  const search = route.useSearch()
  const userRole = useAuthStore((state) => state.auth.user?.role)
  const isAdmin = Boolean(userRole && userRole >= ROLE.ADMIN)
  const userId = useAuthStore((state) => state.auth.user?.id)
  const team = useQuery({
    queryKey: ['workspace-team', userId],
    queryFn: getWorkspaceTeam,
    enabled: Boolean(userId),
  })
  const isOwner = team.data?.team?.owner_user_id === userId && Boolean(userId)
  const adminTeamId =
    isAdmin && search.scope === 'all' ? search.team : undefined
  const adminTeam = useQuery({
    queryKey: ['admin-workspace-team', userId, adminTeamId],
    enabled: Boolean(adminTeamId),
    queryFn: async () =>
      requireServerSuccess(
        (
          await api.get<{ success: boolean; data: WorkspaceTeam }>(
            `/api/team/workspaces/${adminTeamId}`
          )
        ).data
      ).data,
  })
  const selectedTeam = adminTeamId ? adminTeam.data : team.data
  const isTeamView = search.scope === 'team' || Boolean(adminTeamId)
  const viewScope = isAdmin && search.scope !== 'self' ? 'all' : 'self'
  const activeSection = (params.section ??
    DASHBOARD_DEFAULT_SECTION) as DashboardSectionId

  const [modelData, setModelData] = useState<QuotaDataItem[]>([])
  const [dataLoading, setDataLoading] = useState(false)
  const [dataError, setDataError] = useState(false)
  const [dataRetry, setDataRetry] = useState(0)
  const [chartPreferences, setChartPreferences] =
    useState<DashboardChartPreferences>(() => getSavedChartPreferences())
  const [defaultFilters] = useState(() =>
    buildDefaultDashboardFilters(getSavedChartPreferences())
  )
  const modelFilters = useMemo<DashboardFilters>(
    () => ({
      ...defaultFilters,
      username:
        isAdmin && !isTeamView && viewScope === 'all'
          ? search.username
          : undefined,
      start_timestamp:
        search.startTime != null
          ? new Date(search.startTime)
          : defaultFilters.start_timestamp,
      end_timestamp:
        search.endTime != null
          ? new Date(search.endTime)
          : defaultFilters.end_timestamp,
      time_granularity: search.granularity ?? defaultFilters.time_granularity,
    }),
    [
      defaultFilters,
      isAdmin,
      isTeamView,
      viewScope,
      search.username,
      search.startTime,
      search.endTime,
      search.granularity,
    ]
  )
  const setModelFilters = useCallback(
    (filters: DashboardFilters) => {
      void navigate({
        to: '/dashboard/$section',
        params: { section: activeSection },
        search: {
          ...search,
          username: filters.username,
          startTime: filters.start_timestamp?.getTime(),
          endTime: filters.end_timestamp?.getTime(),
          granularity: filters.time_granularity,
        },
      })
    },
    [navigate, activeSection, search]
  )
  const [userChartsFilters, setUserChartsFilters] = useState<UserChartsFilters>(
    () => {
      const granularity = getSavedGranularity()
      return {
        timeGranularity: granularity,
        selectedRange: getDefaultDays(granularity),
        topUserLimit: 10,
      }
    }
  )
  const [flowSensitiveVisible, setFlowSensitiveVisible] = useState(true)

  const handleFilterChange = useCallback(
    (filters: DashboardFilters) => {
      setModelFilters(filters)
    },
    [setModelFilters]
  )

  const handleResetFilters = useCallback(() => {
    setModelFilters(buildDefaultDashboardFilters(chartPreferences))
  }, [chartPreferences, setModelFilters])

  const handleDataUpdate = useCallback(
    (data: QuotaDataItem[], loading: boolean, failed = false) => {
      setModelData(data)
      setDataLoading(loading)
      setDataError(failed)
    },
    []
  )

  const handleChartPreferencesChange = useCallback(
    (preferences: DashboardChartPreferences) => {
      setChartPreferences(preferences)
      setModelFilters(buildDefaultDashboardFilters(preferences))
      saveChartPreferences(preferences)
    },
    [setModelFilters]
  )

  const meta = SECTION_META[activeSection] ?? SECTION_META.overview
  const visibleSections = useMemo(
    () =>
      DASHBOARD_SECTION_IDS.filter(
        (section) => section === 'models' || (section === 'users' && isAdmin)
      ),
    [isAdmin]
  )
  const handleSectionChange = useCallback(
    (section: string) => {
      void navigate({
        to: '/dashboard/$section',
        params: { section: section as DashboardSectionId },
        search: { scope: viewScope },
      })
    },
    [navigate, viewScope]
  )
  const showSectionTabs =
    activeSection !== 'overview' && visibleSections.length > 1
  const modelActions =
    activeSection === 'models' ? (
      <>
        <ModelsChartPreferences
          preferences={chartPreferences}
          onPreferencesChange={handleChartPreferencesChange}
        />
        <ModelsFilter
          viewScope={isTeamView ? 'self' : viewScope}
          preferences={chartPreferences}
          currentFilters={modelFilters}
          onFilterChange={handleFilterChange}
          onReset={handleResetFilters}
        />
      </>
    ) : null
  const flowActions =
    activeSection === 'flow' ? (
      <>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant='ghost'
                size='icon'
                onClick={() => setFlowSensitiveVisible((prev) => !prev)}
                aria-label={
                  flowSensitiveVisible
                    ? t('Hide sensitive data')
                    : t('Show sensitive data')
                }
                className='text-muted-foreground hover:text-foreground size-8'
              />
            }
          >
            {flowSensitiveVisible ? <Eye /> : <EyeOff />}
          </TooltipTrigger>
          <TooltipContent>
            {flowSensitiveVisible
              ? t('Hide sensitive data')
              : t('Show sensitive data')}
          </TooltipContent>
        </Tooltip>
        <ModelsFilter
          preferences={chartPreferences}
          currentFilters={modelFilters}
          onFilterChange={handleFilterChange}
          onReset={handleResetFilters}
          titleKey='Flow Filters'
          descriptionKey='Filter the traffic flow view by time range and user.'
        />
      </>
    ) : null
  const sectionActions = modelActions ?? flowActions

  const teamQuery = adminTeamId ? adminTeam : team
  let teamContent = (
    <p className='text-muted-foreground'>
      {t('Team trends are available to the team owner.')}
    </p>
  )
  if (teamQuery.isPending) teamContent = <Skeleton className='h-48' />
  else if (teamQuery.isError) {
    teamContent = <ErrorState onRetry={() => void teamQuery.refetch()} />
  } else if ((isOwner || adminTeamId) && selectedTeam) {
    teamContent = (
      <TeamUsage
        key={selectedTeam.team?.id}
        team={selectedTeam}
        adminTeamId={adminTeamId}
        filters={modelFilters}
        member={search.member ? String(search.member) : 'all'}
        onMemberChange={(member) =>
          void navigate({
            to: '/dashboard/$section',
            params: { section: 'models' },
            search: {
              ...search,
              member: member === 'all' ? undefined : Number(member),
            },
          })
        }
        defaultMetric={chartPreferences.usageMetric}
        defaultChartTab={chartPreferences.modelAnalyticsChart}
      />
    )
  }

  if (activeSection === 'service') return <OverviewDashboard />
  if (activeSection === 'overview') {
    return (
      <SectionPageLayout>
        <SectionPageLayout.Title>{t('My workspace')}</SectionPageLayout.Title>
        <SectionPageLayout.Content>
          <WorkspaceOverview />
        </SectionPageLayout.Content>
      </SectionPageLayout>
    )
  }

  return (
    <SectionPageLayout>
      <SectionPageLayout.Title>{t(meta.titleKey)}</SectionPageLayout.Title>
      <SectionPageLayout.LeadingActions>
        {showSectionTabs ? (
          <Tabs value={activeSection} onValueChange={handleSectionChange}>
            <TabsList className='max-w-full flex-wrap justify-start group-data-horizontal/tabs:h-auto'>
              {visibleSections.map((section) => (
                <TabsTrigger key={section} value={section}>
                  {t(SECTION_META[section].titleKey)}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        ) : null}
        {activeSection === 'models' && (
          <>
            {isAdmin || isOwner ? (
              <Tabs
                value={search.scope === 'team' ? 'team' : viewScope}
                onValueChange={(scope) => {
                  if (scope !== 'self' && scope !== 'all' && scope !== 'team') {
                    return
                  }
                  void navigate({
                    to: '/dashboard/$section',
                    params: { section: 'models' },
                    search: { scope },
                  })
                }}
              >
                <TabsList>
                  {isAdmin && (
                    <TabsTrigger value='all'>{t('All users')}</TabsTrigger>
                  )}
                  <TabsTrigger value='self'>{t('Only Mine')}</TabsTrigger>
                  {isOwner && (
                    <TabsTrigger value='team'>{t('My team')}</TabsTrigger>
                  )}
                </TabsList>
              </Tabs>
            ) : null}
            <Button
              variant='outline'
              render={
                <Link
                  to='/usage-logs/$section'
                  params={{ section: 'common' }}
                  search={{
                    scope: search.scope === 'team' ? 'team' : viewScope,
                    team: adminTeamId,
                    member: isTeamView ? search.member : undefined,
                    username: modelFilters.username,
                    startTime: modelFilters.start_timestamp?.getTime(),
                    endTime: modelFilters.end_timestamp?.getTime(),
                    type: ['2'],
                  }}
                />
              }
            >
              {t('Request details')}
            </Button>
          </>
        )}
      </SectionPageLayout.LeadingActions>
      <SectionPageLayout.Actions>{sectionActions}</SectionPageLayout.Actions>
      <SectionPageLayout.Content>
        <div className='space-y-3 sm:space-y-4'>
          {activeSection === 'models' && isTeamView && teamContent}
          {activeSection === 'models' && !isTeamView && (
            <>
              <FadeIn>
                <Suspense fallback={<LogStatCardsFallback />}>
                  <LazyLogStatCards
                    key={`${viewScope}-${dataRetry}`}
                    viewScope={viewScope}
                    filters={modelFilters}
                    onDataUpdate={handleDataUpdate}
                  />
                </Suspense>
              </FadeIn>
              {isAdmin && viewScope === 'all' && (
                <FadeIn delay={0.05}>
                  <Suspense fallback={<PerformanceOverviewFallback />}>
                    <LazyPerformanceOverview />
                  </Suspense>
                </FadeIn>
              )}
              <FadeIn delay={0.15}>
                <Suspense fallback={<ModelChartsFallback />}>
                  {dataError ? (
                    <ErrorState
                      onRetry={() => setDataRetry((value) => value + 1)}
                    />
                  ) : (
                    <LazyModelCharts
                      data={modelData}
                      loading={dataLoading}
                      defaultChartTab={chartPreferences.modelAnalyticsChart}
                      defaultMetric={chartPreferences.usageMetric}
                      timeGranularity={
                        modelFilters.time_granularity ||
                        DEFAULT_TIME_GRANULARITY
                      }
                    />
                  )}
                </Suspense>
              </FadeIn>
            </>
          )}
          {activeSection === 'users' && (
            <FadeIn>
              <Suspense fallback={<ModelChartsFallback />}>
                <LazyUserCharts
                  filters={userChartsFilters}
                  onFiltersChange={setUserChartsFilters}
                />
              </Suspense>
            </FadeIn>
          )}
          {activeSection === 'flow' && (
            <FadeIn>
              <Suspense fallback={<ModelChartsFallback />}>
                <LazyFlowCharts
                  filters={modelFilters}
                  sensitiveVisible={flowSensitiveVisible}
                />
              </Suspense>
            </FadeIn>
          )}
        </div>
      </SectionPageLayout.Content>
    </SectionPageLayout>
  )
}
