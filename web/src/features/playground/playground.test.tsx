import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'

import { DEFAULT_CONFIG, DEFAULT_PARAMETER_ENABLED } from './constants'
import { Playground } from './index'
import {
  loadMessages,
  saveMessages,
  loadConfig,
  saveConfig,
} from './lib/storage/storage'
import { buildChatCompletionPayload } from './lib/streaming/payload-builder'
import type { Message } from './types'

const message: Message = {
  key: 'saved',
  from: 'user',
  versions: [{ id: 'v', content: 'private personal history' }],
  status: 'complete',
}
const send = vi.fn()
vi.mock('./hooks/use-chat-handler', () => ({
  useChatHandler: () => ({
    sendChat: send,
    stopGeneration: vi.fn(),
    isGenerating: false,
  }),
}))
vi.mock('./components/chat/playground-chat', () => ({
  PlaygroundChat: (props: { messages: Message[] }) => (
    <div>
      {props.messages.map((m) => (
        <p key={m.key}>{m.versions[0].content}</p>
      ))}
    </div>
  ),
}))
vi.mock('./components/input/playground-input', () => ({
  PlaygroundInput: (props: {
    disabled: boolean
    onSubmit: (s: string) => void
    modelValue: string
  }) => (
    <button
      type='button'
      disabled={props.disabled}
      onClick={() => props.onSubmit('hello')}
    >
      {props.modelValue}
    </button>
  ),
}))
let client: QueryClient
let balance = true
beforeEach(() => {
  localStorage.clear()
  balance = true
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  useAuthStore.getState().auth.setUser({ id: 21, username: 'member', role: 1 })
  vi.spyOn(api, 'get').mockImplementation(async (_url, config) => ({
    data: {
      success: true,
      data: {
        scope: config?.params.scope === 'team' ? 'team' : 'personal',
        scopes: ['personal', 'team'],
        group: 'default',
        models: ['gpt-6-luna'],
        key: { id: 1, masked_key: 'sk-****abc', status: 1 },
        can_chat: balance,
        balance_usd: balance ? 1 : 0,
        paygo_balance_usd: 1,
      },
    },
  }))
})
afterEach(() => {
  cleanup()
  client.clear()
  vi.restoreAllMocks()
  send.mockClear()
  useAuthStore.setState(useAuthStore.getInitialState(), true)
})
async function mount() {
  const router = createRouter({
    routeTree: createRootRoute({ component: Playground }),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  await router.load()
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  await screen.findByRole('heading', { name: 'AI chat' })
}
it('isolates legacy, account and workspace history and never requests a plaintext key', async () => {
  saveMessages([message])
  expect(loadMessages('21:personal')).toBeNull()
  saveMessages([message], '21:personal')
  expect(loadMessages('22:personal')).toBeNull()
  expect(loadMessages('21:team')).toBeNull()
  saveConfig({ model: 'old-model' }, '21:personal')
  expect(loadConfig('22:personal')).toEqual({})
  await mount()
  await screen.findByText('private personal history')
  expect(screen.getByRole('button', { name: 'gpt-6-luna' })).toBeEnabled()
  await userEvent.click(screen.getByRole('button', { name: 'Team balance' }))
  await waitFor(() =>
    expect(
      screen.queryByText('private personal history')
    ).not.toBeInTheDocument()
  )
  expect(api.get).not.toHaveBeenCalledWith(expect.stringContaining('reveal'))
  await userEvent.click(
    screen.getByRole('button', { name: 'Personal balance' })
  )
  await screen.findByText('private personal history')
})
it('disables sends when the selected key or available balance is unusable', async () => {
  balance = false
  await mount()
  expect(screen.getByRole('button', { name: 'gpt-6-luna' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Check your available balance and key'
  )
})
it('includes the chosen funding scope and omits unsupported parameters by default', () => {
  const payload = buildChatCompletionPayload(
    [message],
    { ...DEFAULT_CONFIG, scope: 'personal' },
    DEFAULT_PARAMETER_ENABLED
  )
  expect(payload).toMatchObject({
    scope: 'personal',
    model: 'gpt-6-luna',
    stream: true,
  })
  expect(payload).not.toHaveProperty('temperature')
  expect(payload).not.toHaveProperty('max_tokens')
})
