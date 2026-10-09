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
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { z } from 'zod'

import { ConfirmDialog } from '@/components/confirm-dialog'
import { Dialog } from '@/components/dialog'
import { ErrorState } from '@/components/error-state'
import { Button } from '@/components/ui/button'
import {
  Field,
  FieldGroup,
  FieldLabel,
  FieldError,
} from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  getUserSubscriptions,
  invalidateUserSubscription,
} from '@/features/subscriptions/api'
import type { UserSubscription } from '@/features/subscriptions/types'
import { api } from '@/lib/api'
import { getCurrencyLabel } from '@/lib/currency'
import { quotaUnitsToDollars, parseQuotaFromDollars } from '@/lib/format'
import {
  getServerErrorMessage,
  requireServerSuccess,
} from '@/lib/server-error-message'
import { useAuthStore } from '@/stores/auth-store'
import { useSystemConfigStore } from '@/stores/system-config-store'

import { SubscriptionBalance } from './subscription-balance'
import { SubscriptionExpiryForm } from './subscription-expiry-form'
import { WeeklyUsageForm } from './weekly-usage-form'

const schema = (maximum: number) =>
  z.object({
    weekly_amount: z.number().positive().max(maximum),
    name: z.string().max(40).optional(),
  })
type AllowanceInput = { weekly_usd: number; name?: string }
type AllowanceFormInput = z.infer<ReturnType<typeof schema>>

function AllowanceForm(props: {
  subscription?: UserSubscription
  onSubmit: (input: AllowanceInput) => void
  pending: boolean
  error: Error | null
  createTeam?: boolean
}) {
  const { t } = useTranslation()
  const quotaPerUnit = useSystemConfigStore(
    (state) => state.config.currency.quotaPerUnit
  )
  const maximum = quotaUnitsToDollars(1000000 * quotaPerUnit)
  const form = useForm<AllowanceFormInput>({
    resolver: zodResolver(schema(maximum)),
    defaultValues: {
      name: '',
      weekly_amount: quotaUnitsToDollars(
        props.subscription?.weekly_amount ?? 50 * quotaPerUnit
      ),
    },
  })
  let submitLabel = props.createTeam
    ? t('Activate team subscription')
    : t('Activate subscription')
  if (props.subscription) submitLabel = t('Save limits')
  return (
    <form
      className='space-y-4'
      onSubmit={form.handleSubmit((values) =>
        props.onSubmit({
          weekly_usd:
            parseQuotaFromDollars(values.weekly_amount) / quotaPerUnit,
          ...(props.createTeam ? { name: values.name?.trim() ?? '' } : {}),
        })
      )}
    >
      <FieldGroup>
        {props.createTeam && (
          <Field>
            <FieldLabel htmlFor='new-team-name'>
              {t('Team name (optional)')}
            </FieldLabel>
            <Input
              id='new-team-name'
              maxLength={40}
              disabled={props.pending}
              {...form.register('name')}
            />
            <p className='text-muted-foreground text-xs'>
              {t(
                'Leave blank to use the account name. Activating this subscription creates a team and makes this user its owner.'
              )}
            </p>
          </Field>
        )}
        {(['weekly_amount'] as const).map((name) => (
          <Field key={name} data-invalid={!!form.formState.errors[name]}>
            <FieldLabel htmlFor={name}>
              {t('Weekly allowance')} ({getCurrencyLabel()})
            </FieldLabel>
            <Input
              id={name}
              type='number'
              min='0.01'
              max={maximum}
              step='0.01'
              disabled={props.pending}
              aria-invalid={!!form.formState.errors[name]}
              {...form.register(name, { valueAsNumber: true })}
            />
            {form.formState.errors[name] && (
              <FieldError>
                {t(
                  'Enter a positive weekly allowance within the supported limit.'
                )}
              </FieldError>
            )}
          </Field>
        ))}
      </FieldGroup>
      <p className='text-muted-foreground text-sm'>
        {t(
          props.subscription
            ? 'Changing limits preserves usage, expiry and payment policy. No payment or renewal is triggered.'
            : 'Valid for 28 days. Allowance refreshes every 7 days without rollover. No automatic payment or renewal.'
        )}
      </p>
      {props.subscription && (
        <p className='text-muted-foreground text-xs'>
          {t(
            'Limits below recorded usage leave no remaining allowance until the applicable reset. Recorded usage is never reduced.'
          )}
        </p>
      )}
      {props.subscription && !props.subscription.weekly_amount && (
        <p className='text-muted-foreground text-xs'>
          {t(
            'This subscription had no weekly limit. New weekly limits apply to future usage; earlier usage remains in the total.'
          )}
        </p>
      )}
      {props.error && (
        <p role='alert' className='text-destructive text-sm'>
          {getServerErrorMessage(props.error)}
        </p>
      )}
      <Button type='submit' disabled={props.pending}>
        {submitLabel}
      </Button>
    </form>
  )
}

