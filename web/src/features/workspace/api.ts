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
import type { UserSubscriptionRecord } from '@/features/subscriptions/types'
import { api } from '@/lib/api'
import { requireServerSuccess } from '@/lib/server-error-message'

export type WorkspaceScope = 'personal' | 'team' | 'current'

export interface ApiAccessSetup {
  enabled: boolean
  status: 'pending' | 'error' | 'ready' | 'disabled'
  reason: string
  pending_count: number
  ready_count: number
  error_count: number
  worker_last_seen?: string
  scanner_last_success?: string
}

export async function getApiAccessSetup(
  scope: WorkspaceScope
): Promise<ApiAccessSetup> {
  return requireServerSuccess(
    (
      await api.get<Response<ApiAccessSetup>>(
        `/api/workspace/sub2api/status?scope=${scope}`
      )
    ).data
  ).data
}

export interface PersonalKey {
  id: number
  masked_key: string
  status: number
}
export interface TeamInfo {
  id: number
  name: string
  owner_user_id: number
  funding_version?: number
}
export interface WorkspaceInfo {
  subscription_conflict?: boolean
  mode: 'personal' | 'owner' | 'member'
  scope?: 'personal' | 'team'
  scopes?: ('personal' | 'team')[]
  balance_usd: number
  used_usd: number
  prompt_tokens: number
  completion_tokens: number
  requests: number
  api_key: PersonalKey | null
  team: TeamInfo | null
  personal_balance_usd?: number
  paygo_balance_usd?: number
  subscriptions?: UserSubscriptionRecord[]
}
export interface WorkspaceMember {
  weekly_used_usd?: number
  opening_used_usd?: number
  effective_weekly_allowance_usd?: number
  weekly_reset_at?: number
  user_id: number
  username: string
  display_name: string
  status: number
  is_owner: boolean
  balance_usd: number
  allowance_usd: number
  used_usd: number
  prompt_tokens: number
  completion_tokens: number
  requests: number
  masked_key: string
}
export interface WorkspaceTeam {
  has_unexpired_subscription?: boolean
  subscription_conflict?: boolean
  prompt_tokens?: number
  completion_tokens?: number
  used_usd?: number
  team: TeamInfo | null
  pool_balance_usd?: number
  members: WorkspaceMember[]
  subscriptions?: UserSubscriptionRecord[]
}
interface Response<T> {
  success: boolean
  message?: string
  data: T
}
export async function getWorkspace(): Promise<WorkspaceInfo> {
  return getScopedWorkspace('current')
}
export async function getScopedWorkspace(
  scope: WorkspaceScope
): Promise<WorkspaceInfo> {
  return requireServerSuccess(
    (
      await api.get<Response<WorkspaceInfo>>(
        scope === 'current' ? '/api/workspace' : `/api/workspace?scope=${scope}`
      )
    ).data
  ).data
}
export async function createPersonalKey(
  scope: WorkspaceScope = 'current'
): Promise<PersonalKey> {
  return requireServerSuccess(
    (
      await api.post<Response<PersonalKey>>(
        scope === 'current'
          ? '/api/workspace/key'
          : `/api/workspace/key?scope=${scope}`
      )
    ).data
  ).data
}
export async function revealPersonalKey(
  rotate = false,
  scope: WorkspaceScope = 'current'
): Promise<string> {
  return requireServerSuccess(
    (
      await api.post<Response<{ api_key: string }>>(
        `/api/workspace/key/${rotate ? 'rotate' : 'reveal'}${scope === 'current' ? '' : `?scope=${scope}`}`
      )
    ).data
  ).data.api_key
}
export async function getWorkspaceTeam(): Promise<WorkspaceTeam | null> {
  return requireServerSuccess(
    (await api.get<Response<WorkspaceTeam | null>>('/api/workspace/team')).data
  ).data
}
export async function joinWorkspaceTeam(code: string) {
  return requireServerSuccess(
    (await api.post('/api/workspace/team/join', { code })).data
  )
}
export async function inviteWorkspaceMember(rotate = false): Promise<{
  code: string
  expires_at: number
}> {
  return requireServerSuccess(
    (
      await api.post<Response<{ code: string; expires_at: number }>>(
        `/api/workspace/team/invites${rotate ? '/rotate' : ''}`
      )
    ).data
  ).data
}
export async function leaveWorkspaceTeam(teamId: number) {
  return requireServerSuccess(
    (await api.post('/api/workspace/team/leave', { team_id: teamId })).data
  )
}
export async function updateWorkspaceMember(
  id: number,
  input: { allowance_usd?: number; status?: number; display_name?: string },
  adminTeamId?: number
) {
  return requireServerSuccess(
    (
      await api.patch(
        adminTeamId
          ? `/api/team/workspaces/${adminTeamId}/members/${id}`
          : `/api/workspace/team/members/${id}`,
        input
      )
    ).data
  )
}
export async function removeWorkspaceMember(id: number, teamId: number) {
  return requireServerSuccess(
    (
      await api.delete(`/api/workspace/team/members/${id}`, {
        data: { team_id: teamId },
      })
    ).data
  )
}
export async function revealMemberKey(
  id: number,
  rotate = false
): Promise<string> {
  return requireServerSuccess(
    (
      await api.post<Response<{ api_key: string }>>(
        `/api/workspace/team/members/${id}/key/${rotate ? 'rotate' : 'reveal'}`
      )
    ).data
  ).data.api_key
}
