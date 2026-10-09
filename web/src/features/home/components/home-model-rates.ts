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
import { splitBillingExprAndRequestRules } from '@/features/pricing/lib/billing-expr'
import { readTokenTierChain } from '@/features/pricing/lib/billing-expression/display'
import { compileBillingExpression } from '@/features/pricing/lib/billing-expression/parser'
import { evaluateBillingExpression } from '@/features/pricing/lib/billing-expression/runtime'
import { calculateTokenPrice } from '@/features/pricing/lib/price'
import type { PricingModel } from '@/features/pricing/types'

// The public estimate uses the same conversion as the existing pricing page.
// Unsupported pricing modes stay unavailable instead of presenting a false quote.
export function standardTokenRates(
  model: PricingModel | undefined,
  ratio: number | undefined,
  inputTokens = 0,
  outputTokens = 0
) {
  if (
    !model ||
    model.quota_type !== 0 ||
    !model.enable_groups.includes('default') ||
    typeof ratio !== 'number' ||
    !Number.isFinite(ratio) ||
    ratio < 0 ||
    !Number.isFinite(inputTokens) ||
    inputTokens < 0 ||
    !Number.isFinite(outputTokens) ||
    outputTokens < 0
  ) {
    return null
  }
  let rates: { input: number; output: number }
  if (model.billing_mode === 'tiered_expr') {
    // Standard-mode quotes exclude request surcharges, as the pricing page does.
    const { billingExpr } = splitBillingExprAndRequestRules(
      model.billing_expr || ''
    )
    const compiled = compileBillingExpression(billingExpr)
    if (compiled.status !== 'ready') return null
    const tiers = readTokenTierChain(compiled.ast)
    if (
      !tiers ||
      tiers.some(
        (tier) =>
          tier.billingUnit === 'request' ||
          tier.imageCount ||
          tier.conditionText
      )
    ) {
      return null
    }
    const result = evaluateBillingExpression(compiled, {
      tokens: {
        p: inputTokens,
        c: outputTokens,
        len: inputTokens,
        cr: 0,
        cc: 0,
        cc1h: 0,
        img: 0,
        img_cr: 0,
        img_o: 0,
        ai: 0,
        ao: 0,
      },
    })
    if (result.status !== 'success') return null
    const matches = tiers.filter((tier) => tier.label === result.matchedTier)
    if (matches.length !== 1) return null
    const prices = matches[0].prices
    if (typeof prices.p !== 'number' || typeof prices.c !== 'number') {
      return null
    }
    rates = { input: prices.p * ratio, output: prices.c * ratio }
  } else {
    if (
      model.billing_expr ||
      !Number.isFinite(model.model_ratio) ||
      model.model_ratio < 0 ||
      !Number.isFinite(model.completion_ratio) ||
      model.completion_ratio < 0
    ) {
      return null
    }
    rates = {
      input: calculateTokenPrice(model, 'input', ratio),
      output: calculateTokenPrice(model, 'output', ratio),
    }
  }
  return Number.isFinite(rates.input) && Number.isFinite(rates.output)
    ? rates
    : null
}
