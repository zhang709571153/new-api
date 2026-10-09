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

import { ErrorState } from '@/components/error-state'
import { SectionPageLayout } from '@/components/layout'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { AccountActionCard } from '@/features/security/components/account-action-card'
import { requireServerSuccess } from '@/lib/server-error-message'

import { updateUserProfile } from './api'
import { NicknameForm } from './components/nickname-form'
import { useProfile } from './hooks'

export function Profile() {
  const { t } = useTranslation()
  const { profile, loading, refreshProfile } = useProfile()
  return (
    <SectionPageLayout>
      <SectionPageLayout.Title>{t('Account')}</SectionPageLayout.Title>
      <SectionPageLayout.Content>
        <div className='mx-auto max-w-3xl space-y-5'>
          {loading && <Skeleton className='h-40 w-full' />}
          {!loading && !profile && <ErrorState onRetry={refreshProfile} />}
          {profile && (
            <>
              <Card>
                <CardHeader>
                  <CardTitle>
                    {profile.display_name || profile.username}
                  </CardTitle>
                </CardHeader>
                <CardContent className='space-y-4 text-sm'>
                  <p>
                    {t('Username')}: {profile.username}
                  </p>
                  {profile.email && (
                    <p>
                      {t('Email')}: {profile.email}
                    </p>
                  )}
                  <NicknameForm
                    key={profile.display_name}
                    userId={profile.id}
                    nickname={profile.display_name || ''}
                    onSave={async (display_name) =>
                      requireServerSuccess(
                        await updateUserProfile({ display_name })
                      )
                    }
                    onSaved={refreshProfile}
                  />
                </CardContent>
              </Card>
              <AccountActionCard
                action='password'
                username={profile.username}
                hasPassword={profile.has_password}
                onUpdate={refreshProfile}
              />
              <Button
                variant='outline'
                role='link'
                render={<Link to='/security' />}
              >
                {t('Security & Access')}
              </Button>
            </>
          )}
        </div>
      </SectionPageLayout.Content>
    </SectionPageLayout>
  )
}
