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
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Eye, EyeOff, KeyRound } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ConfirmDialog } from '@/components/confirm-dialog'
import { CopyButton } from '@/components/copy-button'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'

import {
  createPersonalKey,
  type PersonalKey as KeyInfo,
  type WorkspaceScope,
} from './api'
import type { ApiCredential } from './use-api-credential'
import { ApiAccessSetupStatus } from './api-access-setup'

export function PersonalKeyCard({
  apiKey,
  scope,
  credential,
}: {
  apiKey: KeyInfo | null
  scope?: WorkspaceScope
  credential: ApiCredential
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [confirmReset, setConfirmReset] = useState(false)
  const [copyFailed, setCopyFailed] = useState(false)
  const create = useMutation({
    gcTime: 0,
    mutationFn: () => createPersonalKey(scope),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['workspace'] })
    },
  })
  const pending = credential.pending || create.isPending
  return (
    <Card className='min-w-0'>
      <CardHeader>
        <CardTitle className='flex items-center gap-2'>
          <KeyRound className='size-5' />
          {t('My API key')}
        </CardTitle>
        {apiKey?.id && (
          <CardAction>
            <Button
              variant='ghost'
              size='sm'
              disabled={pending}
              onClick={() => setConfirmReset(true)}
            >
              {t('Reset key')}
            </Button>
          </CardAction>
        )}
      </CardHeader>
      <CardContent className='space-y-4'>
        <ApiAccessSetupStatus scope={scope} />
        {apiKey?.id ? (
          <div className='flex min-w-0 items-center gap-2'>
            <Input
              readOnly
              type='text'
              value={
                credential.visible && credential.secret
                  ? credential.secret
                  : 'sk-****'
              }
              aria-label={t('My API key')}
              className='min-w-0 font-mono'
              onFocus={(event) => event.currentTarget.select()}
            />
            <Button
              size='icon'
              variant='outline'
              disabled={!credential.secret || pending}
              aria-label={credential.visible ? t('Hide key') : t('Show key')}
              onClick={() => credential.setVisible(!credential.visible)}
            >
              {credential.visible ? <EyeOff /> : <Eye />}
            </Button>
            <CopyButton
              value={credential.secret}
              disabled={!credential.secret || pending}
              variant='outline'
              aria-label={t('Copy API key')}
              onCopyResult={(success) => setCopyFailed(!success)}
            />
          </div>
        ) : (
          <Button disabled={pending} onClick={() => create.mutate()}>
            {t('Create my API key')}
          </Button>
        )}
        {credential.failed && (
          <div role='alert' className='text-destructive text-sm'>
            <p>{t('Unable to load API key. Please retry.')}</p>
            <Button
              variant='outline'
              size='sm'
              onClick={() => void credential.reload()}
            >
              {t('Retry')}
            </Button>
          </div>
        )}
        {copyFailed && (
          <p role='alert' className='text-destructive text-sm'>
            {t(
              'Automatic copy was blocked. Select the visible text and copy it manually.'
            )}
          </p>
        )}
        {create.error && (
          <p role='alert' className='text-destructive text-sm'>
            {create.error.message}
          </p>
        )}
      </CardContent>
      <ConfirmDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title={t('Reset API key?')}
        desc={t(
          'The old key will stop working. Update connected apps with the new key.'
        )}
        destructive
        confirmText={t('Reset key')}
        isLoading={pending}
        handleConfirm={async () => {
          if (await credential.reload(true)) {
            setConfirmReset(false)
            void client.invalidateQueries({ queryKey: ['workspace'] })
          }
        }}
      />
    </Card>
  )
}
