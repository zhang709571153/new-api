import { api } from '@/lib/api'
import { requireServerSuccess } from '@/lib/server-error-message'

export interface PlaygroundContext {
  scope: string
  scopes: string[]
  group: string
  models: string[]
  key: { id: number; masked_key: string; status: number }
  balance_usd: number
  paygo_balance_usd: number
  can_chat: boolean
}
export async function getPlaygroundContext(scope: string) {
  return requireServerSuccess(
    (
      await api.get<{ success: boolean; data: PlaygroundContext }>(
        '/api/workspace/playground',
        { params: { scope } }
      )
    ).data
  ).data
}
