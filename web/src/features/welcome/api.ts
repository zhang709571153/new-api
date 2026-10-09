import { api } from '@/lib/api'
import { requireServerSuccess } from '@/lib/server-error-message'

export interface WelcomeCredit {
  user_id: number
  status: 'pending' | 'granted' | 'skipped' | 'ineligible'
  source: string
  reason: string
  amount_cents: number
}
export interface WelcomePolicy {
  enabled: boolean
  amount_cents: number
  daily_limit_cents: number
  total_limit_cents: number
  ip_daily_limit: number
  daily_spent_cents: number
  total_spent_cents: number
}
interface Result<T> {
  success: boolean
  message?: string
  data: T
}
export async function getWelcomeOffer() {
  return requireServerSuccess(
    (
      await api.get<Result<{ enabled: boolean; amount_cents: number }>>(
        '/api/welcome'
      )
    ).data
  ).data
}
export async function claimWelcomeCredit() {
  await getWelcomeOffer()
  return requireServerSuccess(
    (await api.post<Result<WelcomeCredit>>('/api/workspace/welcome')).data
  ).data
}
export async function getWelcomeAdmin() {
  return requireServerSuccess(
    (
      await api.get<
        Result<{ policy: WelcomePolicy; credits: WelcomeCredit[] }>
      >('/api/welcome/admin')
    ).data
  ).data
}
export async function saveWelcomePolicy(policy: WelcomePolicy) {
  return requireServerSuccess(
    (await api.put('/api/welcome/admin', policy)).data
  )
}
export async function reviewWelcomeCredit(user_id: number, reason: string) {
  return requireServerSuccess(
    (
      await api.post<Result<WelcomeCredit>>('/api/welcome/admin/review', {
        user_id,
        reason,
      })
    ).data
  ).data
}
