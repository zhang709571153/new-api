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
  PlanRecord,
  UserSubscriptionRecord,
} from '@/features/subscriptions/types'

import type { LogOtherData } from '../types'

export function getLogFundingAmounts(
  quota: number,
  other: LogOtherData | null
) {
  if (other?.billing_source !== 'subscription') return { total: quota }
  const wallet = other.wallet_quota_deducted
  if (wallet != null) {
    // The total quota remains the fallback for new records that omitted a
    // zero subscription deduction. Never count that total a second time.
    const subscription =
      other.subscription_consumed ?? Math.max(0, quota - wallet)
    return { total: subscription + wallet, subscription, wallet }
  }
  // Older subscription logs did not carry a wallet split; preserve their
  // explicit deduction (including zero) and original fallback semantics.
  const subscription = other.subscription_consumed ?? quota
  return { total: subscription, subscription }
}

interface BillingSourceVisibilityInput {
  isAdmin: boolean
  /** Admin view only: every system plan. */
  plans: PlanRecord[] | undefined
  /** Own-logs view only: active subscriptions returned by the self API. */
  subscriptions: UserSubscriptionRecord[] | undefined
}

/**
 * Every consume log carries `billing_source`, so the Wallet / Subscription
 * icon on the cost column is only a disambiguator. Admins see it when the
 * system has an enabled plan; own-logs views require an active subscription.
 */
export function shouldShowBillingSource(
  input: BillingSourceVisibilityInput
): boolean {
  if (input.isAdmin) {
    return (input.plans ?? []).some((record) => record.plan?.enabled === true)
  }
  return (input.subscriptions?.length ?? 0) > 0
}
