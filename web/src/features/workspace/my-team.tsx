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
import { Link } from '@tanstack/react-router'
import { Users } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ConfirmDialog } from '@/components/confirm-dialog'
import { CopyButton } from '@/components/copy-button'
import { DataTableRowActionMenu } from '@/components/data-table/core/row-action-menu'
import { Dialog } from '@/components/dialog'
import { EmptyState } from '@/components/empty-state'
import { ErrorState } from '@/components/error-state'
import { SectionPageLayout } from '@/components/layout'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { DropdownMenuItem } from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { NicknameForm } from '@/features/profile/components/nickname-form'
import { toIntlLocale } from '@/i18n/languages'
import { formatCurrencyFromUSD } from '@/lib/currency'
import { formatNumber } from '@/lib/format'
import { useAuthStore } from '@/stores/auth-store'

import {
  getWorkspaceTeam,
  getWorkspace,
  leaveWorkspaceTeam,
  inviteWorkspaceMember,
  updateWorkspaceMember,
  removeWorkspaceMember,
  type WorkspaceMember,
} from './api'
import { MemberKey } from './member-key'
import { MemberSettings } from './member-settings'
import { SubscriptionBalance } from './subscription-balance'
import { TeamMembersTable } from './team-members-table'
import { TeamNameForm } from './team-name-form'
import { TeamSetup } from './team-setup'
import { formatWorkspaceTokens } from './token-format'

