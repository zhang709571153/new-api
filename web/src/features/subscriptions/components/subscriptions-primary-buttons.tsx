import { useMutation } from '@tanstack/react-query'
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
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ConfirmDialog } from '@/components/confirm-dialog'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { requireServerSuccess } from '@/lib/server-error-message'

import { useSubscriptions } from './subscriptions-provider'

export function SubscriptionsPrimaryButtons() {
  const { t } = useTranslation()
  const { setOpen, triggerRefresh } = useSubscriptions()
  const [initializeOpen, setInitializeOpen] = useState(false)
  const initialize = useMutation({
    mutationFn: async () =>
      requireServerSuccess(
        (await api.post('/api/subscription/admin/initialize-cny')).data
      ),
    onSuccess: () => {
      setInitializeOpen(false)
      triggerRefresh()
    },
  })
  return (
    <div className='flex gap-2'>
      <Button
        size='sm'
        variant='outline'
        onClick={() => setInitializeOpen(true)}
      >
        {t('Initialize CNY plans')}
      </Button>
      <ConfirmDialog
        open={initializeOpen}
        onOpenChange={setInitializeOpen}
        title={t('Initialize CNY plans')}
        desc={t(
          'Create the six standard plans from CNY 99 to CNY 2,599. Existing CNY plans will never be overwritten.'
        )}
        handleConfirm={() => initialize.mutate()}
        isLoading={initialize.isPending}
      />
      <Button size='sm' onClick={() => setOpen('create')}>
        <Plus className='h-4 w-4' />
        {t('Create Plan')}
      </Button>
    </div>
  )
}
