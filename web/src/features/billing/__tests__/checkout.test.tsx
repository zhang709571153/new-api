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
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18next from 'i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { submitPaymentForm } from '@/features/wallet/lib/payment'
import { SubscriptionBalance } from '@/features/workspace/subscription-balance'
import zh from '@/i18n/locales/zh.json'
import { useAuthStore } from '@/stores/auth-store'

import * as api from '../api'
import { CheckoutDialog } from '../checkout-dialog'
import { BillingWallet } from '../index'
import { PaygoForm } from '../paygo-form'

vi.mock('../api', () => ({
  billingOverview: vi.fn(),
  createCheckout: vi.fn(),
  reconcile: vi.fn(),
  payBalance: vi.fn(),
  payOnline: vi.fn(),
}))
vi.mock('@/features/wallet/lib/payment', () => ({ submitPaymentForm: vi.fn() }))

const quote: api.BillingOrder = {
  id: 1,
  trade_no: 'RYtest',
  kind: 'convert',
  plan_id: 2,
  plan_title: 'Max',
  price_cents: 0,
  credited_quota: 100,
  old_subscription_id: 9,
  remaining_weeks: 2,
  residual_cents: 19950,
  extension_seconds: 536836,
  duration_seconds: 536836,
  status: 'pending',
  payment_method: '',
  expires_at: Date.now() / 1000 + 600,
  created_at: Date.now() / 1000,
  subscription_id: 0,
}
function wrap(component: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>{component}</QueryClientProvider>
  )
}
beforeEach(async () => {
  vi.clearAllMocks()
  useAuthStore.getState().auth.setUser({ id: 1, username: 'buyer', role: 1 })
  await i18next.changeLanguage('en')
})

