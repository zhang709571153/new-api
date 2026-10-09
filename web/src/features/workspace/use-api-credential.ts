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
import { useCallback, useEffect, useRef, useState } from 'react'

import { useAuthStore } from '@/stores/auth-store'

import { revealMemberKey, revealPersonalKey, type WorkspaceScope } from './api'

// Visible credentials live only in this mounted view. They never enter a query
// cache, storage, URL or mutation result. Copy clicks use the already-loaded
// string so the browser's user activation is not lost to a network round trip.
export function useApiCredential({
  userId,
  keyId,
  keyRevision = '',
  scope = 'current',
  memberId,
}: {
  userId: number
  keyId: number
  keyRevision?: string
  scope?: WorkspaceScope
  memberId?: number
}) {
  const identity = `${userId}:${scope}:${keyId}:${memberId ?? ''}:${keyRevision}`
  const current = useRef(identity)
  current.current = identity
  const sequence = useRef({ request: 0 })
  const [state, setState] = useState({
    identity,
    secret: '',
    pending: Boolean(keyId),
    failed: false,
    visible: true,
  })
  const reload = useCallback(
    async (rotate = false) => {
      const request = ++sequence.current.request
      if (!keyId || useAuthStore.getState().auth.user?.id !== userId) {
        return false
      }
      setState({
        identity,
        secret: '',
        pending: true,
        failed: false,
        visible: true,
      })
      try {
        const secret = memberId
          ? await revealMemberKey(memberId, rotate)
          : await revealPersonalKey(rotate, scope)
        if (
          sequence.current.request !== request ||
          current.current !== identity ||
          useAuthStore.getState().auth.user?.id !== userId
        ) {
          return false
        }
        if (!/^sk-[A-Za-z0-9_-]{16,256}$/.test(secret)) {
          throw new Error('Invalid API key format')
        }
        setState({
          identity,
          secret,
          pending: false,
          failed: false,
          visible: true,
        })
        return true
      } catch {
        if (
          sequence.current.request === request &&
          current.current === identity &&
          useAuthStore.getState().auth.user?.id === userId
        ) {
          setState({
            identity,
            secret: '',
            pending: false,
            failed: true,
            visible: true,
          })
        }
        return false
      }
    },
    [identity, keyId, memberId, scope, userId]
  )
  useEffect(() => {
    const requests = sequence.current
    void reload()
    return () => {
      requests.request++
    }
  }, [reload])
  const valid =
    state.identity === identity &&
    useAuthStore.getState().auth.user?.id === userId
  return {
    secret: valid ? state.secret : '',
    pending: valid ? state.pending : Boolean(keyId),
    failed: valid && state.failed,
    visible: valid ? state.visible : true,
    setVisible: (visible: boolean) =>
      setState((previous) => ({ ...previous, visible })),
    reload,
  }
}

export type ApiCredential = ReturnType<typeof useApiCredential>
