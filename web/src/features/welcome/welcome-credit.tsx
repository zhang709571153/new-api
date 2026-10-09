import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/auth-store'

import { claimWelcomeCredit } from './api'

// Mounted once per signed-in account; retries and React remounts are safe because
// the server commits the grant and claim record in one transaction.
export function WelcomeCredit() {
  const { t } = useTranslation()
  const userId = useAuthStore((s) => s.auth.user?.id)
  const client = useQueryClient()
  const claim = useMutation({
    mutationKey: ['welcome', userId],
    mutationFn: claimWelcomeCredit,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['workspace', userId] })
      void client.invalidateQueries({
        queryKey: ['playground-context', userId],
      })
    },
  })
  const { mutate } = claim
  useEffect(() => {
    if (userId) mutate()
  }, [userId, mutate])
  if (claim.isError) {
    return (
      <Button variant='outline' onClick={() => claim.mutate()}>
        {t('Retry welcome credit')}
      </Button>
    )
  }
  if (!claim.data || ['ineligible', 'granted'].includes(claim.data.status)) {
    return null
  }
  return (
    <p className='text-muted-foreground px-4 py-2 text-sm' role='status'>
      {t('Welcome credit is awaiting review. Contact support if needed.')}
    </p>
  )
}
