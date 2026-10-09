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
import { ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { EmptyState } from '@/components/empty-state'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

import type { Sub2APIStatus } from '../api'

export function Sub2APIManagement(props: { status: Sub2APIStatus }) {
  const { t } = useTranslation()
  let adminURL: URL | undefined
  try {
    const parsed = new URL(props.status.admin_url)
    const isLocal = ['localhost', '127.0.0.1', '[::1]'].includes(
      parsed.hostname
    )
    if (
      !parsed.username &&
      !parsed.password &&
      (parsed.protocol === 'https:' || (parsed.protocol === 'http:' && isLocal))
    ) {
      adminURL = parsed
    }
  } catch {
    // An incomplete server configuration must never become an unsafe link.
  }

  return (
    <EmptyState
      title={t('Sub2API channel management')}
      description={t(
        'Manage upstream accounts, pools and routing in Sub2API. Customer access, plans and billing remain in RealYu.'
      )}
      bordered
      action={
        <div className='flex flex-col items-center gap-4'>
          <Badge variant={props.status.configured ? 'secondary' : 'outline'}>
            {props.status.configured
              ? t('Service connection configured')
              : t('Service connection needs configuration')}
          </Badge>
          {adminURL ? (
            <Button
              role='link'
              render={
                <a
                  href={adminURL.href}
                  target='_blank'
                  rel='noopener noreferrer'
                />
              }
            >
              {t('Open Sub2API management')}
              <ExternalLink aria-hidden='true' data-icon='inline-end' />
            </Button>
          ) : (
            <p className='text-muted-foreground text-sm'>
              {t(
                'The administrator needs to configure the Sub2API management URL.'
              )}
            </p>
          )}
        </div>
      }
    />
  )
}
