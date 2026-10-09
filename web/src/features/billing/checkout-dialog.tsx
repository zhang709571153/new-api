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
import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Dialog } from '@/components/dialog'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { submitPaymentForm } from '@/features/wallet/lib/payment'
import { toIntlLocale } from '@/i18n/languages'
import { useAuthStore } from '@/stores/auth-store'

import { payBalance, payOnline, type BillingOrder } from './api'
import { formatCNY, formatBillingDays } from './format'

export function CheckoutDialog(props: {
  order: BillingOrder | null
  methods: string[]
  walletCNY: number
  onClose: () => void
  onPaid: () => void
}) {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const [now, setNow] = useState(Date.now() / 1000)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(timer)
  }, [])
  const payment = useMutation({
    mutationFn: async (method: string) => {
      if (!props.order) return
      const userId = useAuthStore.getState().auth.user?.id
      const stillCurrent = () =>
        mounted.current && userId === useAuthStore.getState().auth.user?.id
      if (method === 'balance') {
        await payBalance(props.order.trade_no)
        if (!stillCurrent()) return
        props.onPaid()
        props.onClose()
      } else {
        const result = await payOnline(props.order.trade_no, method)
        if (!stillCurrent()) return
        submitPaymentForm(result.url, result.params)
        props.onPaid()
        props.onClose()
      }
    },
  })
  const order = props.order
  let balancePaymentLabel =
    order?.funding_scope === 'team'
      ? t('Pay with personal balance')
      : t('Pay with balance')
  if (order?.price_cents === 0) balancePaymentLabel = t('Confirm upgrade')
  const expired = !!order && order.expires_at <= now
  const busy = payment.isPending || expired
  const selectedMethod = order?.payment_method.startsWith('epay:')
    ? order.payment_method.slice('epay:'.length)
    : null
  const methods = selectedMethod
    ? props.methods.filter((method) => method === selectedMethod)
    : props.methods
  return (
    <Dialog
      open={!!order}
      onOpenChange={(open) => {
        if (!open && !payment.isPending) props.onClose()
      }}
      title={order?.kind === 'paygo' ? t('Top up') : t('Review purchase')}
    >
      {order && (
        <div className='space-y-4'>
          <p className='text-2xl font-semibold'>
            {formatCNY(order.price_cents / 100, locale)}
          </p>
          <p>{order.plan_title || t('Top up')}</p>
          {order.kind !== 'paygo' && (
            <p>
              {order.funding_scope === 'team'
                ? t('Team subscription')
                : t('Personal subscription')}
            </p>
          )}
          {order.funding_scope === 'team' && order.kind === 'subscription' && (
            <Alert>
              <AlertDescription>
                {t(
                  'Payment creates your team and makes you its owner. If you are a member of another team, you will leave it after payment succeeds.'
                )}
              </AlertDescription>
            </Alert>
          )}
          {!!order.weekly_amount && (
            <>
              <p>
                {t('Weekly allowance: {{amount}}', {
                  amount: formatCNY((order.weekly_amount / 500000) * 7, locale),
                })}
              </p>
              <p>
                {t('Total allowance: {{amount}}', {
                  amount: formatCNY(
                    (order.credited_quota / 500000) * 7,
                    locale
                  ),
                })}
              </p>
            </>
          )}
          {order.kind === 'paygo' ? (
            <p>
              {t('Permanent balance: {{amount}}', {
                amount: formatCNY(order.price_cents / 100, locale),
              })}
            </p>
          ) : (
            <>
              <p>
                {t('Duration: {{days}} days', {
                  days: formatBillingDays(order.duration_seconds, locale),
                })}
              </p>
              {order.old_subscription_id > 0 && (
                <Alert>
                  <AlertDescription>
                    <p>
                      {t(
                        'Future complete weeks: {{weeks}}. Residual value: {{amount}}.',
                        {
                          weeks: order.remaining_weeks,
                          amount: formatCNY(order.residual_cents / 100, locale),
                        }
                      )}
                    </p>
                    <p>
                      {t(
                        'Converted time: {{days}} days. The current week is excluded.',
                        {
                          days: formatBillingDays(
                            order.extension_seconds,
                            locale
                          ),
                        }
                      )}
                    </p>
                    <p>
                      {t(
                        'Your previous subscription ends immediately. A partial final week receives a proportional allowance.'
                      )}
                    </p>
                  </AlertDescription>
                </Alert>
              )}
            </>
          )}
          {expired && (
            <p role='alert'>
              {t('Quote expired. Close this dialog and request a new quote.')}
            </p>
          )}
          <div className='flex flex-wrap gap-2'>
            {order.kind !== 'paygo' &&
              !order.payment_method &&
              order.allow_balance_pay !== false && (
                <Button
                  disabled={busy || props.walletCNY < order.price_cents / 100}
                  onClick={() => payment.mutate('balance')}
                >
                  {balancePaymentLabel}
                </Button>
              )}
            {order.price_cents > 0 &&
              methods.map((method) => (
                <Button
                  key={method}
                  disabled={busy}
                  onClick={() => payment.mutate(method)}
                >
                  {method === 'alipay' ? t('Alipay') : t('WeChat Pay')}
                </Button>
              ))}
          </div>
          {order.kind !== 'paygo' &&
            props.walletCNY < order.price_cents / 100 && (
              <p className='text-muted-foreground text-sm'>
                {t(
                  'Insufficient balance. Top up or select an online payment method.'
                )}
              </p>
            )}
          {order.price_cents > 0 && !methods.length && (
            <p role='status'>{t('Online payment is not configured yet.')}</p>
          )}
        </div>
      )}
    </Dialog>
  )
}
