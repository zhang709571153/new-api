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
import { useCallback, useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import { ErrorState } from '@/components/error-state'
import { SectionPageLayout } from '@/components/layout'
import type { NavGroup } from '@/components/layout/types'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { CacheStatsDialog } from '@/features/system-settings/general/channel-affinity/cache-stats-dialog'
import { getWorkspaceTeam } from '@/features/workspace/api'
import { useSidebarConfig } from '@/hooks/use-sidebar-config'
import { useAuthStore } from '@/stores/auth-store'

import { UserInfoDialog } from './components/dialogs/user-info-dialog'
import {
  type LogsViewScope,
  UsageLogsProvider,
  useLogsViewScope,
  useUsageLogsContext,
} from './components/usage-logs-provider'
import { UsageLogsTable } from './components/usage-logs-table'
import { getDefaultTimeRange } from './lib/utils'
import {
  isUsageLogsSectionId,
  USAGE_LOGS_DEFAULT_SECTION,
  type UsageLogsSectionId,
} from './section-registry'

const route = getRouteApi('/_authenticated/usage-logs/$section')
const TASK_LOG_SECTIONS = ['drawing', 'task'] as const

const SECTION_META: Record<UsageLogsSectionId, { titleKey: string }> = {
  common: {
    titleKey: 'Common Logs',
  },
  drawing: {
    titleKey: 'Drawing Logs',
  },
  task: {
    titleKey: 'Task Logs',
  },
}

function UsageLogsContent() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const params = route.useParams()
  const searchParams = route.useSearch()
  const defaultRange = useMemo(() => getDefaultTimeRange(), [])
  const activeCategory: UsageLogsSectionId =
    params.section && isUsageLogsSectionId(params.section)
      ? params.section
      : USAGE_LOGS_DEFAULT_SECTION
  const {
    selectedUserId,
    userInfoDialogOpen,
    setUserInfoDialogOpen,
    affinityTarget,
    affinityDialogOpen,
    setAffinityDialogOpen,
  } = useUsageLogsContext()
  const { canManageScope, viewScope, setViewScope } = useLogsViewScope()
  const userId = useAuthStore((state) => state.auth.user?.id)
  const team = useQuery({
    queryKey: ['workspace-team', userId],
    queryFn: getWorkspaceTeam,
    enabled: Boolean(userId) && activeCategory === 'common',
  })
  const isOwner = Boolean(userId) && team.data?.team?.owner_user_id === userId
  const tabNavGroups = useMemo<NavGroup[]>(
    () => [
      {
        title: 'Task Logs',
        items: TASK_LOG_SECTIONS.map((section) => ({
          title: SECTION_META[section].titleKey,
          url: `/usage-logs/${section}`,
        })),
      },
    ],
    []
  )
  const filteredTabGroups = useSidebarConfig(tabNavGroups)
  const visibleSections = useMemo(
    () =>
      (filteredTabGroups[0]?.items ?? [])
        .map((item) => {
          if (!('url' in item) || typeof item.url !== 'string') return null
          return item.url.split('/').pop() ?? null
        })
        .filter((section): section is UsageLogsSectionId =>
          Boolean(section && isUsageLogsSectionId(section))
        ),
    [filteredTabGroups]
  )

  const handleSectionChange = useCallback(
    (section: string) => {
      void navigate({
        to: '/usage-logs/$section',
        params: { section: section as UsageLogsSectionId },
      })
    },
    [navigate]
  )

  const handleViewScopeChange = useCallback(
    (scope: string) => {
      if (
        scope === 'all' ||
        scope === 'self' ||
        (scope === 'team' && isOwner)
      ) {
        setViewScope(scope as LogsViewScope)
        void navigate({
          to: '/usage-logs/$section',
          params: { section: activeCategory },
          search: {
            ...searchParams,
            scope,
            member: undefined,
            team: undefined,
            username: undefined,
            channel: undefined,
            type: undefined,
            page: 1,
          },
          replace: true,
        })
      }
    },
    [setViewScope, navigate, activeCategory, searchParams, isOwner]
  )

  const pageMeta =
    activeCategory === 'common' ? SECTION_META.common : SECTION_META.task
  const showTaskSwitcher =
    activeCategory !== 'common' && visibleSections.length > 1

  let tableContent = <UsageLogsTable logCategory={activeCategory} />
  if (viewScope === 'team') {
    if (team.isPending) {
      tableContent = <Skeleton className='h-48 w-full' />
    } else if (team.isError) {
      tableContent = <ErrorState onRetry={() => void team.refetch()} />
    } else if (!isOwner) {
      tableContent = (
        <p className='text-muted-foreground'>
          {t('Team request details are available to the team owner.')}
        </p>
      )
    }
  }

  return (
    <>
      <SectionPageLayout fixedContent>
        <SectionPageLayout.Title>
          {activeCategory === 'common'
            ? t('Request details')
            : t(pageMeta.titleKey)}
        </SectionPageLayout.Title>
        <SectionPageLayout.LeadingActions>
          {activeCategory === 'common' && (
            <Button
              variant='outline'
              render={
                <Link
                  to='/dashboard/$section'
                  params={{ section: 'models' }}
                  search={{
                    scope: viewScope,
                    member: searchParams.member,
                    team: viewScope === 'all' ? searchParams.team : undefined,
                    username:
                      viewScope === 'all' ? searchParams.username : undefined,
                    startTime:
                      searchParams.startTime ?? defaultRange.start.getTime(),
                    endTime: searchParams.endTime ?? defaultRange.end.getTime(),
                  }}
                />
              }
            >
              {t('Usage trends')}
            </Button>
          )}
          {(canManageScope || (activeCategory === 'common' && isOwner)) && (
            <Tabs value={viewScope} onValueChange={handleViewScopeChange}>
              <TabsList>
                {canManageScope && (
                  <TabsTrigger value='all'>{t('All users')}</TabsTrigger>
                )}
                <TabsTrigger value='self'>{t('Only Mine')}</TabsTrigger>
                {activeCategory === 'common' && isOwner && (
                  <TabsTrigger value='team'>{t('My team')}</TabsTrigger>
                )}
              </TabsList>
            </Tabs>
          )}
        </SectionPageLayout.LeadingActions>
        <SectionPageLayout.Content>
          <div className='flex h-full min-h-0 flex-col gap-4'>
            {viewScope === 'all' &&
              (searchParams.member || searchParams.team) && (
                <div className='flex flex-wrap items-center gap-2 text-sm'>
                  {searchParams.team && (
                    <span>
                      {t('Team')} #{searchParams.team}
                    </span>
                  )}
                  {searchParams.member && (
                    <span>
                      {t('Member')} #{searchParams.member}
                    </span>
                  )}
                  <Button
                    size='sm'
                    variant='ghost'
                    onClick={() =>
                      void navigate({
                        to: '/usage-logs/$section',
                        params: { section: activeCategory },
                        search: {
                          ...searchParams,
                          member: undefined,
                          team: undefined,
                          page: 1,
                        },
                      })
                    }
                  >
                    {t('Clear member filter')}
                  </Button>
                </div>
              )}
            {activeCategory === 'common' && (
              <p className='text-muted-foreground text-sm'>
                {t(
                  'Each row is one API request. A conversation may contain multiple requests. Open Details to see token categories and billing.'
                )}
              </p>
            )}
            {showTaskSwitcher && (
              <Tabs value={activeCategory} onValueChange={handleSectionChange}>
                <TabsList className='max-w-full flex-wrap justify-start group-data-horizontal/tabs:h-auto'>
                  {visibleSections.map((section) => (
                    <TabsTrigger key={section} value={section}>
                      {t(SECTION_META[section].titleKey)}
                    </TabsTrigger>
                  ))}
                </TabsList>
              </Tabs>
            )}
            <div className='min-h-0 flex-1'>{tableContent}</div>
          </div>
        </SectionPageLayout.Content>
      </SectionPageLayout>

      <UserInfoDialog
        userId={selectedUserId}
        open={userInfoDialogOpen}
        onOpenChange={setUserInfoDialogOpen}
      />

      <CacheStatsDialog
        open={affinityDialogOpen}
        onOpenChange={setAffinityDialogOpen}
        target={
          affinityTarget
            ? {
                rule_name: affinityTarget.rule_name || '',
                using_group:
                  affinityTarget.using_group ||
                  affinityTarget.selected_group ||
                  '',
                key_hint: affinityTarget.key_hint || '',
                key_fp: affinityTarget.key_fp || '',
              }
            : null
        }
      />
    </>
  )
}

export function UsageLogs() {
  const search = route.useSearch()
  const params = route.useParams()
  const scope =
    params.section !== 'common' && search.scope === 'team'
      ? 'self'
      : (search.scope ?? 'all')
  return (
    <UsageLogsProvider key={scope} initialViewScope={scope}>
      <UsageLogsContent />
    </UsageLogsProvider>
  )
}
