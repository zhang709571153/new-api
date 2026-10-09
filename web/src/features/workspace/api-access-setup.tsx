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
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { useAuthStore } from '@/stores/auth-store'

import { getApiAccessSetup, type WorkspaceScope } from './api'

export function ApiAccessSetupStatus({
  scope = 'current',
}: {
  scope?: WorkspaceScope
}) {
  const { t } = useTranslation()
  const userId = useAuthStore((state) => state.auth.user?.id)
  const query = useQuery({
    queryKey: ['workspace', userId, 'api-access-setup', scope],
    queryFn: () => getApiAccessSetup(scope),
    enabled: !!userId,
    gcTime: 0,
    retry: false,
    refetchInterval: (state) => {
      if (!state.state.data?.enabled) return false
      return state.state.data.status === 'ready' ? 60_000 : 10_000
    },
  })
  if (!userId || query.isPending) return null
  if (query.isError) {
    return (
      <div role='status' className='text-muted-foreground text-sm'>
        <p>{t('Unable to check API access setup.')}</p>
        <Button variant='ghost' size='sm' onClick={() => void query.refetch()}>
          {t('Refresh')}
        </Button>
      </div>
    )
  }
  if (!query.data?.enabled || query.data.status === 'disabled') return null
  if (query.data.status === 'ready') {
    return (
      <p role='status' className='text-muted-foreground text-sm'>
        {t('API access setup complete.')}
      </p>
    )
  }
  const needsHelp =
    query.data.status === 'error' ||
    query.data.reason === 'worker_unavailable'
  return (
    <p role='status' className='text-muted-foreground text-sm'>
      {needsHelp
        ? t('API access setup needs administrator attention.')
        : t('Preparing API access. This page will update automatically.')}
    </p>
  )
}
