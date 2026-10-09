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
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterAll, afterEach, beforeEach, expect, test, vi } from 'vitest'

import en from '@/i18n/locales/en.json'
import {
  DEFAULT_CURRENCY_CONFIG,
  useSystemConfigStore,
} from '@/stores/system-config-store'

import type { UsageLog } from '../../data/schema'
import type { LogOtherData } from '../../types'
import { useCommonLogsColumns } from '../columns/common-logs-columns'

vi.mock('@lobehub/icons', () => ({}))
vi.hoisted(() => {
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
  })
})
afterAll(() => vi.unstubAllGlobals())

function makeLog(other: LogOtherData): UsageLog {
  return {
    id: 1,
    user_id: 1,
    created_at: 1,
    type: 2,
    content: other.group_ratio != null ? 'Group Ratio raw log' : '',
    username: 'user',
    token_name: 'Realyu API',
    model_name: 'wan2.5-i2v-preview',
    quota: 5000,
    prompt_tokens: 0,
    completion_tokens: 0,
    use_time: 0,
    is_stream: false,
    channel: 1,
    channel_name: '',
    token_id: 1,
    group: 'default',
    ip: '',
    other: JSON.stringify(other),
    request_id: 'req-1',
    upstream_request_id: '',
  }
}

function DetailPreview(props: { other: LogOtherData; isAdmin: boolean }) {
  const table = useReactTable({
    data: [makeLog(props.other)],
    columns: useCommonLogsColumns(props.isAdmin, false),
    getCoreRowModel: getCoreRowModel(),
  })
  const cell = table
    .getRowModel()
    .rows[0].getAllCells()
    .find((item) => item.column.id === 'content')
  if (!cell) throw new Error('The log must have a content column')
  return flexRender(cell.column.columnDef.cell, cell.getContext())
}
const plugin = {
  key: 'incho',
  name: 'Incho',
  version: '1.0.1',
  author: { name: 'Plugin maintainer' },
}
const previousConfig = useSystemConfigStore.getState().config
let client: QueryClient
const i18n = createInstance()
beforeEach(async () => {
  await i18n.init({
    lng: 'en',
    resources: { en },
    interpolation: { escapeValue: false },
  })
  useSystemConfigStore
    .getState()
    .setConfig({ currency: { ...DEFAULT_CURRENCY_CONFIG } })
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['status'], {}, { updatedAt: Date.now() + 60_000 })
  client.setQueryData(
    ['pricing'],
    { data: [], vendors: [] },
    { updatedAt: Date.now() + 60_000 }
  )
})
afterEach(() => {
  client.clear()
  useSystemConfigStore.getState().setConfig(previousConfig)
})
function renderPreview(other: LogOtherData, isAdmin = true) {
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <DetailPreview other={other} isAdmin={isAdmin} />
      </QueryClientProvider>
    </I18nextProvider>
  )
  return screen.getByRole('button', { name: /./ })
}

test('shows the current brand for historical default token names', async () => {
  fireEvent.click(renderPreview({ model_price: 0.25 }))
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText('RealYu API')).toBeVisible()
  expect(within(dialog).queryByText('Realyu API')).not.toBeInTheDocument()
})

test('shows CNY image fees without applying the text group multiplier again', async () => {
  fireEvent.click(
    renderPreview(
      {
        model_price: 0.25,
        group_ratio: 0.125,
        tool_surcharges: [
          {
            name: 'image_generation',
            tier: '2K',
            currency: 'CNY',
            count: 2,
            price: 42.857142857,
            unit_price_cny: 0.3,
            amount_cny: 0.6,
            usd_exchange_rate: 7,
          },
        ],
      },
      false
    )
  )
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText('Image Generation · 2K')).toBeVisible()
  expect(within(dialog).getByText(/2 × [¥￥]0\.3 = [¥￥]0\.6/)).toBeVisible()
})

test('keeps log details open when the parent refreshes with unchanged data', async () => {
  const other = { model_price: 0.25 }
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </I18nextProvider>
  )
  const { rerender } = render(<DetailPreview other={other} isAdmin />, {
    wrapper,
  })
  fireEvent.click(screen.getByRole('button', { name: 'Per-call · $0.25' }))
  expect(await screen.findByRole('dialog')).toBeVisible()
  rerender(<DetailPreview other={other} isAdmin />)
  expect(screen.getByRole('dialog')).toBeVisible()
})

