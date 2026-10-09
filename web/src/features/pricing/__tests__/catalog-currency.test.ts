import { afterEach, expect, it } from 'vitest'

import {
  DEFAULT_CURRENCY_CONFIG,
  useSystemConfigStore,
} from '@/stores/system-config-store'

import {
  formatDynamicUnitPrice,
  formatTaskUsageUnitPrice,
} from '../lib/dynamic-price'
import { formatPrice, formatRequestPrice } from '../lib/price'
import type { PricingModel } from '../types'

const previous = useSystemConfigStore.getState().config.currency
afterEach(() =>
  useSystemConfigStore.getState().setConfig({ currency: previous })
)
it('catalog USD prices stay in USD under a CNY account without changing other displays', () => {
  useSystemConfigStore.getState().setConfig({
    currency: {
      ...DEFAULT_CURRENCY_CONFIG,
      quotaDisplayType: 'CNY',
      usdExchangeRate: 7,
    },
  })
  const model = {
    id: 1,
    model_name: 'example',
    quota_type: 0,
    model_ratio: 1,
    completion_ratio: 3,
    enable_groups: ['default'],
    group_ratio: { default: 1 },
  } as PricingModel
  expect(
    formatPrice(model, 'input', 'M', false, 1, 7, undefined, true, 'USD')
  ).toBe('$2')
  expect(formatPrice(model, 'input', 'M')).toBe('¥14')
  expect(
    formatRequestPrice(
      { ...model, quota_type: 1, model_price: 0.5 },
      false,
      1,
      7,
      undefined,
      true,
      'USD'
    )
  ).toBe('$0.5')
  expect(
    formatDynamicUnitPrice(2, { tokenUnit: 'M', displayCurrency: 'USD' })
  ).toBe('$2')
  expect(
    formatTaskUsageUnitPrice(0.5, { tokenUnit: 'M', displayCurrency: 'USD' })
  ).toBe('$0.5')
  expect(useSystemConfigStore.getState().config.currency.quotaDisplayType).toBe(
    'CNY'
  )
})
