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
import { Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ConfirmDialog } from '@/components/confirm-dialog'
import { CopyButton } from '@/components/copy-button'
import { Dialog } from '@/components/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useAuthStore } from '@/stores/auth-store'

import type { WorkspaceMember } from './api'
import { useApiCredential } from './use-api-credential'

export function MemberKey(props: {
  member: WorkspaceMember
  onClose: () => void
  finalFocus?: React.ComponentProps<typeof Dialog>['finalFocus']
}) {
  const { t } = useTranslation()
  const userId = useAuthStore((state) => state.auth.user?.id) ?? 0
  const credential = useApiCredential({
    userId,
    keyId: props.member.user_id,
    memberId: props.member.user_id,
  })
  const [copyFailed, setCopyFailed] = useState(false)
  const [reset, setReset] = useState(false)
  return (
    <Dialog
      open
      finalFocus={props.finalFocus}
      onOpenChange={(open) => {
        if (!open && !credential.pending) props.onClose()
      }}
      title={t('Member API key')}
      description={props.member.display_name || props.member.username}
    >
      <div className='space-y-4'>
        <Input
          readOnly
          value={
            credential.visible && credential.secret
              ? credential.secret
              : 'sk-****'
          }
          type='text'
          aria-label={t('Member API key')}
          onFocus={(event) => event.currentTarget.select()}
        />
        <div className='flex flex-wrap gap-2'>
          <Button
            size='icon'
            variant='outline'
            disabled={!credential.secret}
            aria-label={credential.visible ? t('Hide key') : t('Show key')}
            onClick={() => credential.setVisible(!credential.visible)}
          >
            {credential.visible ? <EyeOff /> : <Eye />}
          </Button>
          <CopyButton
            value={credential.secret}
            disabled={!credential.secret || credential.pending}
            onCopyResult={(success) => setCopyFailed(!success)}
            size='default'
            variant='default'
            aria-label={t('Copy API key')}
          >
            {t('Copy API key')}
          </CopyButton>
          <Button
            variant='outline'
            disabled={credential.pending}
            onClick={() => setReset(true)}
          >
            {t('Reset key')}
          </Button>
        </div>
        {credential.failed && (
          <p role='alert' className='text-destructive text-sm'>
            {t('Unable to load API key. Please retry.')}
            <Button variant='outline' onClick={() => void credential.reload()}>
              {t('Retry')}
            </Button>
          </p>
        )}
        {copyFailed && (
          <p role='alert' className='text-destructive text-sm'>
            {t(
              'Automatic copy was blocked. Select the visible text and copy it manually.'
            )}
          </p>
        )}
      </div>
      <ConfirmDialog
        open={reset}
        onOpenChange={setReset}
        title={t('Reset API key?')}
        desc={t(
          'The old key will stop working. Update connected apps with the new key.'
        )}
        destructive
        confirmText={t('Reset key')}
        isLoading={credential.pending}
        handleConfirm={async () => {
          if (await credential.reload(true)) setReset(false)
        }}
      />
    </Dialog>
  )
}
