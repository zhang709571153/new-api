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
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ErrorState } from '@/components/error-state'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  getScopedWorkspace,
  type WorkspaceScope,
} from '@/features/workspace/api'
import { PersonalKeyCard } from '@/features/workspace/personal-key'
import { SubscriptionBalance } from '@/features/workspace/subscription-balance'
import { formatWorkspaceTokens } from '@/features/workspace/token-format'
import { useApiCredential } from '@/features/workspace/use-api-credential'
import { toIntlLocale } from '@/i18n/languages'
import { formatCurrencyFromUSD } from '@/lib/currency'
import { useAuthStore } from '@/stores/auth-store'

import { CodexGuide } from './codex-guide'
import { PerformanceHealthPanel } from './performance-health-panel'

export function WorkspaceOverview() {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const userId = useAuthStore((state) => state.auth.user?.id) ?? 0
  const [scope, setScope] = useState<WorkspaceScope>('current')
  const workspace = useQuery({
    queryKey: ['workspace', userId, scope],
    queryFn: () => getScopedWorkspace(scope),
    enabled: Boolean(userId),
    refetchInterval: 30000,
  })
  const data = workspace.data
  const selectedScope =
    data?.scope ?? (data?.mode === 'personal' ? 'personal' : 'team')
  const keyScope = data?.scope ?? scope
  const credential = useApiCredential({
    userId,
    keyId: data?.api_key?.id ?? 0,
    keyRevision: data?.api_key?.masked_key ?? '',
    scope: keyScope,
  })
  const now = Date.now() / 1000
  const subscriptionBalance = data?.subscription_conflict
    ? 0
    : (data?.subscriptions ?? []).reduce((sum, { subscription: sub }) => {
        if (
          sub.status !== 'active' ||
          sub.start_time > now ||
          sub.end_time <= now
        ) {
          return sum
        }
        const remaining = Math.max(0, sub.amount_total - sub.amount_used)
        const weekly = sub.weekly_amount
          ? Math.max(
              0,
              (sub.weekly_limit_amount ?? sub.weekly_amount) -
                (sub.weekly_used ?? 0)
            )
          : remaining
        return sum + weekly / 500000
      }, 0)
  const paygoBalance =
    data?.paygo_balance_usd ??
    data?.personal_balance_usd ??
    data?.balance_usd ??
    0
  const availableBalance =
    data?.scope || data?.mode === 'member'
      ? data.balance_usd
      : paygoBalance + subscriptionBalance
  if (workspace.isError) {
    return (
      <ErrorState
        title={t('Unable to load usage. Please refresh and try again.')}
        onRetry={() => void workspace.refetch()}
      />
    )
  }
  return (
    <div className='w-full min-w-0 space-y-3'>
      {data?.team && (
        <div className='flex flex-wrap items-center gap-3'>
          <Tabs
            value={selectedScope}
            onValueChange={(value) => setScope(value as WorkspaceScope)}
          >
            <TabsList aria-label={t('Funding source')}>
              <TabsTrigger value='personal'>{t('Personal')}</TabsTrigger>
              <TabsTrigger value='team'>{t('Team')}</TabsTrigger>
            </TabsList>
          </Tabs>
          {selectedScope === 'team' && (
            <span className='text-muted-foreground min-w-0 truncate text-sm'>
              {data.team.name}
            </span>
          )}
        </div>
      )}
      <div className='grid min-w-0 gap-3 sm:grid-cols-2'>
        {[
          {
            label: t('Available balance'),
            value: formatCurrencyFromUSD(availableBalance, { locale }),
            breakdown: data?.mode !== 'member',
            topUp: selectedScope === 'personal',
          },
          {
            label: t('Tokens used'),
            value: formatWorkspaceTokens(
              (data?.prompt_tokens ?? 0) + (data?.completion_tokens ?? 0),
              locale
            ),
          },
        ].map((item) => (
          <Card key={item.label} size='sm'>
            <CardHeader className='min-h-7 items-center'>
              <CardTitle className='text-muted-foreground text-sm font-normal'>
                {item.label}
              </CardTitle>
              {item.topUp && (
                <CardAction className='row-span-1 self-center'>
                  <Button
                    size='sm'
                    variant='outline'
                    role='link'
                    render={<Link to='/wallet' />}
                  >
                    {t('Top up')}
                  </Button>
                </CardAction>
              )}
            </CardHeader>
            <CardContent>
              {workspace.isPending ? (
                <Skeleton className='h-8 w-24' />
              ) : (
                <p className='text-3xl font-semibold tabular-nums'>
                  {item.value}
                </p>
              )}
              {item.breakdown && !workspace.isPending && (
                <dl className='text-muted-foreground mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs'>
                  {selectedScope === 'personal' && (
                    <div className='flex flex-wrap gap-x-2'>
                      <dt>{t('Pay-as-you-go balance')}</dt>
                      <dd>{formatCurrencyFromUSD(paygoBalance, { locale })}</dd>
                    </div>
                  )}
                  <div className='flex flex-wrap gap-x-2'>
                    <dt>{t('Available subscription allowance')}</dt>
                    <dd>
                      {formatCurrencyFromUSD(subscriptionBalance, { locale })}
                    </dd>
                  </div>
                </dl>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
      <div className='grid min-w-0 items-start gap-3 xl:grid-cols-2'>
        <div className='min-w-0 space-y-3'>
          {data && (
            <PersonalKeyCard
              key={`key:${userId}:${keyScope}`}
              apiKey={data.api_key}
              scope={keyScope}
              credential={credential}
            />
          )}
          {data?.subscription_conflict ? (
            <p role='alert' className='text-destructive text-sm'>
              {t(
                'The selected account has multiple active subscriptions. Contact an administrator to resolve this before use.'
              )}
            </p>
          ) : (
            <SubscriptionBalance subscriptions={data?.subscriptions} />
          )}
        </div>
        <CodexGuide
          key={`setup:${userId}:${keyScope}`}
          credential={credential}
          hasKey={Boolean(data?.api_key?.id)}
        />
      </div>
      {!!userId && <PerformanceHealthPanel />}
      <div className='flex flex-wrap gap-2'>
        <Button variant='outline' role='link' render={<Link to='/usage' />}>
          {t('View my usage')}
        </Button>
        <Button variant='ghost' role='link' render={<Link to='/team' />}>
          {data?.team ? t('My team') : t('Join team')}
        </Button>
      </div>
    </div>
  )
}
