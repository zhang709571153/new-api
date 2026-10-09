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
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Dialog } from '@/components/dialog'
import { ErrorState } from '@/components/error-state'
import { SectionPageLayout } from '@/components/layout'
import { LoadingState } from '@/components/loading-state'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { SubscriptionBalance } from '@/features/workspace/subscription-balance'
import { toIntlLocale } from '@/i18n/languages'
import { useAuthStore } from '@/stores/auth-store'

import {
  billingOverview,
  createCheckout,
  reconcile,
  type BillingOrder,
  type CheckoutRequest,
} from './api'
import { CheckoutDialog } from './checkout-dialog'
import { formatCNY } from './format'
import { PaygoForm } from './paygo-form'

type FundingScope = 'personal' | 'team'
type BillingWalletProps = {
  scope?: FundingScope
  onScopeChange?: (scope: FundingScope) => void
}

export function BillingWallet(props: BillingWalletProps = {}) {
  const userId = useAuthStore((state) => state.auth.user?.id)
  return <BillingWalletSession key={userId ?? 'signed-out'} {...props} />
}

function BillingWalletSession(props: BillingWalletProps) {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const client = useQueryClient()
  const userId = useAuthStore((state) => state.auth.user?.id)
  const [order, setOrder] = useState<BillingOrder | null>(null)
  const [topupOpen, setTopupOpen] = useState(false)
  const [localScope, setLocalScope] = useState<FundingScope>('personal')
  const scope = props.scope ?? localScope
  const changeScope = (value: unknown) => {
    if (value !== 'personal' && value !== 'team') return
    setTopupOpen(false)
    setOrder(null)
    setLocalScope(value)
    props.onScopeChange?.(value)
  }
  const overview = useQuery({
    queryKey: ['billing', 'overview', userId],
    queryFn: billingOverview,
    enabled: !!userId,
    refetchInterval: 30000,
  })
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['billing'] })
    void client.invalidateQueries({ queryKey: ['workspace'] })
    void client.invalidateQueries({ queryKey: ['workspace-team'] })
    void client.invalidateQueries({ queryKey: ['workspace-team-trends'] })
  }
  const checkout = useMutation({
    mutationFn: (request: Omit<CheckoutRequest, 'idempotency_key'>) =>
      createCheckout({ ...request, idempotency_key: crypto.randomUUID() }),
    onSuccess: (result) => {
      setTopupOpen(false)
      setOrder(result)
      refresh()
    },
  })
  const checkPayment = useMutation({
    mutationFn: reconcile,
    onSuccess: refresh,
  })
  const pendingTrades = (overview.data?.orders ?? [])
    .filter(
      (item) =>
        item.status === 'pending' &&
        item.payment_method.startsWith('epay:') &&
        item.created_at > Date.now() / 1000 - 3 * 86400
    )
    .slice(0, 5)
    .map((item) => item.trade_no)
  const confirmations = useQuery({
    queryKey: ['billing-payment-confirmations', userId, pendingTrades],
    queryFn: () =>
      Promise.allSettled(pendingTrades.map((trade) => reconcile(trade))),
    enabled: !!userId && pendingTrades.length > 0,
    refetchInterval: 15000,
    refetchIntervalInBackground: false,
    retry: false,
    meta: { errorToast: false },
  })
  useEffect(() => {
    if (userId !== useAuthStore.getState().auth.user?.id) return
    const confirmed = confirmations.data?.some(
      (result) =>
        result.status === 'fulfilled' &&
        (result.value?.status === 'paid' ||
          result.value?.status === 'wallet_credited')
    )
    if (!confirmed) return
    void client.invalidateQueries({ queryKey: ['billing'] })
    void client.invalidateQueries({ queryKey: ['workspace'] })
    void client.invalidateQueries({ queryKey: ['workspace-team'] })
    void client.invalidateQueries({ queryKey: ['workspace-team-trends'] })
  }, [confirmations.data, client, userId])
  if (!userId || overview.isPending) return <LoadingState />
  if (overview.isError || !overview.data) {
    return <ErrorState onRetry={() => void overview.refetch()} />
  }
  const data = overview.data
  const wallet =
    (data.workspace.paygo_balance_usd ??
      data.workspace.personal_balance_usd ??
      data.workspace.balance_usd) * 7
  const personalSubscriptions = data.subscriptions.filter(
    ({ subscription }) =>
      !subscription.workspace_team_id &&
      subscription.status === 'active' &&
      subscription.end_time > Date.now() / 1000
  )
  const personalActive =
    data.currentSubscription === undefined
      ? personalSubscriptions[0]?.subscription
      : data.currentSubscription
  const active =
    scope === 'team' ? data.currentTeamSubscription : personalActive
  const conflict =
    scope === 'team'
      ? data.teamSubscriptionConflict
      : data.subscriptionConflict || personalSubscriptions.length > 1
  const teamOwner = (data.ownedTeamId ?? 0) > 0
  const plans = scope === 'team' ? (data.teamPlans ?? []) : data.plans
  const subscriptions = active ? [{ subscription: active }] : []
  const inStock =
    data.storefrontEnabled && data.info?.payment_compliance_confirmed === true
  const enabled = inStock
  const methods = data.info?.enable_online_topup
    ? (data.info?.pay_methods ?? [])
        .map((method) => method.type)
        .filter((method) => method === 'alipay' || method === 'wxpay')
    : []
  const cny = (value: number) => formatCNY(value, locale)
  const orderStatus = (item: BillingOrder) => {
    if (item.status === 'wallet_credited') {
      return t('Credited to permanent balance')
    }
    if (item.status === 'paid') return t('Subscription activated')
    if (!item.payment_method && item.expires_at < Date.now() / 1000) {
      return t('Expired')
    }
    return t('Awaiting payment confirmation')
  }
  return (
    <SectionPageLayout>
      <SectionPageLayout.Title>{t('Plans and PAYGO')}</SectionPageLayout.Title>
      <SectionPageLayout.Content>
        <div className='mx-auto w-full max-w-6xl space-y-6'>
          <Tabs value={scope} onValueChange={changeScope}>
            <TabsList aria-label={t('Subscription scope')}>
              <TabsTrigger value='personal'>
                {t('Personal subscription')}
              </TabsTrigger>
              <TabsTrigger value='team'>{t('Team subscription')}</TabsTrigger>
            </TabsList>
          </Tabs>
          <div className='grid gap-4 lg:grid-cols-2'>
            {scope === 'personal' && (
              <Card>
                <CardHeader>
                  <CardTitle>{t('Pay-as-you-go balance')}</CardTitle>
                </CardHeader>
                <CardContent className='space-y-4'>
                  <p className='text-3xl font-semibold'>{cny(wallet)}</p>
                  <Button
                    disabled={!enabled || !methods.length || checkout.isPending}
                    onClick={() => setTopupOpen(true)}
                  >
                    {inStock && methods.length
                      ? t('Top up')
                      : t('Out of stock')}
                  </Button>
                </CardContent>
              </Card>
            )}
            <div className='space-y-4'>
              {conflict ? (
                <Alert variant='destructive'>
                  <AlertDescription>
                    {t(
                      'The selected account has multiple active subscriptions. Contact an administrator to resolve this before use.'
                    )}
                  </AlertDescription>
                </Alert>
              ) : (
                <SubscriptionBalance subscriptions={subscriptions} />
              )}
            </div>
          </div>
          {scope === 'team' && !teamOwner && (
            <p role='status'>
              {t(
                'Payment creates your team and makes you its owner. If you are a member of another team, you will leave it after payment succeeds.'
              )}
            </p>
          )}
          {scope === 'team' && teamOwner && !active && !conflict && (
            <p role='status'>
              {t(
                'You already own a team. Dissolve the expired team before purchasing a new team subscription.'
              )}
            </p>
          )}
          {active &&
            !conflict &&
            !(
              active.purchase_price_cents && active.purchase_price_cents > 0
            ) && (
              <p role='status'>
                {t('Contact an administrator to change this subscription.')}
              </p>
            )}
          <div className='space-y-2'>
            <h2 className='text-xl font-semibold'>
              {t('28-day subscriptions')}
            </h2>
          </div>
          <div className='grid gap-4 sm:grid-cols-2 xl:grid-cols-3'>
            {plans.map((plan) => {
              const isCurrent = active?.plan_id === plan.id
              const canUpgrade =
                !!active &&
                active.start_time <= Date.now() / 1000 &&
                (active.purchase_price_cents ?? 0) > 0 &&
                Math.round(plan.price_amount * 100) >
                  (active.purchase_price_cents ?? 0) &&
                !isCurrent
              const canStart = !active && (scope === 'personal' || !teamOwner)
              const canBuy =
                enabled &&
                !checkout.isPending &&
                !conflict &&
                (canUpgrade || canStart)
              let label = t('Subscribe')
              if (active) label = canUpgrade ? t('Upgrade') : t('Unavailable')
              if (isCurrent) label = t('Current plan')
              if (!inStock) label = t('Out of stock')
              return (
                <Card
                  key={plan.id}
                  className={plan.title === 'Max' ? 'border-primary' : ''}
                >
                  <CardHeader>
                    <CardTitle className='flex items-center justify-between gap-2'>
                      {plan.title}
                      {plan.title === 'Max' && (
                        <Badge>{t('Recommended')}</Badge>
                      )}
                    </CardTitle>
                    {plan.subtitle && (
                      <p className='text-muted-foreground text-sm'>
                        {plan.subtitle}
                      </p>
                    )}
                  </CardHeader>
                  <CardContent className='space-y-4'>
                    <p className='text-2xl font-semibold'>
                      {cny(plan.price_amount)}{' '}
                      <span className='text-muted-foreground text-sm'>
                        / {t('28 days')}
                      </span>
                    </p>
                    <p>
                      {t(
                        'Includes {{total}} allowance · {{weekly}} every 7 days',
                        {
                          total: cny((plan.total_amount / 500000) * 7),
                          weekly: cny(((plan.weekly_amount ?? 0) / 500000) * 7),
                        }
                      )}
                    </p>
                    <Button
                      className='w-full'
                      disabled={!canBuy}
                      onClick={() =>
                        checkout.mutate({
                          kind: active ? 'upgrade' : 'subscription',
                          funding_scope: scope,
                          old_subscription_id: active?.id,
                          plan_id: plan.id,
                        })
                      }
                    >
                      {label}
                    </Button>
                  </CardContent>
                </Card>
              )
            })}
          </div>
          {!plans.length && (
            <p role='status'>{t('No subscription plans are available.')}</p>
          )}
          <Card>
            <CardHeader>
              <CardTitle>{t('Purchase history')}</CardTitle>
            </CardHeader>
            <CardContent className='space-y-3'>
              {!data.orders.some(
                (item) => (item.funding_scope ?? 'personal') === scope
              ) && (
                <p className='text-muted-foreground'>{t('No orders yet')}</p>
              )}
              {data.orders
                .filter((item) => (item.funding_scope ?? 'personal') === scope)
                .map((item) => (
                  <div
                    key={item.id}
                    className='flex flex-wrap items-center justify-between gap-3 border-b pb-3 last:border-0'
                  >
                    <div className='min-w-0'>
                      <p className='font-medium'>
                        {item.plan_title || t('Top up')} ·{' '}
                        {cny(item.price_cents / 100)}
                      </p>
                      <p className='text-muted-foreground text-xs break-all'>
                        {item.trade_no}
                      </p>
                      <p className='text-muted-foreground text-sm'>
                        {orderStatus(item)}
                      </p>
                    </div>
                    {item.status === 'pending' &&
                      item.payment_method.startsWith('epay:') && (
                        <Button
                          variant='outline'
                          disabled={checkPayment.isPending}
                          onClick={() => checkPayment.mutate(item.trade_no)}
                        >
                          {t('Check payment')}
                        </Button>
                      )}
                    {item.status === 'pending' &&
                      inStock &&
                      (!item.payment_method ||
                        item.payment_method.startsWith('epay:')) &&
                      item.expires_at > Date.now() / 1000 && (
                        <Button
                          variant='outline'
                          onClick={() => setOrder(item)}
                        >
                          {t('Continue payment')}
                        </Button>
                      )}
                  </div>
                ))}
            </CardContent>
          </Card>
          <Dialog
            open={
              scope === 'personal' && topupOpen && enabled && methods.length > 0
            }
            onOpenChange={setTopupOpen}
            title={t('Top up')}
          >
            <PaygoForm
              disabled={!enabled || checkout.isPending}
              onSubmit={(amount) =>
                checkout.mutate({
                  kind: 'paygo',
                  funding_scope: 'personal',
                  amount_cny: amount,
                })
              }
            />
          </Dialog>
          <CheckoutDialog
            order={
              inStock && (order?.funding_scope ?? 'personal') === scope
                ? order
                : null
            }
            methods={methods}
            walletCNY={wallet}
            onClose={() => setOrder(null)}
            onPaid={refresh}
          />
        </div>
      </SectionPageLayout.Content>
    </SectionPageLayout>
  )
}
