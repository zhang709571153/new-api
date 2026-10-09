import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { z } from 'zod'

import { ConfirmDialog } from '@/components/confirm-dialog'
import { StaticDataTable } from '@/components/data-table'
import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { formatCNY } from '@/features/billing/format'
import { toIntlLocale } from '@/i18n/languages'
import { useAuthStore } from '@/stores/auth-store'

import {
  getWelcomeAdmin,
  reviewWelcomeCredit,
  saveWelcomePolicy,
  type WelcomeCredit,
} from './api'

const reviewSchema = z.object({ reason: z.string().trim().min(3).max(200) })

export function WelcomeAdmin() {
  const { t, i18n } = useTranslation()
  const user = useAuthStore((s) => s.auth.user)
  const client = useQueryClient()
  const [selected, setSelected] = useState<WelcomeCredit | null>(null)
  const form = useForm<z.infer<typeof reviewSchema>>({
    resolver: zodResolver(reviewSchema),
    defaultValues: { reason: '' },
  })
  const query = useQuery({
    queryKey: ['welcome-admin', user?.id],
    queryFn: getWelcomeAdmin,
    enabled: user?.role === 100,
  })
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['welcome-admin'] })
  }
  const policy = useMutation({
    mutationFn: saveWelcomePolicy,
    onSuccess: refresh,
  })
  const review = useMutation({
    mutationFn: (reason: string) => {
      if (!selected) throw new Error('No claim selected')
      return reviewWelcomeCredit(selected.user_id, reason)
    },
    onSuccess: () => {
      setSelected(null)
      form.reset()
      refresh()
    },
  })
  if (user?.role !== 100) return null
  if (query.isPending) return <LoadingState />
  if (query.isError) return <ErrorState onRetry={() => void query.refetch()} />
  const data = query.data
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  return (
    <div className='space-y-4'>
      <h2 className='text-lg font-semibold'>{t('Welcome credit')}</h2>
      <p>
        {t('Gift amount')}: {formatCNY(data.policy.amount_cents / 100, locale)}
      </p>
      <p>
        {t('Daily gift budget')}:{' '}
        {formatCNY(data.policy.daily_limit_cents / 100, locale)}
      </p>
      <p>
        {t('Total gift budget')}:{' '}
        {formatCNY(data.policy.total_spent_cents / 100, locale)} /{' '}
        {formatCNY(data.policy.total_limit_cents / 100, locale)}
      </p>
      <Button
        disabled={policy.isPending}
        onClick={() =>
          policy.mutate({ ...data.policy, enabled: !data.policy.enabled })
        }
      >
        {data.policy.enabled
          ? t('Pause welcome credit')
          : t('Enable welcome credit')}
      </Button>
      <p className='text-muted-foreground text-sm'>
        {t(
          'Latest 200 claims. Manual review keeps the account and campaign limits.'
        )}
      </p>
      <StaticDataTable
        data={data.credits}
        getRowKey={(row) => row.user_id}
        columns={[
          { id: 'user', header: t('User ID'), cell: (row) => row.user_id },
          {
            id: 'status',
            header: t('Status'),
            cell: (row) =>
              row.status === 'granted' ? t('Granted') : t('Awaiting review'),
          },
          {
            id: 'reason',
            header: t('Reason'),
            cell: (row) => row.reason || '—',
          },
          {
            id: 'amount',
            header: t('Amount'),
            cell: (row) => formatCNY(row.amount_cents / 100, locale),
          },
          {
            id: 'review',
            header: t('Actions'),
            cell: (row) => (
              <Button
                variant='outline'
                size='sm'
                disabled={row.status !== 'pending'}
                onClick={() => {
                  form.reset()
                  setSelected(row)
                }}
              >
                {t('Review')}
              </Button>
            ),
          },
        ]}
      />
      <ConfirmDialog
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
        title={t('Review welcome credit')}
        desc={t('Grant once to this account after checking the claim.')}
        confirmText={t('Grant')}
        isLoading={review.isPending}
        handleConfirm={() =>
          void form.handleSubmit((values) => review.mutate(values.reason))()
        }
      >
        <Label htmlFor='welcome-review-reason'>{t('Reason')}</Label>
        <Input
          id='welcome-review-reason'
          {...form.register('reason')}
          aria-invalid={Boolean(form.formState.errors.reason)}
        />
        {form.formState.errors.reason && (
          <p role='alert'>
            {t('Enter a reason between 3 and 200 characters.')}
          </p>
        )}
      </ConfirmDialog>
    </div>
  )
}
