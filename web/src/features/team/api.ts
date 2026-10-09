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
import { api } from '@/lib/api'
import { formatCurrencyFromUSD } from '@/lib/currency'
import { requireServerSuccess } from '@/lib/server-error-message'

export interface TeamMember {
  id: number
  username: string
  display_name: string
  status: number
  quota: number
  used_quota: number
  request_count: number
  period_quota: number
  period_requests: number
  prompt_tokens: number
  completion_tokens: number
  last_request_at: number
}

export interface TeamOverview {
  members: TeamMember[]
  totals: {
    quota: number
    requests: number
    prompt_tokens: number
    completion_tokens: number
  }
  start_timestamp: number
  end_timestamp: number
  quota_per_unit: number
}

export interface NewTeamMember {
  user: { id: number; username: string; display_name: string; quota: number }
  api_key: string
  token_id: number
}

export async function getTeamOverview(days: number): Promise<TeamOverview> {
  const end = Math.floor(Date.now() / 1000)
  const response = await api.get<{
    success: boolean
    message?: string
    data: TeamOverview
  }>('/api/team/overview', {
    params: { start_timestamp: end - days * 86400, end_timestamp: end },
  })
  return requireServerSuccess(response.data).data
}

export async function createTeamMember(input: {
  username: string
  display_name: string
  quota: number
  models: string[]
}): Promise<NewTeamMember> {
  const response = await api.post<{
    success: boolean
    message?: string
    data: NewTeamMember
  }>('/api/team/members', input)
  return requireServerSuccess(response.data).data
}

export function formatPoints(
  quota: number,
  quotaPerUnit: number,
  locale?: Intl.LocalesArgument
): string {
  return formatCurrencyFromUSD(quota / quotaPerUnit, {
    locale,
    digitsSmall: 4,
    digitsLarge: 4,
  })
}