export function AllowanceDialog(props: {
  user: { id: number; username: string }
  fixedScope?: 'personal' | 'team'
  expectedTeamId?: number
  expectedFundingVersion?: number
  onClose: () => void
  onSuccess: () => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const viewerId = useAuthStore((state) => state.auth.user?.id)
  const isRoot = (useAuthStore((state) => state.auth.user?.role) ?? 0) >= 100
  const [openedAt] = useState(() => Date.now() / 1000)
  const [selectedScope, setScope] = useState<'personal' | 'team' | null>(null)
  const [confirmEnd, setConfirmEnd] = useState<UserSubscription | null>(null)
  const submitting = useRef(false)
  const [expiryPending, setExpiryPending] = useState(false)
  const query = useQuery({
    queryKey: ['user-allowance', props.user.id, viewerId],
    queryFn: async () =>
      requireServerSuccess(await getUserSubscriptions(props.user.id)),
  })
  const team = query.data?.team
  const independentTeam = team?.funding_version === 1 ? team : null
  const scope =
    props.fixedScope ?? selectedScope ?? (independentTeam ? 'team' : 'personal')
  const createTeam = scope === 'team' && !team && props.fixedScope !== 'team'
  const teamId = scope === 'team' && independentTeam ? independentTeam.id : 0
  const scopeMismatch =
    (props.expectedTeamId !== undefined &&
      (team?.id !== props.expectedTeamId ||
        (props.expectedFundingVersion !== undefined &&
          team?.funding_version !== props.expectedFundingVersion))) ||
    (props.fixedScope === 'team' && !independentTeam)
  const unexpiredSubscriptions =
    query.data?.data?.filter(
      (row) =>
        !createTeam &&
        (row.subscription.workspace_team_id ?? 0) === teamId &&
        row.subscription.status === 'active' &&
        row.subscription.end_time > openedAt
    ) ?? []
  const subscriptionConflict = unexpiredSubscriptions.length > 1
  const active = unexpiredSubscriptions[0]?.subscription
  let scopeTitle = t('Personal allowance: {{name}}', {
    name: props.user.username,
  })
  if (teamId) {
    scopeTitle = t('Team allowance: {{name}}', { name: independentTeam?.name })
  }
  if (createTeam) {
    scopeTitle = t('New team subscription for {{name}}', {
      name: props.user.username,
    })
  }
  const cancelAllowed =
    !!confirmEnd &&
    !scopeMismatch &&
    !subscriptionConflict &&
    active?.id === confirmEnd.id &&
    (confirmEnd.workspace_team_id ?? 0) === teamId &&
    (active.workspace_team_id ?? 0) === teamId
  useEffect(() => {
    if (confirmEnd && !cancelAllowed && !submitting.current) setConfirmEnd(null)
  }, [confirmEnd, cancelAllowed])
  const saved = () => {
    void client.invalidateQueries({ queryKey: ['workspace'] })
    void client.invalidateQueries({ queryKey: ['workspace-team'] })
    void client.invalidateQueries({ queryKey: ['billing'] })
    void client.invalidateQueries({ queryKey: ['supplier-teams'] })
    void client.invalidateQueries({ queryKey: ['team-overview'] })
    void client.invalidateQueries({
      queryKey: ['user-allowance', props.user.id, viewerId],
    })
    props.onSuccess()
    props.onClose()
  }
  const save = useMutation({
    mutationFn: async (
      input: AllowanceInput & {
        team_id: number
        subscription_id?: number
        funding_context?: { team_id: number; funding_version: number }
      }
    ) =>
      requireServerSuccess(
        (
          await api.put(
            `/api/subscription/admin/users/${props.user.id}/allowance`,
            input
          )
        ).data
      ),
    meta: { errorToast: false },
    onSuccess: saved,
    onSettled: () => {
      submitting.current = false
    },
  })
  const provision = useMutation({
    mutationFn: async (input: AllowanceInput) =>
      requireServerSuccess(
        (
          await api.post(
            `/api/subscription/admin/users/${props.user.id}/team-provision`,
            input
          )
        ).data
      ),
    meta: { errorToast: false },
    onSuccess: saved,
    onSettled: () => {
      submitting.current = false
    },
  })
  const editUsage = useMutation({
    mutationFn: async (input: {
      team_id: number
      subscription_id: number
      weekly_used_usd: number
      weekly_reset_at: number
      funding_context?: { team_id: number; funding_version: number }
    }) =>
      requireServerSuccess(
        (
          await api.put(
            `/api/subscription/admin/users/${props.user.id}/allowance`,
            input
          )
        ).data
      ),
    meta: { errorToast: false },
    onSuccess: saved,
    onSettled: () => {
      submitting.current = false
    },
  })
  const cancel = useMutation({
    mutationFn: async (subscription: UserSubscription) =>
      requireServerSuccess(
        await invalidateUserSubscription(
          subscription.id,
          {
            user_id: subscription.user_id,
            workspace_team_id: subscription.workspace_team_id ?? 0,
          },
          props.expectedTeamId !== undefined &&
            props.expectedFundingVersion !== undefined
            ? {
                team_id: props.expectedTeamId,
                funding_version: props.expectedFundingVersion,
              }
            : undefined
        )
      ),
    meta: { errorToast: false },
    onSuccess: saved,
    onSettled: () => {
      submitting.current = false
    },
  })
  const pending =
    expiryPending ||
    save.isPending ||
    provision.isPending ||
    editUsage.isPending ||
    cancel.isPending ||
    query.isFetching
  return (
    <>
      <Dialog
        open
        onOpenChange={(open) => {
          if (!open && !submitting.current) props.onClose()
        }}
        showCloseButton={!pending}
        title={t('Subscription')}
        description={props.user.username}
      >
        {query.isPending && <Skeleton className='h-48' />}
        {query.isError && <ErrorState onRetry={() => void query.refetch()} />}
        {query.isSuccess && scopeMismatch && (
          <p role='alert'>
            {t(
              'The team has changed. Close this dialog and refresh before editing its subscription.'
            )}
          </p>
        )}
        {query.isSuccess && !scopeMismatch && (
          <div className='space-y-5'>
            {(independentTeam || !team) && !props.fixedScope && (
              <Tabs
                value={scope}
                onValueChange={(value) => {
                  if (submitting.current) return
                  setScope(value as 'personal' | 'team')
                  save.reset()
                  provision.reset()
                  editUsage.reset()
                  cancel.reset()
                  setConfirmEnd(null)
                }}
              >
                <TabsList aria-label={t('Funding source')}>
                  <TabsTrigger value='personal' disabled={pending}>
                    {t('Personal')}
                  </TabsTrigger>
                  <TabsTrigger value='team' disabled={pending}>
                    {t('Team')}
                  </TabsTrigger>
                </TabsList>
              </Tabs>
            )}
            <p className='text-muted-foreground text-sm'>{scopeTitle}</p>
            {team?.funding_version === 0 && (
              <p className='text-muted-foreground text-sm'>
                {t(
                  'This legacy team still uses the owner’s personal funds until migration.'
                )}
              </p>
            )}
            {subscriptionConflict && (
              <p role='alert' className='text-destructive text-sm'>
                {t(
                  'The selected account has multiple active subscriptions. Contact an administrator to resolve this before use.'
                )}
              </p>
            )}
            {!subscriptionConflict && (
              <SubscriptionBalance
                subscriptions={active ? [{ subscription: active }] : []}
              />
            )}
            {!subscriptionConflict && (
              <AllowanceForm
                key={`${props.user.id}-${scope}-${teamId}-${active?.id ?? 'new'}`}
                subscription={active}
                createTeam={createTeam}
                pending={pending}
                error={createTeam ? provision.error : save.error}
                onSubmit={(input) => {
                  if (
                    submitting.current ||
                    pending ||
                    scopeMismatch ||
                    subscriptionConflict
                  ) {
                    return
                  }
                  submitting.current = true
                  if (createTeam) {
                    provision.mutate(input)
                    return
                  }
                  save.mutate({
                    ...input,
                    team_id: teamId,
                    ...(active ? { subscription_id: active.id } : {}),
                    ...(props.expectedTeamId !== undefined &&
                    props.expectedFundingVersion !== undefined
                      ? {
                          funding_context: {
                            team_id: props.expectedTeamId,
                            funding_version: props.expectedFundingVersion,
                          },
                        }
                      : {}),
                  })
                }}
              />
            )}
            {active && !subscriptionConflict && (
              <SubscriptionExpiryForm
                key={`expiry-${active.id}-${active.end_time}`}
                subscription={active}
                pending={pending}
                fundingContext={
                  props.expectedTeamId !== undefined &&
                  props.expectedFundingVersion !== undefined
                    ? {
                        team_id: props.expectedTeamId,
                        funding_version: props.expectedFundingVersion,
                      }
                    : undefined
                }
                onSaved={saved}
                onSubmitStart={() => {
                  if (
                    submitting.current ||
                    pending ||
                    scopeMismatch ||
                    subscriptionConflict
                  ) {
                    return false
                  }
                  submitting.current = true
                  setExpiryPending(true)
                  return true
                }}
                onSubmitEnd={() => {
                  submitting.current = false
                  setExpiryPending(false)
                }}
              />
            )}
            {isRoot && teamId > 0 && active && !subscriptionConflict && (
              <WeeklyUsageForm
                key={`weekly-usage-${active.id}-${active.weekly_reset_at ?? 0}`}
                subscription={active}
                pending={pending}
                error={editUsage.error}
                onSubmit={(weeklyUsedUSD) => {
                  if (
                    submitting.current ||
                    pending ||
                    scopeMismatch ||
                    subscriptionConflict
                  ) {
                    return
                  }
                  submitting.current = true
                  editUsage.mutate({
                    team_id: teamId,
                    subscription_id: active.id,
                    weekly_used_usd: weeklyUsedUSD,
                    weekly_reset_at: active.weekly_reset_at ?? 0,
                    funding_context: { team_id: teamId, funding_version: 1 },
                  })
                }}
              />
            )}
            {active && !subscriptionConflict && (
              <Button
                variant='outline'
                disabled={pending}
                onClick={() => {
                  cancel.reset()
                  setConfirmEnd(active)
                }}
              >
                {t('End subscription')}
              </Button>
            )}
            <p className='text-muted-foreground text-xs'>
              {t(
                'Only the selected allowance is changed. Personal and team funds remain separate.'
              )}
            </p>
          </div>
        )}
      </Dialog>
      <ConfirmDialog
        open={cancelAllowed}
        onOpenChange={(open) => {
          if (!open && !submitting.current) setConfirmEnd(null)
        }}
        title={t('End subscription?')}
        desc={t(
          'The selected subscription becomes unavailable immediately. Other subscriptions and personal balance are unchanged.'
        )}
        destructive
        confirmText={t('End subscription')}
        isLoading={pending}
        handleConfirm={() => {
          if (!confirmEnd || !cancelAllowed || submitting.current || pending) {
            return
          }
          submitting.current = true
          cancel.mutate(confirmEnd)
        }}
      >
        {cancel.error && (
          <p role='alert' className='text-destructive text-sm'>
            {getServerErrorMessage(cancel.error)}
          </p>
        )}
      </ConfirmDialog>
    </>
  )
}
