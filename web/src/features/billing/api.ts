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
import type {
  SubscriptionPlan,
  SelfSubscriptionData,
  UserSubscription,
} from '@/features/subscriptions/types'
import { getTopupInfo } from '@/features/wallet/api'
import { getScopedWorkspace } from '@/features/workspace/api'
import { api } from '@/lib/api'
import { requireServerSuccess } from '@/lib/server-error-message'

interface Response<T> {
  success: boolean
  message?: string
  data: T
}
export interface BillingOrder {
  funding_scope?: 'personal' | 'team'
  workspace_team_id?: number
  id: number
  weekly_amount?: number
  allow_balance_pay?: boolean
  trade_no: string
  kind: string
  plan_id: number
  plan_title: string
  price_cents: number
  credited_quota: number
  old_subscription_id: number
  remaining_weeks: number
  residual_cents: number
  extension_seconds: number
  duration_seconds: number
  status: string
  payment_method: string
  expires_at: number
  created_at: number
  subscription_id: number
}
export interface CheckoutRequest {
  funding_scope?: 'personal' | 'team'
  kind: 'paygo' | 'subscription' | 'upgrade' | 'convert'
  plan_id?: number
  amount_cny?: number
  old_subscription_id?: number
  idempotency_key: string
}
interface Catalog {
  plans: SubscriptionPlan[]
  storefront_enabled: boolean
  team_plans?: SubscriptionPlan[]
  current_subscription?: UserSubscription | null
  current_team_subscription?: UserSubscription | null
  owned_team_id?: number
  subscription_conflict?: boolean
  team_subscription_conflict?: boolean
}
interface ScopeOverview {
  teamPlans?: SubscriptionPlan[]
  currentSubscription?: UserSubscription | null
  currentTeamSubscription?: UserSubscription | null
  ownedTeamId?: number
  subscriptionConflict?: boolean
  teamSubscriptionConflict?: boolean
}
export async function billingOverview() {
  const [catalog, subscriptions, workspace, info, orders] = await Promise.all([
    api.get<Response<Catalog>>('/api/billing/catalog'),
    api.get<Response<SelfSubscriptionData>>('/api/subscription/self'),
    getScopedWorkspace('personal'),
    getTopupInfo(),
    api.get<Response<BillingOrder[]>>('/api/billing/orders'),
  ])
  const catalogData = requireServerSuccess(catalog.data).data
  const scopeOverview: ScopeOverview = {
    teamPlans: catalogData.team_plans ?? [],
    currentSubscription: catalogData.current_subscription,
    currentTeamSubscription: catalogData.current_team_subscription,
    ownedTeamId: catalogData.owned_team_id ?? 0,
    subscriptionConflict: catalogData.subscription_conflict === true,
    teamSubscriptionConflict: catalogData.team_subscription_conflict === true,
  }
  return {
    ...scopeOverview,
    storefrontEnabled:
      requireServerSuccess(catalog.data).data.storefront_enabled === true,
    plans: requireServerSuccess(catalog.data).data.plans ?? [],
    subscriptions:
      requireServerSuccess(subscriptions.data).data.subscriptions ?? [],
    workspace,
    info: requireServerSuccess(info).data,
    orders: requireServerSuccess(orders.data).data ?? [],
  }
}
export async function createCheckout(request: CheckoutRequest) {
  return requireServerSuccess(
    (await api.post<Response<BillingOrder>>('/api/billing/orders', request))
      .data
  ).data
}
export async function payBalance(trade: string) {
  return requireServerSuccess(
    (
      await api.post<Response<BillingOrder>>(
        `/api/billing/orders/${trade}/balance`
      )
    ).data
  ).data
}
export async function reconcile(trade: string) {
  return requireServerSuccess(
    (
      await api.post<Response<BillingOrder>>(
        `/api/billing/orders/${trade}/reconcile`
      )
    ).data
  ).data
}
export async function payOnline(trade: string, method: string) {
  return requireServerSuccess(
    (
      await api.post<Response<{ url: string; params: Record<string, string> }>>(
        `/api/billing/orders/${trade}/epay`,
        { payment_method: method }
      )
    ).data
  ).data
}