export function MyTeam() {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const userId = useAuthStore((state) => state.auth.user?.id)
  const client = useQueryClient()
  const memberActionTrigger = useRef<HTMLButtonElement | null>(null)
  const teamHeading = useRef<HTMLHeadingElement>(null)
  const restoreMemberActionFocus = () =>
    memberActionTrigger.current?.isConnected
      ? memberActionTrigger.current
      : teamHeading.current
  const workspace = useQuery({
    queryKey: ['workspace', userId],
    queryFn: getWorkspace,
    enabled: Boolean(userId),
  })
  const team = useQuery({
    queryKey: ['workspace-team', userId],
    queryFn: getWorkspaceTeam,
    enabled: Boolean(userId),
    refetchInterval: 30000,
  })
  const [setup, setSetup] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const [removing, setRemoving] = useState<
    (WorkspaceMember & { teamId: number }) | null
  >(null)
  const [rotatingInvite, setRotatingInvite] = useState(false)
  const [editing, setEditing] = useState<WorkspaceMember | null>(null)
  const [nicknameMember, setNicknameMember] = useState<WorkspaceMember | null>(
    null
  )
  const [keyMember, setKeyMember] = useState<WorkspaceMember | null>(null)
  const [invitation, setInvitation] = useState<{
    code: string
    expires_at: number
  } | null>(null)
  const invite = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      setInvitation(await inviteWorkspaceMember())
    },
  })
  const rotateInvite = useMutation({
    gcTime: 0,
    mutationFn: () => inviteWorkspaceMember(true),
    onSuccess: (value) => {
      setInvitation(value)
      setRotatingInvite(false)
    },
  })
  const leave = useMutation({
    mutationFn: (teamId: number) => leaveWorkspaceTeam(teamId),
    onSuccess: async () => {
      setLeaving(false)
      setInvitation(null)
      setEditing(null)
      setNicknameMember(null)
      setKeyMember(null)
      client.removeQueries({ queryKey: ['workspace-team-trends'] })
      await client.invalidateQueries()
    },
  })
  const status = useMutation({
    mutationFn: (member: WorkspaceMember) =>
      updateWorkspaceMember(member.user_id, {
        status: member.status === 1 ? 2 : 1,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['workspace-team'] })
    },
  })
  const data = team.data
  const teamId = data?.team?.id ?? 0
  const remove = useMutation({
    mutationFn: (member: WorkspaceMember & { teamId: number }) =>
      removeWorkspaceMember(member.user_id, member.teamId),
    onSuccess: async () => {
      memberActionTrigger.current = null
      setRemoving(null)
      setKeyMember(null)
      setEditing(null)
      setNicknameMember(null)
      await client.invalidateQueries({ queryKey: ['workspace-team'] })
    },
  })
  const owner = data?.team?.owner_user_id === userId
  const canBuyTeamSubscription =
    !team.isPending &&
    !team.isError &&
    !workspace.isPending &&
    !workspace.isError &&
    !owner &&
    workspace.data?.mode !== 'owner'
  const canJoin = canBuyTeamSubscription && !data?.team
  const canLeave = Boolean(data?.team)
  useEffect(() => {
    if (!canJoin) setSetup(false)
    if (!canLeave) setLeaving(false)
  }, [canJoin, canLeave])
  const money = (value: number) =>
    formatCurrencyFromUSD(value, { locale, digitsSmall: 4 })
  return (
    <SectionPageLayout>
      <SectionPageLayout.Title>{t('My team')}</SectionPageLayout.Title>
      <SectionPageLayout.Content>
        {renaming && data?.team && owner && (
          <Dialog
            open
            title={t('Edit team name')}
            onOpenChange={(open) => {
              if (!open) setRenaming(false)
            }}
          >
            <TeamNameForm
              teamId={data.team.id}
              name={data.team.name}
              onSaved={() => setRenaming(false)}
            />
          </Dialog>
        )}
        <div className='mx-auto max-w-5xl min-w-0 space-y-5'>
          {team.isPending && <Skeleton className='h-48 w-full' />}
          {team.isError && <ErrorState onRetry={() => void team.refetch()} />}
          {canJoin && (
            <Card>
              <CardContent className='py-10'>
                <EmptyState
                  icon={Users}
                  title={t('Bring your team together')}
                  description={t(
                    'Your team is created after payment succeeds. You can also join with an invitation code.'
                  )}
                  action={
                    <div className='flex flex-wrap justify-center gap-2'>
                      <Button
                        render={
                          <Link to='/wallet' search={{ scope: 'team' }} />
                        }
                      >
                        {t('Buy team subscription')}
                      </Button>
                      <Button variant='outline' onClick={() => setSetup(true)}>
                        {t('Join team')}
                      </Button>
                    </div>
                  }
                />
              </CardContent>
            </Card>
          )}
          {!team.isPending && !team.isError && data?.team && (
            <>
              <div className='flex flex-wrap items-center justify-between gap-3'>
                <h2
                  ref={teamHeading}
                  tabIndex={-1}
                  className='text-2xl font-semibold'
                >
                  {data.team.name}
                </h2>
                <div className='flex flex-wrap gap-2'>
                  {owner && (
                    <Button variant='outline' onClick={() => setRenaming(true)}>
                      {t('Edit team name')}
                    </Button>
                  )}
                  {owner && (
                    <Button
                      variant='outline'
                      render={
                        <Link
                          to='/dashboard/$section'
                          params={{ section: 'models' }}
                          search={{ scope: 'team' }}
                        />
                      }
                    >
                      {t('Team usage trends')}
                    </Button>
                  )}
                  {owner && (
                    <Button
                      disabled={invite.isPending}
                      onClick={() => invite.mutate()}
                    >
                      {t('Invite member')}
                    </Button>
                  )}
                  {canBuyTeamSubscription && (
                    <Button
                      variant='outline'
                      render={<Link to='/wallet' search={{ scope: 'team' }} />}
                    >
                      {t('Buy team subscription')}
                    </Button>
                  )}
                  {canLeave && (
                    <Button variant='outline' onClick={() => setLeaving(true)}>
                      {t(owner ? 'Leave and dissolve team' : 'Leave team')}
                    </Button>
                  )}
                </div>
              </div>
              {canBuyTeamSubscription && (
                <p className='text-muted-foreground text-sm'>
                  {t(
                    'After payment succeeds, you will leave this team and become the owner of your new team.'
                  )}
                </p>
              )}
              <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-4'>
                {[
                  {
                    title: t('Shared balance'),
                    value: money(data.pool_balance_usd ?? 0),
                  },
                  {
                    title: t('Cumulative usage'),
                    value: money(data.used_usd ?? 0),
                  },
                  {
                    title: t('Tokens used'),
                    value: formatWorkspaceTokens(
                      (data.prompt_tokens ?? 0) + (data.completion_tokens ?? 0),
                      locale
                    ),
                  },
                  {
                    title: t('Team members'),
                    value: formatNumber(data.members.length, locale),
                  },
                ].map((item) => (
                  <Card key={item.title}>
                    <CardHeader>
                      <CardTitle className='text-muted-foreground text-sm font-normal'>
                        {item.title}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className='text-2xl font-semibold tabular-nums'>
                      {item.value}
                    </CardContent>
                  </Card>
                ))}
              </div>
              {data.subscription_conflict ? (
                <p role='alert' className='text-destructive text-sm'>
                  {t(
                    'The selected account has multiple active subscriptions. Contact an administrator to resolve this before use.'
                  )}
                </p>
              ) : (
                <SubscriptionBalance subscriptions={data.subscriptions} />
              )}
              {!owner && workspace.data && (
                <p className='text-muted-foreground text-sm'>
                  {t(
                    'Your personal balance: {{balance}}. Joining a team does not transfer this balance.',
                    { balance: money(workspace.data.personal_balance_usd ?? 0) }
                  )}
                </p>
              )}
              <Card className='min-w-0 overflow-hidden'>
                <CardHeader>
                  <CardTitle>{t('Usage by member')}</CardTitle>
                </CardHeader>
                <CardContent className='min-w-0 p-0'>
                  <TeamMembersTable
                    members={data.members}
                    weeklyAllowance={data.team.funding_version === 1}
                    showStatus
                    balanceDigitsSmall={4}
                    renderActions={
                      owner
                        ? (member) => (
                            <DataTableRowActionMenu
                              ariaLabel={t('Actions')}
                              onOpenChange={(open, trigger) => {
                                if (open) memberActionTrigger.current = trigger
                              }}
                            >
                              <DropdownMenuItem
                                render={
                                  <Link
                                    to='/usage-logs/$section'
                                    params={{ section: 'common' }}
                                    search={{
                                      scope: 'team',
                                      member: member.user_id,
                                      type: ['2'],
                                    }}
                                  />
                                }
                              >
                                {t('View details')}
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                render={
                                  <Link
                                    to='/dashboard/$section'
                                    params={{ section: 'models' }}
                                    search={{
                                      scope: 'team',
                                      member: member.user_id,
                                    }}
                                  />
                                }
                              >
                                {t('View trends')}
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onClick={() => setNicknameMember(member)}
                              >
                                {t('Edit nickname')}
                              </DropdownMenuItem>
                              {!member.is_owner && (
                                <>
                                  <DropdownMenuItem
                                    onClick={() => setEditing(member)}
                                  >
                                    {data.team?.funding_version === 1
                                      ? t('Member weekly allowance')
                                      : t('Set allowance')}
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    disabled={status.isPending}
                                    onClick={() => status.mutate(member)}
                                  >
                                    {member.status === 1
                                      ? t('Pause')
                                      : t('Resume')}
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    variant='destructive'
                                    onClick={() => {
                                      remove.reset()
                                      setRemoving({ ...member, teamId })
                                    }}
                                  >
                                    {t('Remove member')}
                                  </DropdownMenuItem>
                                </>
                              )}
                              <DropdownMenuItem
                                onClick={() => setKeyMember(member)}
                              >
                                {t('API key')}
                              </DropdownMenuItem>
                            </DataTableRowActionMenu>
                          )
                        : undefined
                    }
                  />
                </CardContent>
              </Card>
            </>
          )}
          {(status.error || invite.error || rotateInvite.error) && (
            <p role='alert' className='text-destructive text-sm'>
              {(status.error || invite.error || rotateInvite.error)?.message}
            </p>
          )}
        </div>
        <ConfirmDialog
          open={Boolean(removing)}
          finalFocus={restoreMemberActionFocus}
          onOpenChange={(open) => {
            if (!open) setRemoving(null)
          }}
          title={t('Remove member')}
          desc={t(
            'Remove {{name}} from this team? Their team key will stop working. Personal funds and usage history are kept.',
            { name: removing?.display_name || removing?.username || '' }
          )}
          confirmText={t('Remove member')}
          destructive
          isLoading={remove.isPending}
          handleConfirm={() => {
            if (removing && data?.team) remove.mutate(removing)
          }}
        >
          {remove.error && (
            <p role='alert' className='text-destructive text-sm'>
              {remove.error.message}
            </p>
          )}
        </ConfirmDialog>
        {canLeave && (
          <ConfirmDialog
            open={leaving}
            onOpenChange={setLeaving}
            title={t(owner ? 'Leave and dissolve team' : 'Leave team')}
            desc={t(
              owner
                ? 'Everyone will leave this team. Member team keys and invitations will stop working. Personal balances, personal keys and usage history are kept.'
                : 'Your team key will stop working. Your personal balance, personal key and usage history are kept.'
            )}
            confirmText={t(owner ? 'Leave and dissolve team' : 'Leave team')}
            destructive
            isLoading={leave.isPending}
            handleConfirm={() => {
              if (data?.team && canLeave) {
                leave.mutate(data.team.id)
              }
            }}
          >
            {owner && (
              <p className='text-muted-foreground text-sm'>
                {t(
                  'Any remaining team subscription allowance will no longer be usable. No automatic refund or transfer to personal balance is made. Subscription and billing records are kept.'
                )}
              </p>
            )}
            {leave.error && (
              <p role='alert' className='text-destructive text-sm'>
                {leave.error.message}
              </p>
            )}
          </ConfirmDialog>
        )}
        <ConfirmDialog
          open={rotatingInvite}
          onOpenChange={setRotatingInvite}
          title={t('Replace invitation code')}
          desc={t(
            'The old invitation code will stop working. Existing members are not affected.'
          )}
          confirmText={t('Replace invitation code')}
          destructive
          isLoading={rotateInvite.isPending}
          handleConfirm={() => rotateInvite.mutate()}
        />
        {setup && canJoin && <TeamSetup onClose={() => setSetup(false)} />}
        {owner && editing && (
          <MemberSettings
            finalFocus={restoreMemberActionFocus}
            member={editing}
            weeklyAllowance={data?.team?.funding_version === 1}
            onClose={() => setEditing(null)}
          />
        )}
        {owner && nicknameMember && (
          <Dialog
            open
            onOpenChange={(open) => {
              if (!open) setNicknameMember(null)
            }}
            title={t('Edit nickname')}
            description={nicknameMember.username}
            finalFocus={restoreMemberActionFocus}
          >
            <NicknameForm
              userId={nicknameMember.user_id}
              nickname={nicknameMember.display_name || ''}
              onSave={(display_name) =>
                updateWorkspaceMember(nicknameMember.user_id, { display_name })
              }
              onSaved={() => setNicknameMember(null)}
            />
          </Dialog>
        )}
        {owner && keyMember && (
          <MemberKey
            member={keyMember}
            onClose={() => setKeyMember(null)}
            finalFocus={restoreMemberActionFocus}
          />
        )}
        {owner && invitation && (
          <Dialog
            open
            onOpenChange={(open) => {
              if (!open) setInvitation(null)
            }}
            title={t('Invite member')}
            description={t(
              'This code can be reused and stays valid until you replace it.'
            )}
          >
            <div className='space-y-4'>
              <div className='bg-muted flex min-w-0 items-center justify-between gap-2 rounded-lg p-3'>
                <code className='min-w-0 break-all'>{invitation.code}</code>
                <CopyButton
                  value={invitation.code}
                  aria-label={t('Copy invitation code')}
                />
              </div>
              <Button variant='outline' onClick={() => setRotatingInvite(true)}>
                {t('Replace invitation code')}
              </Button>
              <p className='text-muted-foreground text-sm'>
                {t('Set a limit after the member joins.')}
              </p>
            </div>
          </Dialog>
        )}
      </SectionPageLayout.Content>
    </SectionPageLayout>
  )
}