test.each([
  {
    name: 'fixed expression zero price',
    other: {
      billing_mode: 'tiered_expr',
      billing_unit: 'request' as const,
      fixed_price: 0,
      matched_tier: 'free',
      expr_b64: btoa('tier("free", fixed(0))'),
    },
    expected: 'free · Per-call $0/request',
  },
  {
    name: 'fixed expression trace outside the display grammar',
    other: {
      billing_mode: 'tiered_expr',
      billing_unit: 'request' as const,
      fixed_price: 0.01,
      matched_tier: 'priority',
      expr_b64: btoa(
        'param("fast") == true ? tier("priority", fixed(0.01)) : tier("tokens", p * 2)'
      ),
    },
    expected: 'Priority · Per-call $0.01/request',
  },
  {
    name: 'per-call',
    other: { model_price: 0.25 },
    expected: 'Per-call · $0.25',
  },
  {
    name: 'standard',
    other: { model_ratio: 1, completion_ratio: 2 },
    expected: 'Standard · $2 / $4/million tokens',
  },
  {
    name: 'zero price fallback',
    other: { model_price: 0, group_ratio: 1 },
    expected: 'Group Ratio 1x',
  },
  { name: 'missing price fallback', other: {}, expected: '—' },
])('$name stays visible without a plugin counter', ({ other, expected }) => {
  const preview = renderPreview({
    ...other,
    admin_info: { task_plugin: plugin },
  })
  expect(preview.textContent).toBe(expected)
})

test('quota saturation remains first and only billing adds to the counter', () => {
  const preview = renderPreview({
    model_price: 0.25,
    admin_info: {
      task_plugin: plugin,
      quota_saturation: {
        op: 'round',
        kind: 'overflow',
        original: 3e9,
        clamped: 2147483647,
      },
    },
  })
  expect(preview.textContent).toBe('Quota clamped+1')
})

test('ordinary users see historical billed unit prices and no ratio in preview or details', async () => {
  const preview = renderPreview(
    {
      model_ratio: 5,
      completion_ratio: 5,
      group_ratio: 0.25,
      user_group_ratio: -1,
    },
    false
  )
  expect(preview.textContent).toBe('Standard · $2.5 / $12.5/million tokens')
  fireEvent.click(preview)
  const dialog = within(await screen.findByRole('dialog'))
  expect(dialog.queryByText('Group Ratio')).not.toBeInTheDocument()
  expect(dialog.queryByText('User Exclusive Ratio')).not.toBeInTheDocument()
  expect(dialog.queryByText('Group Ratio raw log')).not.toBeInTheDocument()
  expect(dialog.getByText('$2.5/million tokens')).toBeVisible()
  expect(dialog.getByText('$12.5/million tokens')).toBeVisible()
})

test('user-exclusive pricing applies to matched tier including cached input', async () => {
  const preview = renderPreview(
    {
      billing_mode: 'tiered_expr',
      expr_b64: btoa('tier("standard", p * 10 + c * 50 + cr * 1)'),
      matched_tier: 'standard',
      cache_tokens: 5,
      group_ratio: 0.25,
      user_group_ratio: 0.2,
    },
    false
  )
  expect(preview).toHaveTextContent('Standard · $2 / $10/million tokens')
  fireEvent.click(preview)
  const dialog = within(await screen.findByRole('dialog'))
  expect(dialog.getByText('$2/million tokens')).toBeVisible()
  expect(dialog.getByText('$10/million tokens')).toBeVisible()
  expect(dialog.getByText('$0.2/million tokens')).toBeVisible()
  expect(dialog.queryByText('$10.0000')).not.toBeInTheDocument()
  expect(dialog.queryByText('User Exclusive Ratio')).not.toBeInTheDocument()
})

test('administrators retain the original unit prices and recorded ratio', async () => {
  const preview = renderPreview({ model_ratio: 5, group_ratio: 0.2 })
  expect(preview.textContent).toBe('Standard · $10/million tokens')
  fireEvent.click(preview)
  const dialog = within(await screen.findByRole('dialog'))
  expect(dialog.getByText('$10/million tokens')).toBeVisible()
  expect(dialog.getByText('Group Ratio')).toBeVisible()
  expect(dialog.getByText('0.2000x')).toBeVisible()
})

test('an explicit zero user ratio stays free in the user preview', () => {
  const preview = renderPreview(
    { model_price: 0.15, group_ratio: 0.2, user_group_ratio: 0 },
    false
  )
  expect(preview.textContent).toBe('Per-call · $0')
})

test('matched request rules are folded into user model prices but not unrelated tool fees', async () => {
  const preview = renderPreview(
    {
      model_price: 0.5,
      group_ratio: 0.2,
      request_rules: [
        { cond: 'fast', multiplier: 2, matched: true },
        { cond: 'other', multiplier: 3, matched: false },
      ],
      image_generation_call: true,
      image_generation_call_price: 0.15,
    },
    false
  )
  expect(preview.textContent).toBe('Per-call · $0.2')
  fireEvent.click(preview)
  const dialog = within(await screen.findByRole('dialog'))
  expect(dialog.getByText('$0.03')).toBeVisible()
})

test('missing pricing metadata does not fall back to a ratio for ordinary users', () => {
  const preview = renderPreview({ group_ratio: 0.2 }, false)
  expect(preview.textContent).toBe('—')
})

test.each([true, false])(
  'plugin information in the opened dialog respects admin=%s',
  async (isAdmin) => {
    const preview = renderPreview(
      { model_price: 0.25, admin_info: { task_plugin: plugin } },
      isAdmin
    )
    expect(preview.textContent).toBe('Per-call · $0.25')
    fireEvent.click(preview)
    const dialog = within(await screen.findByRole('dialog'))
    if (isAdmin) {
      expect(dialog.getByText('Incho')).toBeVisible()
      expect(dialog.getByText('1.0.1')).toBeVisible()
      expect(dialog.getByText('Plugin maintainer')).toBeVisible()
    } else {
      expect(dialog.queryByText('Incho')).not.toBeInTheDocument()
      expect(dialog.queryByText('Plugin maintainer')).not.toBeInTheDocument()
    }
  }
)

