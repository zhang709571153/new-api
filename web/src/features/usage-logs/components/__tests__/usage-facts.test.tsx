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
import { render, screen, within } from '@testing-library/react'
import i18next from 'i18next'
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  test,
} from 'vitest'

import zh from '@/i18n/locales/zh.json'
import {
  useSystemConfigStore,
  DEFAULT_CURRENCY_CONFIG,
} from '@/stores/system-config-store'

import type { UsageLog } from '../../data/schema'
import { getUsageLabel } from '../../lib/format'
import type { LogOtherData } from '../../types'
import { DetailsDialog } from '../dialogs/details-dialog'

const i18nKeys = {
  'Log Details': 'Log Details',
  Consume: 'Consume',
  'Billing Details': 'Billing Details',
  'Billing Mode': 'Billing Mode',
  'Per-token': 'Per-token',
  'Dynamic Pricing': 'Dynamic Pricing',
  'Matched Tier': 'Matched Tier',
  'Group Ratio': 'Group Ratio',
  'Total Cost': 'Total Cost',
  'Usage parameters': 'Usage parameters',
}

function makeLog(other: LogOtherData): UsageLog {
  return {
    id: 1,
    user_id: 1,
    created_at: 1,
    type: 2,
    content: '',
    username: 'user',
    token_name: 'token',
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

function renderDetails(other: LogOtherData, promptTokens = 0): QueryClient {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const freshAt = Date.now() + 60_000
  queryClient.setQueryData(['status'], {}, { updatedAt: freshAt })
  queryClient.setQueryData(
    ['pricing'],
    { data: [], vendors: [] },
    { updatedAt: freshAt }
  )

  render(
    <QueryClientProvider client={queryClient}>
      <DetailsDialog
        log={{ ...makeLog(other), prompt_tokens: promptTokens }}
        isAdmin={false}
        isRoot={false}
        open
        onOpenChange={() => undefined}
      />
    </QueryClientProvider>
  )
  return queryClient
}

function rowValue(label: string): string | null {
  return screen.getByText(label).nextElementSibling?.textContent ?? null
}

describe('subscription funding breakdown', () => {
  beforeEach(() => {
    useSystemConfigStore
      .getState()
      .setConfig({ currency: { ...DEFAULT_CURRENCY_CONFIG } })
  })

  test.each([
    {
      consumed: 1000,
      wallet: 4000,
      expectedSubscription: '$0.002',
      expectedWallet: '$0.008',
    },
    {
      consumed: 0,
      wallet: 5000,
      expectedSubscription: '$0',
      expectedWallet: '$0.01',
    },
    {
      consumed: undefined,
      wallet: 5000,
      expectedSubscription: '$0',
      expectedWallet: '$0.01',
    },
  ])(
    'shows funding breakdown for subscription=$consumed and wallet=$wallet',
    ({ consumed, wallet, expectedSubscription, expectedWallet }) => {
      const client = renderDetails({
        billing_source: 'subscription',
        subscription_consumed: consumed,
        wallet_quota_deducted: wallet,
      })
      expect(rowValue('Subscription')).toBe(expectedSubscription)
      expect(rowValue('Wallet')).toBe(expectedWallet)
      expect(rowValue('Total Cost')).toBe('$0.01')
      client.clear()
    }
  )

  test('preserves the legacy funding breakdown when the wallet field is absent', () => {
    const client = renderDetails({
      billing_source: 'subscription',
      subscription_consumed: 5000,
    })
    expect(rowValue('Final Consumed')).toBe('$0.01')
    expect(screen.queryByText('Wallet')).not.toBeInTheDocument()
    client.clear()
  })
})

test('shows the recorded request and response models in log details', () => {
  const queryClient = renderDetails({
    response_model: {
      requested_model: 'requested-model',
      upstream_model: 'mapped-model',
      returned_model: 'unexpected-model',
    },
  })
  expect(screen.getByText('Response model: unexpected-model')).toBeVisible()
  expect(rowValue('Request Model')).toBe('requested-model')
  expect(rowValue('Upstream Model')).toBe('mapped-model')
  expect(screen.getByText('unexpected-model')).toBeVisible()
  queryClient.clear()
})

describe('usage facts billing details', () => {
  test('translates every supported reasoning effort into Chinese', () => {
    const translate = (key: string) =>
      (zh.translation as Record<string, string>)[key] ?? key
    for (const [value, expected] of Object.entries({
      none: '无',
      minimal: '最低',
      low: '低',
      medium: '中等',
      high: '高',
      xhigh: '极高',
      max: '最高',
      ultra: '超高',
      standard: '标准',
    })) {
      expect(getUsageLabel(value, translate)).toBe(expected)
    }
  })
  test('keeps model rates in USD but settled cost in the account currency', () => {
    useSystemConfigStore.getState().setConfig({
      currency: {
        ...DEFAULT_CURRENCY_CONFIG,
        quotaDisplayType: 'CNY',
        usdExchangeRate: 7,
      },
    })
    try {
      const client = renderDetails({
        model_ratio: 5,
        completion_ratio: 5,
        group_ratio: 0.1,
        cache_ratio: 0.1,
      })
      expect(screen.getByText('$1/million tokens')).toBeVisible()
      expect(screen.getByText('$5/million tokens')).toBeVisible()
      expect(rowValue('Total Cost')).toBe('¥0.07')
      client.clear()
    } finally {
      useSystemConfigStore.setState(
        useSystemConfigStore.getInitialState(),
        true
      )
    }
  })
  test('keeps explicitly reported zero input, output and cache visible', () => {
    const queryClient = renderDetails({ cache_tokens: 0 })
    expect(rowValue('Input Tokens')).toBe('0')
    expect(rowValue('Output Tokens')).toBe('0')
    expect(rowValue('Cache Read')).toBe('0')
    queryClient.clear()
  })

  test('shows the actually billed service tier without inferring a requested fast tier', () => {
    const queryClient = renderDetails({
      billed_service_tier: 'default',
      service_tier_reported: true,
    })
    expect(rowValue('Billed service tier')).toBe('Standard')
    queryClient.clear()
  })

  test('marks estimated usage and only shows reasoning counts when supplied', () => {
    const queryClient = renderDetails(
      { usage_estimated: true, reasoning_tokens: 24 },
      100
    )
    expect(rowValue('Usage source')).toBe('Estimated usage')
    expect(rowValue('Reasoning tokens (included in output)')).toBe('24')
    queryClient.clear()
  })

  test('does not invent a reasoning count for older records', () => {
    const queryClient = renderDetails({}, 100)
    expect(
      screen.queryByText('Reasoning tokens (included in output)')
    ).not.toBeInTheDocument()
    queryClient.clear()
  })

  test('shows the settled image count and a per-image price', () => {
    const queryClient = renderDetails({
      billing_mode: 'tiered_expr',
      expr_b64: btoa('tier("image", fixed(0.04)) * image_count'),
      billing_unit: 'request',
      fixed_price: 0.04,
      image_count: 2,
      matched_tier: 'image',
    })
    expect(
      screen.getByText('Billable image count').parentElement
    ).toHaveTextContent('2')
    expect(screen.getAllByText(/\/image/).length).toBeGreaterThan(0)
    queryClient.clear()
  })
  const queryClients: QueryClient[] = []

  test('shows actual billable image and cache tokens while retaining the aggregate cache count', () => {
    queryClients.push(
      renderDetails(
        {
          billing_mode: 'tiered_expr',
          expr_b64: btoa(
            'tier("standard", p * 5 + cr * 1.25 + img * 8 + img_cr * 2 + c * 30)'
          ),
          matched_tier: 'standard',
          cache_tokens: 300,
          image_cache_tokens: 200,
          billing_tokens: { p: 300, cr: 100, img: 400, img_cr: 200, c: 100 },
        },
        1000
      )
    )
    const billable = within(
      screen.getByRole('group', { name: 'Billable token breakdown' })
    )
    expect(
      billable.getByText('Image Cache').nextElementSibling
    ).toHaveTextContent('200')
    expect(
      billable.getByText('Cache Read').nextElementSibling
    ).toHaveTextContent('100')
    expect(billable.getByText('Image In').nextElementSibling).toHaveTextContent(
      '400'
    )
    expect(
      screen.getByText('Input Tokens').nextElementSibling
    ).toHaveTextContent('1,000')
    expect(
      screen
        .getAllByText('Cache Read')
        .some((label) => label.nextElementSibling?.textContent === '300')
    ).toBe(true)
  })

  beforeAll(() => {
    i18next.addResourceBundle('en', 'translation', i18nKeys)
  })

  afterEach(() => {
    for (const queryClient of queryClients) {
      queryClient.clear()
    }
    queryClients.length = 0
  })

  test('renders one raw-key row per usage fact before total cost', () => {
    const expression = 'tier("720P", u("seconds") * 5)'
    queryClients.push(
      renderDetails({
        group_ratio: 1,
        billing_mode: 'tiered_expr',
        expr_b64: Buffer.from(expression, 'utf8').toString('base64'),
        matched_tier: '720P',
        usage_facts: {
          resolution: '720P',
          seconds: 5,
        },
      })
    )

    expect(screen.getByText('Usage parameters')).toBeInTheDocument()
    expect(rowValue('resolution')).toBe('720P')
    expect(rowValue('seconds')).toBe('5')
    expect(rowValue('Billing Mode')).toBe('Dynamic Pricing')
    expect(rowValue('Matched Tier')).toBe('720P')

    const usageHeader = screen.getByText('Usage parameters')
    const totalCost = screen.getByText('Total Cost')
    expect(
      usageHeader.compareDocumentPosition(totalCost) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  test('does not render usage parameter rows when usage_facts is absent', () => {
    queryClients.push(
      renderDetails({
        group_ratio: 1,
      })
    )

    expect(screen.queryByText('Usage parameters')).toBeNull()
    expect(screen.queryByText('resolution')).toBeNull()
    expect(screen.queryByText('seconds')).toBeNull()
    expect(screen.getByText('Total Cost')).toBeInTheDocument()
  })

  test('does not render usage parameter rows when usage_facts is empty', () => {
    queryClients.push(
      renderDetails({
        group_ratio: 1,
        usage_facts: {},
      })
    )

    expect(screen.queryByText('Usage parameters')).toBeNull()
    expect(screen.queryByText('resolution')).toBeNull()
    expect(screen.queryByText('seconds')).toBeNull()
    expect(screen.getByText('Total Cost')).toBeInTheDocument()
  })
})
