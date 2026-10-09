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
import { BookOpen, Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { CopyButton } from '@/components/copy-button'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import type { ApiCredential } from '@/features/workspace/use-api-credential'
import { isDevelopmentSite } from '@/lib/development-site'

import {
  buildSetupCommand,
  setupCommandPreview,
  type SetupClient,
  type SetupPlatform,
} from './setup-command'
import { WorkBuddyGuide } from './workbuddy-guide'

export function CodexGuide({
  hasKey,
  credential,
}: {
  hasKey: boolean
  credential: ApiCredential
}) {
  const { t } = useTranslation()
  const [client, setClient] = useState<SetupClient>('codex')
  const [platform, setPlatform] = useState<SetupPlatform>('windows')
  const [copyFailed, setCopyFailed] = useState(false)
  if (isDevelopmentSite()) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('One-click setup')}</CardTitle>
          <CardDescription>
            {t(
              'Client setup is unavailable on this test site. Use the live site for your work devices.'
            )}
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }
  const command = credential.secret
    ? buildSetupCommand(platform, credential.secret, client)
    : ''
  return (
    <Card className='min-w-0'>
      <CardHeader>
        <div className='flex flex-wrap items-center justify-between gap-2'>
          <CardTitle>{t('One-click setup')}</CardTitle>
          <Button
            size='sm'
            variant='outline'
            role='link'
            render={<Link to='/docs' />}
          >
            <BookOpen aria-hidden='true' />
            {t('Documentation')}
          </Button>
        </div>
      </CardHeader>
      <CardContent className='flex min-w-0 flex-col gap-3'>
        <Tabs
          value={client}
          onValueChange={(value) => {
            setClient(value as SetupClient)
            setCopyFailed(false)
          }}
        >
          <TabsList
            className='h-auto max-w-full flex-wrap'
            aria-label={t('Application')}
          >
            <TabsTrigger value='codex'>Codex</TabsTrigger>
            <TabsTrigger value='workbuddy'>WorkBuddy</TabsTrigger>
          </TabsList>
        </Tabs>
        <Tabs
          value={platform}
          onValueChange={(value) => {
            setPlatform(value as SetupPlatform)
            setCopyFailed(false)
          }}
        >
          <TabsList className='h-auto max-w-full flex-wrap'>
            <TabsTrigger value='windows'>Windows</TabsTrigger>
            <TabsTrigger value='unix'>
              {client === 'workbuddy' ? 'macOS' : 'macOS / Linux'}
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <div
          role='group'
          aria-label={t('Setup command')}
          className='bg-muted flex min-w-0 flex-col overflow-hidden rounded-lg'
        >
          <Textarea
            readOnly
            rows={4}
            spellCheck={false}
            aria-label={t('Setup command text')}
            className='min-w-0 resize-y font-mono text-xs break-all'
            value={
              credential.visible && command
                ? command
                : setupCommandPreview(platform, client)
            }
            onFocus={(event) => event.currentTarget.select()}
          />
          <div className='flex flex-wrap justify-end gap-2 px-3 py-3'>
            <Button
              size='icon'
              variant='outline'
              disabled={!command}
              aria-label={
                credential.visible
                  ? t('Hide command key')
                  : t('Show command key')
              }
              onClick={() => credential.setVisible(!credential.visible)}
            >
              {credential.visible ? <EyeOff /> : <Eye />}
            </Button>
            <CopyButton
              key={`${client}-${platform}`}
              value={command}
              disabled={!command || credential.pending}
              size='sm'
              variant='outline'
              aria-label={t('Copy setup command')}
              onCopyResult={(success) => setCopyFailed(!success)}
            >
              {t('Copy command')}
            </CopyButton>
          </div>
        </div>
        {!hasKey && (
          <p className='text-muted-foreground text-sm'>
            {t('Create your API key first.')}
          </p>
        )}
        {copyFailed && (
          <p role='alert' className='text-destructive text-sm'>
            {t(
              'Automatic copy was blocked. Select the visible text and copy it manually.'
            )}
          </p>
        )}
        <div className='text-muted-foreground space-y-1 text-sm'>
          {client === 'workbuddy' ? (
            <WorkBuddyGuide />
          ) : (
            <p>
              {t(
                'Quit Codex completely before running setup. Reopen it after setup finishes.'
              )}
            </p>
          )}
          <p>
            {t('Set up once per computer. After changing keys, sign in again.')}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
