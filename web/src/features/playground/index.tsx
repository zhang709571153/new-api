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
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ErrorState } from '@/components/error-state'
import { LoadingState } from '@/components/loading-state'
import { Button } from '@/components/ui/button'
import { toIntlLocale } from '@/i18n/languages'
import { formatCurrencyFromUSD } from '@/lib/currency'
import { useAuthStore } from '@/stores/auth-store'

import { PlaygroundChat } from './components/chat/playground-chat'
import { PlaygroundInput } from './components/input/playground-input'
import { DEFAULT_PARAMETER_ENABLED } from './constants'
import { getPlaygroundContext, type PlaygroundContext } from './context'
import {
  useChatHandler,
  usePlaygroundConversation,
  usePlaygroundState,
} from './hooks'

export function Playground() {
  const { t } = useTranslation()
  const userId = useAuthStore((s) => s.auth.user?.id) ?? 0
  const [scope, setScope] = useState('current')
  const context = useQuery({
    queryKey: ['playground-context', userId, scope],
    queryFn: () => getPlaygroundContext(scope),
    refetchInterval: 30000,
  })
  if (context.isPending) return <LoadingState />
  if (context.isError) {
    return (
      <ErrorState
        description={t('Check your key in My workspace, then try again.')}
        onRetry={() => void context.refetch()}
      />
    )
  }
  return (
    <PlaygroundSession
      key={`${userId}:${context.data.scope}`}
      userId={userId}
      context={context.data}
      onScopeChange={setScope}
    />
  )
}

function PlaygroundSession(props: {
  userId: number
  context: PlaygroundContext
  onScopeChange: (scope: string) => void
}) {
  const { t, i18n } = useTranslation()
  const client = useQueryClient()
  const data = props.context
  const {
    config: storedConfig,
    messages,
    isLoadingMessages,
    updateMessages,
    updateConfig,
    updateParameterEnabled,
    clearMessages,
  } = usePlaygroundState(`${props.userId}:${data.scope}`)
  const config = {
    ...storedConfig,
    scope: data.scope,
    group: data.group,
    stream: true,
    model: data.models.includes(storedConfig.model)
      ? storedConfig.model
      : (data.models.find((m) => m === 'gpt-6-luna') ?? data.models[0] ?? ''),
  }
  const parameterEnabled = DEFAULT_PARAMETER_ENABLED
  const models = data.models.map((model) => ({ label: model, value: model }))
  const groups = [{ label: data.group, value: data.group, ratio: 1 }]
  const canChat = data.can_chat && models.length > 0

  const { sendChat, stopGeneration, isGenerating } = useChatHandler({
    config,
    parameterEnabled,
    onMessageUpdate: updateMessages,
  })
  const wasGenerating = useRef(false)
  useEffect(() => {
    if (wasGenerating.current && !isGenerating) {
      void client.invalidateQueries({
        queryKey: ['playground-context', props.userId],
      })
      void client.invalidateQueries({ queryKey: ['workspace', props.userId] })
    }
    wasGenerating.current = isGenerating
  }, [client, isGenerating, props.userId])

  const {
    editingMessageKey,
    handleSendMessage,
    handleRegenerateMessage,
    handleEditMessage,
    handleEditOpenChange,
    applyEdit,
    handleDeleteMessage,
  } = usePlaygroundConversation({
    messages,
    updateMessages,
    sendChat,
  })

  const handleClearMessages = () => {
    handleEditOpenChange(false)
    clearMessages()
  }

  return (
    <div className='relative flex size-full min-h-0 flex-col overflow-hidden'>
      <div className='flex shrink-0 flex-wrap items-center gap-3 border-b px-4 py-3 text-sm'>
        <h1 className='font-semibold'>{t('AI chat')}</h1>
        {data.scopes.map((scope) => (
          <Button
            key={scope}
            size='sm'
            variant={scope === data.scope ? 'secondary' : 'ghost'}
            aria-pressed={scope === data.scope}
            disabled={isGenerating}
            onClick={() => props.onScopeChange(scope)}
          >
            {scope === 'team' ? t('Team balance') : t('Personal balance')}
          </Button>
        ))}
        <span>
          {t('Available balance')}:{' '}
          {formatCurrencyFromUSD(data.balance_usd, {
            locale: toIntlLocale(i18n.resolvedLanguage || i18n.language),
          })}
        </span>
        <span className='text-muted-foreground'>{data.key.masked_key}</span>
        <Button variant='outline' size='sm' render={<Link to='/wallet' />}>
          {t('Top up')}
        </Button>
      </div>
      <p className='text-muted-foreground px-4 py-2 text-xs'>
        {t(
          'Text chat uses your selected balance and key. History stays in this browser.'
        )}
      </p>
      {!canChat && (
        <p role='alert' className='px-4 py-2 text-sm'>
          {t('Check your available balance and key before sending.')}
        </p>
      )}
      {/* Full-width scroll container: scrolling works even over side whitespace */}
      <div className='flex min-h-0 flex-1 flex-col overflow-hidden'>
        <PlaygroundChat
          messages={messages}
          isLoadingMessages={isLoadingMessages}
          onRegenerateMessage={(message) => {
            if (canChat) handleRegenerateMessage(message)
          }}
          onEditMessage={handleEditMessage}
          onDeleteMessage={handleDeleteMessage}
          onSelectPrompt={(text) => {
            if (canChat) handleSendMessage(text)
          }}
          isGenerating={isGenerating}
          editingKey={editingMessageKey}
          onCancelEdit={handleEditOpenChange}
          onSaveEdit={(newContent) => applyEdit(newContent, false)}
          onSaveEditAndSubmit={(newContent) => {
            if (canChat) applyEdit(newContent, true)
          }}
        />
      </div>

      {/* Input area: center content and constrain to the same container width */}
      <div className='mx-auto w-full max-w-4xl'>
        <PlaygroundInput
          config={config}
          disabled={isGenerating || !canChat}
          groups={groups}
          groupValue={config.group}
          isGenerating={isGenerating}
          isModelLoading={false}
          modelValue={config.model}
          models={models}
          onGroupChange={(value) => updateConfig('group', value)}
          onConfigChange={updateConfig}
          onClearMessages={handleClearMessages}
          onModelChange={(value) => updateConfig('model', value)}
          onParameterEnabledChange={updateParameterEnabled}
          onStop={stopGeneration}
          onSubmit={handleSendMessage}
          parameterEnabled={parameterEnabled}
          hasMessages={messages.length > 0}
        />
      </div>
    </div>
  )
}
