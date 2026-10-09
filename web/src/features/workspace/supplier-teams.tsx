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
import { ChevronDown, RefreshCw, Users } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { toIntlLocale } from '@/i18n/languages'
import { hasPermission } from '@/lib/admin-permissions'
import { api } from '@/lib/api'
import { formatCurrencyFromUSD } from '@/lib/currency'
import { formatQuota } from '@/lib/format'
import { requireServerSuccess } from '@/lib/server-error-message'
import { useAuthStore } from '@/stores/auth-store'

import type { WorkspaceTeam } from './api'
import { SubscriptionSummary } from './subscription-summary'
import { TeamMembersTable } from './team-members-table'
import { formatWorkspaceTokens } from './token-format'

export function SupplierTeams() {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const user = useAuthStore((state) => state.auth.user)
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<number[]>([])
  const teams = useQuery({
    queryKey: ['supplier-teams', user?.id],
    enabled: Boolean(user?.id) && hasPermission(user, 'team', 'read'),
    queryFn: async () =>
      requireServerSuccess(
        (
          await api.get<{ success: boolean; data: { teams: WorkspaceTeam[] } }>(
            '/api/team/workspaces'
          )
        ).data
      ).data.teams,
    refetchInterval: 30000,
  })
  if (teams.isError) {
    return (
      <ErrorState
        title={t('Unable to load teams')}
        onRetry={() => void teams.refetch()}
      />
    )
  }
  if (teams.isPending) return <Skeleton className='h-48' />
  const visible = (teams.data ?? []).filter((item) =>
    `${item.team?.name ?? ''} ${item.members.map((m) => `${m.user_id} ${m.username} ${m.display_name}`).join(' ')}`
      .toLowerCase()
      .includes(search.trim().toLowerCase())
  )
  return (
    <div className='space-y-4'>
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <div>
          <h2 className='font-semibold'>{t('Team breakdown')}</h2>
        </div>
        <div className='flex flex-wrap gap-2'>
          <Button
            variant='outline'
            disabled={!expanded.length}
            onClick={() => setExpanded([])}
          >
            {t('Collapse all')}
          </Button>
          <Button
            variant='outline'
            disabled={teams.isFetching}
            aria-label={t('Refresh')}
            onClick={() => void teams.refetch()}
          >
            <RefreshCw className='size-4' />
          </Button>
        </div>
      </div>
      <Input
        className='max-w-sm'
        aria-label={t('Search teams or members')}
        placeholder={t('Search teams or members')}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      {visible.map((item) => {
        if (!item.team) return null
        const team = item.team
        const owner = item.members.find((member) => member.is_owner)
        const isExpanded = expanded.includes(team.id)
        const now = Date.now() / 1000
        const subscriptionConflict =
          item.subscription_conflict ||
          (team.funding_version === 1 &&
            (item.subscriptions ?? []).filter(
              ({ subscription: sub }) =>
                sub.status === 'active' && sub.end_time > now
            ).length > 1)
        const active = (item.subscriptions ?? []).filter(
          ({ subscription: sub }) =>
            sub.status === 'active' &&
            sub.start_time <= now &&
            sub.end_time > now
        )
        const total = active.reduce(
          (acc, { subscription: sub }) => ({
            weekly:
              acc.weekly + (sub.weekly_limit_amount ?? sub.weekly_amount ?? 0),
            weeklyUsed: acc.weeklyUsed + (sub.weekly_used ?? 0),
          }),
          { weekly: 0, weeklyUsed: 0 }
        )
        return (
          <Collapsible
            key={team.id}
            open={isExpanded}
            onOpenChange={(open) =>
              setExpanded((ids) =>
                open
                  ? [...ids.filter((id) => id !== team.id), team.id]
                  : ids.filter((id) => id !== team.id)
              )
            }
          >
            <Card className='overflow-hidden'>
              <CardHeader className='gap-4'>
                <div className='flex flex-wrap items-start justify-between gap-3'>
                  <div className='min-w-0'>
                    <CardTitle className='break-words'>{team.name}</CardTitle>
                    <p className='text-muted-foreground mt-1 text-sm'>
                      {t('Owner')}:{' '}
                      {owner?.display_name ||
                        owner?.username ||
                        `#${team.owner_user_id}`}{' '}
                      · {t('{{count}} members', { count: item.members.length })}
                    </p>
                  </div>
                  <div className='flex flex-wrap gap-2'>
                    {hasPermission(user, 'team', 'write') && (
                      <Button
                        variant='outline'
                        size='sm'
                        render={<Link to='/users' search={{ team: team.id }} />}
                      >
                        {t('Manage team')}
                      </Button>
                    )}
                    <CollapsibleTrigger
                      render={<Button variant='outline' size='sm' />}
                    >
                      <ChevronDown
                        className={`size-4 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                      />
                      {isExpanded ? t('Hide members') : t('Member breakdown')}
                    </CollapsibleTrigger>
                  </div>
                </div>
                <div>
                  <p className='text-muted-foreground mb-1 text-xs'>
                    {t('Monthly subscription')}
                  </p>
                  <SubscriptionSummary
                    subscriptions={item.subscriptions ?? []}
                  />
                </div>
                <dl className='grid grid-cols-1 gap-4 sm:grid-cols-3'>
                  {[
                    {
                      label: t('Available balance'),
                      value: formatCurrencyFromUSD(item.pool_balance_usd ?? 0, {
                        locale,
                      }),
                    },
                    {
                      label: t('Cumulative usage'),
                      value: formatCurrencyFromUSD(item.used_usd ?? 0, {
                        locale,
                      }),
                    },
                    {
                      label: t('Tokens used'),
                      value: formatWorkspaceTokens(
                        (item.prompt_tokens ?? 0) +
                          (item.completion_tokens ?? 0),
                        locale
                      ),
                    },
                  ].map((metric) => (
                    <div key={metric.label}>
                      <dt className='text-muted-foreground text-xs'>
                        {metric.label}
                      </dt>
                      <dd className='mt-1 text-xl font-semibold tabular-nums'>
                        {metric.value}
                      </dd>
                    </div>
                  ))}
                </dl>
                <div className='text-muted-foreground flex flex-wrap gap-x-6 gap-y-1 border-t pt-3 text-sm'>
                  {subscriptionConflict && (
                    <span role='alert' className='text-destructive'>
                      {t(
                        'The selected account has multiple active subscriptions. Contact an administrator to resolve this before use.'
                      )}
                    </span>
                  )}
                  {!subscriptionConflict &&
                    active.length > 0 &&
                    total.weekly > 0 && (
                      <span>
                        {t('Weekly allowance')}: {formatQuota(total.weeklyUsed)}{' '}
                        / {formatQuota(total.weekly)} {t('Used')}
                      </span>
                    )}
                </div>
              </CardHeader>
              <CollapsibleContent>
                <CardContent className='border-t p-0'>
                  <TeamMembersTable
                    members={item.members}
                    weeklyAllowance={team.funding_version === 1}
                    renderMember={(member) => (
                      <Link
                        to='/usage-logs/$section'
                        params={{ section: 'common' }}
                        search={{
                          scope: 'all',
                          member: member.user_id,
                          team: team.id,
                          type: ['2'],
                        }}
                        className='text-primary hover:underline'
                      >
                        {member.display_name || member.username}
                      </Link>
                    )}
                  />
                </CardContent>
              </CollapsibleContent>
            </Card>
          </Collapsible>
        )
      })}
      {!visible.length && (
        <EmptyState
          icon={Users}
          title={t('No teams to display')}
          description={t('Create a team or try a different search.')}
        />
      )}
    </div>
  )
}
