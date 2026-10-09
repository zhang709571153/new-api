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
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { PublicLayout } from '@/components/layout'
import { Button } from '@/components/ui/button'
import { PerformanceHealthPanel } from '@/features/dashboard/components/overview/performance-health-panel'

export function ServiceStatus() {
  const { t } = useTranslation()
  return (
    <PublicLayout showNotifications={false} showSidebarWhenAuthenticated>
      <div className='mx-auto max-w-5xl space-y-6 py-8'>
        <div className='flex flex-wrap items-start justify-between gap-4'>
          <div className='space-y-2'>
            <h1 className='text-3xl font-semibold tracking-tight'>
              {t('Service status')}
            </h1>
            <p className='text-muted-foreground max-w-2xl text-sm leading-6'>
              {t(
                'Success rates and response times from real requests. Updated every minute. No samples means no recent data, not an outage.'
              )}
            </p>
          </div>
          <Button
            variant='outline'
            render={
              <Link to='/dashboard/$section' params={{ section: 'overview' }} />
            }
          >
            {t('My workspace')}
          </Button>
        </div>
        <PerformanceHealthPanel showAllModels />
      </div>
    </PublicLayout>
  )
}
