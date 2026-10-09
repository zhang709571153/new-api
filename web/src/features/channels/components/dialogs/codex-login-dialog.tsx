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
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { CopyButton } from '@/components/copy-button'
import { Dialog } from '@/components/dialog'
import { Button } from '@/components/ui/button'
import { handleServerError } from '@/lib/handle-server-error'
import { createServerError } from '@/lib/server-error-message'

import {
  codexChannelLogin,
  getChannel,
  refreshCodexCredential,
  testChannel,
  updateChannelStatus,
  type CodexChannelLoginAttempt,
} from '../../api'
import type { Channel } from '../../types'

export function CodexLoginDialog(props: {
  channel: Channel
  onClose: () => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [attempt, setAttempt] = useState<CodexChannelLoginAttempt | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const current = useRef<CodexChannelLoginAttempt | null>(null)
  const alive = useRef(true)
  const automaticallySaved = useRef<string | null>(null)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      if (current.current) {
        void codexChannelLogin(
          props.channel.id,
          'cancel',
          current.current.id
        ).catch(() => undefined)
      }
    }
  }, [props.channel.id])

  useEffect(() => {
    if (!attempt || !['starting', 'pending'].includes(attempt.status)) return
    let stopped = false
    let timer: number
    let checking = false
    const check = async () => {
      if (stopped || checking) return
      window.clearTimeout(timer)
      if (Date.now() >= attempt.expires_at * 1000) {
        const expired = {
          ...attempt,
          status: 'failed' as const,
          error_code: 'login_expired',
        }
        current.current = expired
        setAttempt(expired)
        setMessage('')
        return
      }
      checking = true
      let finished = false
      try {
        const result = await codexChannelLogin(
          props.channel.id,
          'status',
          attempt.id
        )
        if (!stopped && alive.current && result) {
          current.current = result
          setAttempt(result)
          setMessage('')
          finished = !['starting', 'pending'].includes(result.status)
        }
      } catch {
        if (!stopped && alive.current) {
          setMessage(
            t('Connection interrupted. Retrying authorization status...')
          )
        }
      } finally {
        checking = false
        if (!stopped && !finished) {
          timer = window.setTimeout(() => void check(), 3000)
        }
      }
    }
    const focus = () => {
      void check()
    }
    timer = window.setTimeout(() => void check(), 3000)
    window.addEventListener('focus', focus)
    return () => {
      stopped = true
      window.clearTimeout(timer)
      window.removeEventListener('focus', focus)
    }
  }, [attempt, props.channel.id, t])

  async function begin() {
    setBusy(true)
    setMessage('')
    try {
      const result = await codexChannelLogin(props.channel.id, 'start')
      if (!alive.current) {
        if (result) {
          void codexChannelLogin(props.channel.id, 'cancel', result.id).catch(
            () => undefined
          )
        }
        return
      }
      current.current = result
      setAttempt(result)
    } catch (error) {
      if (alive.current) handleServerError(error)
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  const saveAndTest = useCallback(
    async (refresh: boolean) => {
      setBusy(true)
      setMessage('')
      try {
        if (refresh) {
          const result = await refreshCodexCredential(props.channel.id)
          if (!result.success) {
            throw createServerError(result, t('Refresh failed'))
          }
        } else {
          if (!current.current) return
          await codexChannelLogin(
            props.channel.id,
            'complete',
            current.current.id
          )
          current.current = null
          setAttempt(null)
        }
        if (!alive.current) return
        setMessage(t('Credential updated. Testing the channel...'))
        const result = await testChannel(props.channel.id, { stream: true })
        if (!alive.current) return
        if (!result.success) {
          setMessage(
            t(
              'Credential updated, but the channel test failed. Check channel details before enabling it.'
            )
          )
          return
        }
        setMessage(t('Credential updated and channel test passed.'))
        // Preserve deliberate manual disables. The operator explicitly requests
        // recovery when clicking this action on an automatically disabled channel.
        const latest = await getChannel(props.channel.id)
        if (!alive.current) return
        if (
          props.channel.status === 3 &&
          latest.success &&
          latest.data?.status === 3
        ) {
          const enabled = await updateChannelStatus(props.channel.id, 1)
          if (!enabled.success) {
            throw createServerError(enabled, t('Operation failed'))
          }
          setMessage(t('Credential updated. Channel tested and enabled.'))
        }
        void client.invalidateQueries({ queryKey: ['channels'] })
        void client.invalidateQueries({ queryKey: ['codex-channel-usage'] })
      } catch (error) {
        if (alive.current) handleServerError(error)
      } finally {
        if (alive.current) setBusy(false)
      }
    },
    [client, props.channel.id, props.channel.status, t]
  )

  useEffect(() => {
    if (
      attempt?.status !== 'ready' ||
      automaticallySaved.current === attempt.id
    ) {
      return
    }
    automaticallySaved.current = attempt.id
    void saveAndTest(false)
  }, [attempt?.id, attempt?.status, saveAndTest])

  let failure = t('Login failed or expired. Close this dialog and try again.')
  if (attempt?.error_code === 'account_mismatch') {
    failure = t(
      'The signed-in account does not match this channel. No credential was changed.'
    )
  }

  return (
    <Dialog
      open
      showCloseButton={false}
      onOpenChange={(open) => {
        if (!open && !busy) props.onClose()
      }}
      title={t('Update login credentials')}
      description={props.channel.name}
      footer={
        <Button variant='outline' disabled={busy} onClick={props.onClose}>
          {t('Close')}
        </Button>
      }
    >
      <div className='space-y-4'>
        <p className='text-muted-foreground text-sm'>
          {t(
            'Sign in again to automatically save credentials and test this channel.'
          )}
        </p>
        {!attempt && (
          <div className='flex flex-wrap gap-2'>
            <Button
              variant='outline'
              disabled={busy}
              onClick={() => void saveAndTest(true)}
            >
              {t('Refresh credential')}
            </Button>
            <Button disabled={busy} onClick={() => void begin()}>
              {t('Sign in again')}
            </Button>
          </div>
        )}
        {attempt?.email && (
          <p className='text-sm break-all'>
            {t('Sign in to this account:')} {attempt.email}
          </p>
        )}
        {attempt?.status === 'starting' && (
          <p role='status'>{t('Starting secure login...')}</p>
        )}
        {attempt?.status === 'pending' && (
          <div className='space-y-3'>
            <p className='text-sm'>
              {t(
                'Open the official sign-in page and enter this code. Enable device code login in ChatGPT security settings if requested. Keep this dialog open.'
              )}
            </p>
            <div className='flex items-center gap-2'>
              <code className='text-xl font-semibold tracking-wider'>
                {attempt.user_code}
              </code>
              <CopyButton
                value={attempt.user_code ?? ''}
                aria-label={t('Copy login code')}
              />
            </div>
            <Button
              render={
                <a
                  href={attempt.verification_url}
                  target='_blank'
                  rel='noopener noreferrer'
                />
              }
            >
              {t('Open official sign-in page')}
            </Button>
            <p role='status' className='text-sm'>
              {t('Waiting for authorization...')}
            </p>
          </div>
        )}
        {attempt?.status === 'ready' && (
          <div className='space-y-2'>
            <p role='status'>
              {t('Authorization complete. Saving credentials and testing...')}
            </p>
            <Button disabled={busy} onClick={() => void saveAndTest(false)}>
              {t('Update credential and test')}
            </Button>
          </div>
        )}
        {attempt?.status === 'failed' && (
          <p role='alert' className='text-destructive text-sm'>
            {failure}
          </p>
        )}
        {message && (
          <p role='status' className='text-sm'>
            {message}
          </p>
        )}
      </div>
    </Dialog>
  )
}