test.each([
  {
    expression: 'tier("music", u("clips") * 0.25)',
    tier: 'music',
    expected: 'music · clips $0.25/unit',
  },
  {
    expression:
      'u("mode") == "pro" ? tier("pro", u("seconds") * 0.8) : tier("std", u("seconds") * 0.4)',
    tier: 'pro',
    expected: 'pro · seconds $0.8/second',
  },
  {
    expression: 'tier("tokens", u("tokens") * 9.8 / 1000000)',
    tier: 'tokens',
    expected: 'tokens · tokens $9.8/1M token',
  },
  {
    expression: 'tier("free", u("clips") * 0)',
    tier: 'free',
    expected: 'free · clips $0/unit',
  },
  {
    expression: 'tier("mixed", 0.1 + u("clips") * 0.25 + u("units") * 0.14)',
    tier: 'mixed',
    expected:
      'mixed · clips $0.25/unit · units $0.14/credit · Additional charge $0.1/request',
  },
])(
  'task expression $tier shows its recorded unit price',
  ({ expression, tier, expected }) => {
    client.setQueryData(['pricing'], {
      data: [
        {
          model_name: 'wan2.5-i2v-preview',
          billing_expr: 'tier("current", u("clips") * 99)',
          billing_usage_schema: {
            clips: { type: 'number', unit: 'count' },
            seconds: { type: 'number', unit: 'second' },
            tokens: { type: 'number', unit: 'token' },
            units: { type: 'number', unit: 'credit' },
            mode: { enum: ['pro', 'std'] },
          },
        },
      ],
      vendors: [],
    })
    const preview = renderPreview({
      is_task: true,
      billing_mode: 'tiered_expr',
      expr_b64: Buffer.from(expression).toString('base64'),
      matched_tier: tier,
      model_price: 0,
      admin_info: { task_plugin: plugin },
    })
    expect(preview.textContent).toBe(expected)
  }
)

test('task log prices use localized unit labels from pricing metadata', async () => {
  client.setQueryData(['pricing'], {
    data: [
      {
        model_name: 'wan2.5-i2v-preview',
        billing_usage_schema: {
          images: {
            type: 'number',
            unit: 'count',
            unitLabel: { en: 'image', zh: '张' },
          },
        },
      },
    ],
    vendors: [],
  })
  const preview = renderPreview({
    is_task: true,
    billing_mode: 'tiered_expr',
    expr_b64: btoa('tier("images", u("images") * 0.25)'),
    matched_tier: 'images',
  })
  expect(preview).toHaveTextContent('images · images $0.25/image')
  await act(() => i18n.changeLanguage('zh-CN'))
  expect(
    screen.getByRole('button', { name: /images · images/ })
  ).toHaveTextContent('images · images $0.25/张')
})

test('task log prices select the executing provider’s schema', () => {
  client.setQueryData(['pricing'], {
    data: [
      {
        model_name: 'wan2.5-i2v-preview',
        billing_usage_schema: { seconds: { type: 'number', unit: 'second' } },
        billing_plugin_variants: [
          {
            plugin_key: 'beta',
            plugin_name: 'Beta',
            billing_expr: 'tier("images", u("images") * 0.25)',
            billing_usage_schema: {
              images: {
                type: 'number',
                unit: 'count',
                unitLabel: { en: 'image' },
              },
            },
          },
        ],
      },
    ],
    vendors: [],
  })
  const preview = renderPreview({
    is_task: true,
    billing_mode: 'tiered_expr',
    expr_b64: btoa('tier("images", u("images") * 0.25)'),
    matched_tier: 'images',
    admin_info: {
      task_plugin: { key: 'beta', name: 'Beta', version: '1.0.0' },
    },
  })
  expect(preview).toHaveTextContent('images · images $0.25/image')
})

test.each(['missing schema', 'unsupported expression', 'unknown tier'])(
  'task pricing with %s shows an explicit unavailable summary',
  (scenario) => {
    if (scenario !== 'missing schema') {
      client.setQueryData(['pricing'], {
        data: [
          {
            model_name: 'wan2.5-i2v-preview',
            billing_usage_schema: { clips: { type: 'number', unit: 'count' } },
          },
        ],
        vendors: [],
      })
    }
    const expression =
      scenario === 'unsupported expression'
        ? 'tier("music", max(u("clips"), 1) * 0.25)'
        : 'tier("music", u("clips") * 0.25)'
    const preview = renderPreview({
      is_task: true,
      billing_mode: 'tiered_expr',
      expr_b64: Buffer.from(expression).toString('base64'),
      matched_tier: scenario === 'unknown tier' ? 'old' : 'music',
    })
    expect(preview.textContent).toBe('Dynamic Pricing · No matching results')
  }
)
