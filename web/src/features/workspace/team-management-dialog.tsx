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
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ConfirmDialog } from '@/components/confirm-dialog'
import { DataTableRowActionMenu } from '@/components/data-table/core/row-action-menu'
import { Dialog } from '@/components/dialog'
import { ErrorState } from '@/components/error-state'
import { Button } from '@/components/ui/button'
import { DropdownMenuItem } from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { NicknameForm } from '@/features/profile/components/nickname-form'
import { hasPermission } from '@/lib/admin-permissions'
import { api } from '@/lib/api'
import {
  getServerErrorMessage,
  requireServerSuccess,
} from '@/lib/server-error-message'
import { useAuthStore } from '@/stores/auth-store'

import { AllowanceDialog } from './allowance-dialog'
import {
  updateWorkspaceMember,
  type WorkspaceMember,
  type WorkspaceTeam,
} from './api'
import { MemberSettings } from './member-settings'
import { SubscriptionSummary } from './subscription-summary'
import { TeamMembersTable } from './team-members-table'
import { TeamNameForm } from './team-name-form'

export function TeamManagementDialog(props: {
  teamId: number
  onClose: () => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const user = useAuthStore((state) => state.auth.user)
  const writable = hasPermission(user, 'team', 'write')
  const [editing, setEditing] = useState<WorkspaceMember | null>(null)
  const [nickname, setNickname] = useState<WorkspaceMember | null>(null)
  const [removing, setRemoving] = useState<WorkspaceMember | null>(null)
  const [subscription, setSubscription] = useState(false)
  const query = useQuery({
    queryKey: ['admin-workspace-team', user?.id, props.teamId],
    queryFn: async () =>
      requireServerSuccess(
        (
          await api.get<{ success: boolean; data: WorkspaceTeam }>(
            `/api/team/workspaces/${props.teamId}`
          )
        ).data
      ).data,
  })
  const refresh = () => {
    for (const key of [
      'admin-workspace-team',
      'supplier-teams',
      'workspace-team',
    ]) {
      void client.invalidateQueries({ queryKey: [key] })
    }
  }
  const status = useMutation({
    mutationFn: (member: WorkspaceMember) =>
      updateWorkspaceMember(
        member.user_id,
        { status: member.status === 1 ? 2 : 1 },
        props.teamId
      ),
    onSuccess: refresh,
  })
  const remove = useMutation({
    mutationFn: (member: WorkspaceMember) =>
      api
        .delete(
          `/api/team/workspaces/${props.teamId}/members/${member.user_id}`
        )
        .then((r) => requireServerSuccess(r.data)),
    onSuccess: () => {
      setRemoving(null)
      refresh()
    },
  })
  const data = query.data
  const team = data?.team
  const owner = data?.members.find((member) => member.is_owner)
  return (
    <>
      <Dialog
        open
        onOpenChange={(open) => {
          if (!open && !remove.isPending && !status.isPending) props.onClose()
        }}
        title={t('Manage team')}
        description={team?.name}
        contentClassName='sm:max-w-6xl'
        bodyClassName='space-y-5'
      >
        {query.isPending && <Skeleton className='h-48' />}
        {query.isError && <ErrorState onRetry={() => void query.refetch()} />}
        {data && team && (
          <>
            {writable && (
              <TeamNameForm
                key={team.id}
                teamId={team.id}
                name={team.name}
                admin
              />
            )}
            <div className='flex flex-wrap items-center justify-between gap-3'>
              <SubscriptionSummary subscriptions={data.subscriptions ?? []} />
              {owner && (user?.role ?? 0) >= 10 && (
                <Button variant='outline' onClick={() => setSubscription(true)}>
                  {t('Edit subscription')}
                </Button>
              )}
            </div>
            <TeamMembersTable
              members={data.members}
              weeklyAllowance={team.funding_version === 1}
              showStatus
              renderActions={
                writable
                  ? (member) => (
                      <DataTableRowActionMenu ariaLabel={t('Actions')}>
                        <DropdownMenuItem onClick={() => setNickname(member)}>
                          {t('Edit nickname')}
                        </DropdownMenuItem>
                        {!member.is_owner && (
                          <>
                            <DropdownMenuItem
                              onClick={() => setEditing(member)}
                            >
                              {team.funding_version === 1
                                ? t('Member weekly allowance')
                                : t('Set allowance')}
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              disabled={status.isPending}
                              onClick={() => status.mutate(member)}
                            >
                              {member.status === 1 ? t('Pause') : t('Resume')}
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              variant='destructive'
                              onClick={() => {
                                remove.reset()
                                setRemoving(member)
                              }}
                            >
                              {t('Remove member')}
                            </DropdownMenuItem>
                          </>
                        )}
                      </DataTableRowActionMenu>
                    )
                  : undefined
              }
            />
          </>
        )}
        {status.error && (
          <p role='alert' className='text-destructive text-sm'>
            {getServerErrorMessage(status.error)}
          </p>
        )}
      </Dialog>
      {editing && (
        <MemberSettings
          member={editing}
          weeklyAllowance={team?.funding_version === 1}
          adminTeamId={props.teamId}
          onClose={() => setEditing(null)}
        />
      )}
      {nickname && (
        <Dialog
          open
          title={t('Edit nickname')}
          onOpenChange={(open) => {
            if (!open) setNickname(null)
          }}
        >
          <NicknameForm
            userId={nickname.user_id}
            nickname={nickname.display_name}
            onSave={(name) =>
              updateWorkspaceMember(
                nickname.user_id,
                { display_name: name },
                props.teamId
              )
            }
            onSaved={() => {
              setNickname(null)
              refresh()
            }}
          />
        </Dialog>
      )}
      {subscription && owner && (
        <AllowanceDialog
          user={{
            id: owner.user_id,
            username: owner.display_name || owner.username,
          }}
          fixedScope={team?.funding_version === 1 ? 'team' : 'personal'}
          expectedTeamId={props.teamId}
          expectedFundingVersion={team?.funding_version}
          onClose={() => setSubscription(false)}
          onSuccess={refresh}
        />
      )}
      <ConfirmDialog
        open={Boolean(removing)}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setRemoving(null)
        }}
        title={t('Remove member')}
        desc={removing?.display_name || removing?.username || ''}
        confirmText={t('Remove member')}
        destructive
        isLoading={remove.isPending}
        handleConfirm={() => {
          if (removing && !remove.isPending) remove.mutate(removing)
        }}
      >
        {remove.error && (
          <p role='alert' className='text-destructive text-sm'>
            {getServerErrorMessage(remove.error)}
          </p>
        )}
      </ConfirmDialog>
    </>
  )
}