describe('PAYGO amount', () => {
  it.each([0.01, 1.23, 49, 51])(
    'accepts a custom CNY amount of %s',
    async (amount) => {
      const submit = vi.fn()
      wrap(<PaygoForm disabled={false} onSubmit={submit} />)
      await userEvent.clear(screen.getByRole('spinbutton'))
      await userEvent.type(screen.getByRole('spinbutton'), String(amount))
      await userEvent.click(screen.getByRole('button', { name: 'Top up' }))
      await waitFor(() => expect(submit).toHaveBeenCalledWith(amount))
    }
  )
  it.each(['0', '-1', '1.001', '10000.01'])(
    'rejects invalid amount %s and allows a valid preset',
    async (amount) => {
      const submit = vi.fn()
      wrap(<PaygoForm disabled={false} onSubmit={submit} />)
      const input = screen.getByRole('spinbutton')
      fireEvent.change(input, { target: { value: amount } })
      const form = input.closest('form')
      if (!form) throw new Error('Missing top-up form')
      fireEvent.submit(form)
      await waitFor(() => expect(input).toHaveAttribute('aria-invalid', 'true'))
      expect(submit).not.toHaveBeenCalled()
      await userEvent.click(screen.getByRole('button', { name: '¥200' }))
      await userEvent.click(screen.getByRole('button', { name: 'Top up' }))
      await waitFor(() => expect(submit).toHaveBeenCalledWith(200))
    }
  )
  it('disables amount and purchase when billing belongs to a team owner', () => {
    wrap(<PaygoForm disabled onSubmit={vi.fn()} />)
    expect(screen.getByRole('spinbutton')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Top up' })).toBeDisabled()
  })
})

describe('upgrade confirmation', () => {
  it('resumes an online order using its selected method without offering a second payment rail', async () => {
    const onlineOrder = {
      ...quote,
      kind: 'subscription',
      price_cents: 9900,
      payment_method: 'epay:alipay',
    }
    vi.mocked(api.payOnline).mockResolvedValue({
      url: 'https://payments.example/checkout',
      params: {},
    })
    wrap(
      <CheckoutDialog
        order={onlineOrder}
        methods={['alipay', 'wxpay']}
        walletCNY={100}
        onPaid={vi.fn()}
        onClose={vi.fn()}
      />
    )
    expect(
      screen.queryByRole('button', { name: 'Pay with balance' })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'WeChat Pay' })
    ).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Alipay' }))
    await waitFor(() =>
      expect(api.payOnline).toHaveBeenCalledWith(onlineOrder.trade_no, 'alipay')
    )
  })
  it('shows excluded current week, residual and duration before a no-payment upgrade', async () => {
    const onPaid = vi.fn(),
      onClose = vi.fn()
    vi.mocked(api.payBalance).mockResolvedValue({ ...quote, status: 'paid' })
    wrap(
      <CheckoutDialog
        order={quote}
        methods={[]}
        walletCNY={0}
        onPaid={onPaid}
        onClose={onClose}
      />
    )
    expect(screen.getByText(/Future complete weeks: 2/)).toHaveTextContent(
      '199.50'
    )
    expect(screen.getByText(/The current week is excluded/)).toBeVisible()
    expect(
      screen.getByText(/previous subscription ends immediately/)
    ).toBeVisible()
    await userEvent.click(
      screen.getByRole('button', { name: 'Confirm upgrade' })
    )
    await waitFor(() => expect(onPaid).toHaveBeenCalledOnce())
    expect(api.payBalance).toHaveBeenCalledWith('RYtest')
    expect(onClose).toHaveBeenCalledOnce()
  })
  it('blocks expired quotes and insufficient balance', () => {
    wrap(
      <CheckoutDialog
        order={{
          ...quote,
          kind: 'upgrade',
          price_cents: 89900,
          expires_at: Date.now() / 1000 - 1,
        }}
        methods={['alipay']}
        walletCNY={1}
        onPaid={vi.fn()}
        onClose={vi.fn()}
      />
    )
    expect(
      screen.getByRole('button', { name: 'Pay with balance' })
    ).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Alipay' })).toBeDisabled()
    expect(
      screen.getByText(
        'Quote expired. Close this dialog and request a new quote.'
      )
    ).toHaveAttribute('role', 'alert')
  })
  it('keeps confirmation open after payment failure', async () => {
    const close = vi.fn()
    vi.mocked(api.payBalance).mockRejectedValue(
      new Error('insufficient balance')
    )
    wrap(
      <CheckoutDialog
        order={quote}
        methods={[]}
        walletCNY={0}
        onPaid={vi.fn()}
        onClose={close}
      />
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'Confirm upgrade' })
    )
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Confirm upgrade' })
      ).toBeEnabled()
    )
    expect(close).not.toHaveBeenCalled()
  })
  it('renders Chinese copy after switching locale', async () => {
    i18next.addResourceBundle('zhCN', 'translation', zh.translation)
    await i18next.changeLanguage('zhCN')
    wrap(
      <CheckoutDialog
        order={quote}
        methods={[]}
        walletCNY={0}
        onPaid={vi.fn()}
        onClose={vi.fn()}
      />
    )
    expect(screen.getByRole('button', { name: '确认升级' })).toBeVisible()
    expect(screen.getByText(/当周不参与折算/)).toBeVisible()
  })
})

describe('payment lifecycle', () => {
  it.each(['account change', 'unmount'])(
    'does not redirect to an old account payment page after %s',
    async (scenario) => {
      let resolve!: (value: {
        url: string
        params: Record<string, string>
      }) => void
      vi.mocked(api.payOnline).mockReturnValue(
        new Promise((done) => {
          resolve = done
        })
      )
      const onPaid = vi.fn()
      const view = wrap(
        <CheckoutDialog
          order={{ ...quote, kind: 'subscription', price_cents: 9900 }}
          methods={['alipay']}
          walletCNY={0}
          onPaid={onPaid}
          onClose={vi.fn()}
        />
      )
      await userEvent.click(screen.getByRole('button', { name: 'Alipay' }))
      if (scenario === 'unmount') {
        view.unmount()
      } else {
        await act(async () =>
          useAuthStore
            .getState()
            .auth.setUser({ id: 2, username: 'another', role: 1 })
        )
      }
      await act(async () =>
        resolve({
          url: 'https://payments.invalid/fixture',
          params: { trade_no: 'old-account' },
        })
      )
      expect(submitPaymentForm).not.toHaveBeenCalled()
      expect(onPaid).not.toHaveBeenCalled()
    }
  )
})

describe('billing page', () => {
  const storefront = {
    storefrontEnabled: false,
    plans: [
      {
        id: 1,
        title: 'Lite',
        price_amount: 99,
        currency: 'CNY',
        duration_unit: 'day' as const,
        duration_value: 28,
        quota_reset_period: 'never' as const,
        enabled: true,
        sort_order: 0,
        allow_balance_pay: true,
        allow_wallet_overflow: true,
        max_purchase_per_user: 0,
        total_amount: 8000000,
        weekly_amount: 2000000,
      },
    ],
    subscriptions: [],
    orders: [],
    workspace: {
      mode: 'personal' as const,
      balance_usd: 20,
      paygo_balance_usd: 20,
      used_usd: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      requests: 0,
      api_key: null,
      team: null,
    },
    info: {
      enable_online_topup: true,
      enable_stripe_topup: false,
      pay_methods: [{ name: 'Alipay', type: 'alipay' }],
      min_topup: 50,
      stripe_min_topup: 1,
      amount_options: [50],
      discount: {},
      payment_compliance_confirmed: true,
    },
  }
  it('automatically confirms a missed callback and refreshes the credited order even when purchasing is closed', async () => {
    const pending = {
      ...quote,
      kind: 'paygo',
      price_cents: 300,
      payment_method: 'epay:alipay',
    }
    const credited = { ...pending, status: 'wallet_credited' }
    vi.mocked(api.billingOverview)
      .mockResolvedValueOnce({ ...storefront, orders: [pending] })
      .mockResolvedValue({ ...storefront, orders: [credited] })
    vi.mocked(api.reconcile).mockResolvedValue(credited)
    wrap(<BillingWallet />)
    expect(
      await screen.findByText('Credited to permanent balance')
    ).toBeVisible()
    expect(api.reconcile).toHaveBeenCalledWith(pending.trade_no)
    expect(
      screen.queryByRole('button', { name: 'Check payment' })
    ).not.toBeInTheDocument()
  })
  it('keeps an unconfirmed payment pending and lets the user retry after gateway failure', async () => {
    const pending = {
      ...quote,
      kind: 'paygo',
      price_cents: 500,
      payment_method: 'epay:alipay',
    }
    vi.mocked(api.billingOverview).mockResolvedValue({
      ...storefront,
      orders: [pending],
    })
    vi.mocked(api.reconcile).mockRejectedValue(new Error('Gateway unavailable'))
    wrap(<BillingWallet />)
    await waitFor(() =>
      expect(api.reconcile).toHaveBeenCalledWith(pending.trade_no)
    )
    expect(screen.getByText('Awaiting payment confirmation')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Check payment' })).toBeEnabled()
    expect(
      screen.queryByText('Credited to permanent balance')
    ).not.toBeInTheDocument()
  })
  it('keeps the catalog visible but closes every purchase action when the switch is off', async () => {
    vi.mocked(api.billingOverview).mockResolvedValue(storefront)
    wrap(<BillingWallet />)
    const buttons = await screen.findAllByRole('button', {
      name: 'Out of stock',
    })
    expect(buttons).toHaveLength(2)
    for (const button of buttons) expect(button).toBeDisabled()
    expect(screen.getByText('Lite')).toBeVisible()
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(
      screen.queryByText(/Purchases are not available yet/)
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /residual|converted time/i })
    ).not.toBeInTheDocument()
    expect(api.createCheckout).not.toHaveBeenCalled()
  })
  it('opens the amount selector only after clicking top-up when sales reopen', async () => {
    vi.mocked(api.billingOverview).mockResolvedValue({
      ...storefront,
      storefrontEnabled: true,
    })
    wrap(<BillingWallet />)
    const button = await screen.findByRole('button', { name: 'Top up' })
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    await userEvent.click(button)
    expect(screen.getByRole('dialog', { name: 'Top up' })).toBeVisible()
    expect(screen.getByRole('spinbutton')).toBeEnabled()
    expect(screen.queryByText(/CNY 50 minimum/)).not.toBeInTheDocument()
  })
  const current = {
    id: 19,
    user_id: 1,
    plan_id: 1,
    status: 'active',
    start_time: Date.now() / 1000 - 60,
    end_time: Date.now() / 1000 + 604800,
    amount_total: 8000000,
    amount_used: 20,
    weekly_amount: 2000000,
    weekly_used: 20,
    weekly_reset_at: Date.now() / 1000 + 604700,
    purchase_price_cents: 9900,
    purchase_title: 'Lite',
  }
  const teamPlan = {
    ...storefront.plans[0],
    id: 10,
    title: 'Team Standard',
    funding_scope: 'team' as const,
  }

  it('buys a team subscription independently of an active personal plan without a team PAYGO control', async () => {
    vi.mocked(api.billingOverview).mockResolvedValue({
      ...storefront,
      storefrontEnabled: true,
      currentSubscription: current,
      subscriptions: [{ subscription: current }],
      teamPlans: [teamPlan],
      ownedTeamId: 0,
    })
    vi.mocked(api.createCheckout).mockResolvedValue({
      ...quote,
      kind: 'subscription',
      funding_scope: 'team',
      old_subscription_id: 0,
    })
    wrap(<BillingWallet scope='team' />)
    await screen.findByText('Team Standard')
    expect(screen.queryByText('Pay-as-you-go balance')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Top up' })
    ).not.toBeInTheDocument()
    expect(screen.queryByText('Lite')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Subscribe' }))
    await waitFor(() =>
      expect(api.createCheckout).toHaveBeenCalledWith(
        expect.objectContaining({
          funding_scope: 'team',
          kind: 'subscription',
          plan_id: 10,
        })
      )
    )
    expect(
      await screen.findByRole('dialog', { name: 'Review purchase' })
    ).toHaveTextContent('Payment creates your team')
  })
  it('allows owners to upgrade only their own team subscription and keeps lower and current plans unavailable', async () => {
    const teamSubscription = {
      ...current,
      id: 23,
      plan_id: 10,
      workspace_team_id: 7,
    }
    vi.mocked(api.billingOverview).mockResolvedValue({
      ...storefront,
      storefrontEnabled: true,
      currentSubscription: { ...current, purchase_price_cents: 99900 },
      currentTeamSubscription: teamSubscription,
      ownedTeamId: 7,
      teamPlans: [
        teamPlan,
        { ...teamPlan, id: 11, title: 'Team Lower', price_amount: 49 },
        { ...teamPlan, id: 12, title: 'Team Higher', price_amount: 199 },
      ],
    })
    vi.mocked(api.createCheckout).mockResolvedValue({
      ...quote,
      funding_scope: 'team',
    })
    wrap(<BillingWallet scope='team' />)
    await screen.findByText('Team Higher')
    expect(screen.getByRole('button', { name: 'Current plan' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Unavailable' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Upgrade' }))
    await waitFor(() =>
      expect(api.createCheckout).toHaveBeenCalledWith(
        expect.objectContaining({
          funding_scope: 'team',
          kind: 'upgrade',
          old_subscription_id: 23,
          plan_id: 12,
        })
      )
    )
  })
  it('requires an owner with an expired subscription to dissolve the old team before buying another', async () => {
    vi.mocked(api.billingOverview).mockResolvedValue({
      ...storefront,
      storefrontEnabled: true,
      ownedTeamId: 7,
      currentTeamSubscription: null,
      teamPlans: [teamPlan],
    })
    wrap(<BillingWallet scope='team' />)
    expect(
      await screen.findByRole('button', { name: 'Subscribe' })
    ).toBeDisabled()
    expect(screen.getByText(/Dissolve the expired team/)).toBeVisible()
    expect(api.createCheckout).not.toHaveBeenCalled()
  })
  it('blocks a second personal purchase even when the existing subscription starts in the future', async () => {
    vi.mocked(api.billingOverview).mockResolvedValue({
      ...storefront,
      storefrontEnabled: true,
      currentSubscription: { ...current, start_time: Date.now() / 1000 + 60 },
    })
    wrap(<BillingWallet />)
    expect(
      await screen.findByRole('button', { name: 'Current plan' })
    ).toBeDisabled()
    expect(api.createCheckout).not.toHaveBeenCalled()
  })
  it('displays subscription conflicts and blocks purchases without hiding the personal balance', async () => {
    vi.mocked(api.billingOverview).mockResolvedValue({
      ...storefront,
      storefrontEnabled: true,
      subscriptionConflict: true,
      currentSubscription: null,
    })
    wrap(<BillingWallet />)
    expect(
      await screen.findByText(
        /The selected account has multiple active subscriptions/
      )
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Subscribe' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Top up' })).toBeEnabled()
  })
  it('keeps personal purchases available to members and scopes order history', async () => {
    vi.mocked(api.billingOverview).mockResolvedValue({
      ...storefront,
      storefrontEnabled: true,
      workspace: { ...storefront.workspace, mode: 'member' },
      orders: [
        { ...quote, id: 8, funding_scope: 'team', plan_title: 'Team history' },
        {
          ...quote,
          id: 9,
          funding_scope: 'personal',
          plan_title: 'Personal history',
        },
      ],
    })
    wrap(<BillingWallet />)
    expect(
      await screen.findByRole('button', { name: 'Subscribe' })
    ).toBeEnabled()
    expect(screen.getByText(/Personal history/)).toBeVisible()
    expect(screen.queryByText(/Team history/)).not.toBeInTheDocument()
    await userEvent.click(
      screen.getByRole('tab', { name: 'Team subscription' })
    )
    expect(screen.getByText(/Team history/)).toBeVisible()
    expect(screen.queryByText(/Personal history/)).not.toBeInTheDocument()
  })
  it('does not open a stale personal quote after the user switches to team billing', async () => {
    let resolve!: (value: api.BillingOrder) => void
    vi.mocked(api.billingOverview).mockResolvedValue({
      ...storefront,
      storefrontEnabled: true,
    })
    vi.mocked(api.createCheckout).mockReturnValue(
      new Promise((done) => {
        resolve = done
      })
    )
    wrap(<BillingWallet />)
    await userEvent.click(
      await screen.findByRole('button', { name: 'Subscribe' })
    )
    await userEvent.click(
      screen.getByRole('tab', { name: 'Team subscription' })
    )
    await act(async () => resolve({ ...quote, funding_scope: 'personal' }))
    expect(
      screen.queryByRole('dialog', { name: 'Review purchase' })
    ).not.toBeInTheDocument()
  })
  it('closes the previous account order when the signed-in user changes', async () => {
    vi.mocked(api.billingOverview).mockResolvedValue({
      ...storefront,
      storefrontEnabled: true,
    })
    vi.mocked(api.createCheckout).mockResolvedValue({
      ...quote,
      funding_scope: 'personal',
    })
    wrap(<BillingWallet />)
    await userEvent.click(
      await screen.findByRole('button', { name: 'Subscribe' })
    )
    expect(
      await screen.findByRole('dialog', { name: 'Review purchase' })
    ).toBeVisible()
    await act(async () =>
      useAuthStore
        .getState()
        .auth.setUser({ id: 2, username: 'another', role: 1 })
    )
    expect(
      await screen.findByRole('button', { name: 'Subscribe' })
    ).toBeEnabled()
    expect(
      screen.queryByRole('dialog', { name: 'Review purchase' })
    ).not.toBeInTheDocument()
  })
  it('discards a previous account delayed checkout result after switching accounts', async () => {
    let resolve!: (value: api.BillingOrder) => void
    vi.mocked(api.billingOverview).mockResolvedValue({
      ...storefront,
      storefrontEnabled: true,
    })
    vi.mocked(api.createCheckout).mockReturnValue(
      new Promise((done) => {
        resolve = done
      })
    )
    wrap(<BillingWallet />)
    await userEvent.click(
      await screen.findByRole('button', { name: 'Subscribe' })
    )
    await act(async () =>
      useAuthStore
        .getState()
        .auth.setUser({ id: 2, username: 'another', role: 1 })
    )
    expect(
      await screen.findByRole('button', { name: 'Subscribe' })
    ).toBeEnabled()
    await act(async () => resolve({ ...quote, funding_scope: 'personal' }))
    expect(
      screen.queryByRole('dialog', { name: 'Review purchase' })
    ).not.toBeInTheDocument()
  })
  it('lets a pending online order resume payment before its quote expires', async () => {
    vi.mocked(api.billingOverview).mockResolvedValue({
      storefrontEnabled: true,
      plans: [],
      subscriptions: [],
      orders: [
        {
          ...quote,
          kind: 'subscription',
          price_cents: 9900,
          payment_method: 'epay:alipay',
        },
      ],
      workspace: {
        mode: 'personal',
        balance_usd: 0,
        used_usd: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        requests: 0,
        api_key: null,
        team: null,
      },
      info: {
        enable_online_topup: true,
        enable_stripe_topup: false,
        pay_methods: [{ name: 'Alipay', type: 'alipay' }],
        min_topup: 50,
        stripe_min_topup: 1,
        amount_options: [50],
        discount: {},
        payment_compliance_confirmed: true,
      },
    })
    wrap(<BillingWallet />)
    expect(
      await screen.findByRole('button', { name: 'Check payment' })
    ).toBeVisible()
    await userEvent.click(
      await screen.findByRole('button', { name: 'Continue payment' })
    )
    expect(screen.getByRole('button', { name: 'Alipay' })).toBeVisible()
  })
  it('shows a purchased CNY subscription and partial week in CNY regardless of legacy display preferences', () => {
    const now = Date.now() / 1000
    const balance = (startTime: number) => (
      <SubscriptionBalance
        subscriptions={[
          {
            subscription: {
              id: 1,
              user_id: 1,
              plan_id: 1,
              purchase_price_cents: 9900,
              purchase_title: 'Lite',
              status: 'active',
              start_time: startTime,
              end_time: now + 302400,
              amount_total: 500000,
              amount_used: 0,
              weekly_amount: 1000000,
              weekly_limit_amount: 500000,
              weekly_used: 0,
              weekly_reset_at: now + 604800,
            },
          },
        ]}
      />
    )
    const view = render(balance(now))
    expect(screen.getByText(/Available now: (CN)?¥7.00/)).toBeVisible()
    expect(screen.getByRole('progressbar', { name: 'This week' })).toBeVisible()
    expect(screen.queryByText(/\$1/)).not.toBeInTheDocument()
    vi.useFakeTimers({ toFake: ['Date'] })
    try {
      vi.setSystemTime((now + 5) * 1000)
      view.rerender(balance(now + 5))
      expect(screen.getByText(/Available now: (CN)?¥7.00/)).toBeVisible()
      expect(screen.queryByText('Inactive')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
  it('stops billing queries after logout even while mounted', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    vi.mocked(api.billingOverview).mockRejectedValue(new Error('network'))
    render(
      <QueryClientProvider client={client}>
        <BillingWallet />
      </QueryClientProvider>
    )
    await screen.findByRole('button', { name: 'Retry' })
    vi.mocked(api.billingOverview).mockClear()
    await act(async () => {
      useAuthStore.getState().auth.reset()
    })
    await act(async () => {
      await client.invalidateQueries()
    })
    expect(api.billingOverview).not.toHaveBeenCalled()
    client.clear()
  })
  it('offers a retry when loading fails instead of showing empty balances', async () => {
    vi.mocked(api.billingOverview).mockRejectedValue(new Error('network'))
    wrap(<BillingWallet />)
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeEnabled()
    expect(screen.queryByText('¥0.00')).not.toBeInTheDocument()
  })
})
