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
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { PricingModel } from '@/features/pricing/types'
import { api } from '@/lib/api'

import { standardTokenRates } from '../components/home-model-rates'
import { SimpleHome } from '../components/simple-home'

const models: PricingModel[] = [
  {
    id: 1,
    model_name: 'gpt-6-luna',
    model_ratio: 1,
    completion_ratio: 1,
    quota_type: 0,
    enable_groups: ['default'],
  },
  {
    id: 2,
    model_name: 'gpt-6-sol',
    model_ratio: 3,
    completion_ratio: 1,
    quota_type: 0,
    enable_groups: ['default'],
  },
  {
    id: 3,
    model_name: 'gpt-6-astra',
    model_ratio: 6,
    completion_ratio: 1,
    quota_type: 0,
    enable_groups: ['default'],
  },
  {
    id: 4,
    model_name: 'gpt-5.6-sol',
    model_ratio: 2,
    completion_ratio: 2,
    quota_type: 0,
    enable_groups: ['default'],
  },
  {
    id: 5,
    model_name: 'gpt-5.6-terra',
    model_ratio: 1.5,
    completion_ratio: 3,
    quota_type: 0,
    enable_groups: ['default'],
  },
  {
    id: 6,
    model_name: 'gpt-5.6-luna',
    model_ratio: 0.25,
    completion_ratio: 4,
    quota_type: 0,
    enable_groups: ['default'],
  },
  {
    id: 7,
    model_name: 'gpt-5.5',
    model_ratio: 2.5,
    completion_ratio: 2,
    quota_type: 0,
    enable_groups: ['default'],
  },
]

const expressionModel: PricingModel = {
  ...models[1],
  billing_mode: 'tiered_expr',
  billing_expr:
    '(len > 272000 ? tier("long", p * 4 + c * 15 + cr * 0.4 + cc * 5) : tier("standard", p * 2 + c * 10 + cr * 0.2 + cc * 2.5)) * (param("service_tier") == "priority" || param("service_tier") == "fast" ? 2 : 1)',
}

let client: QueryClient
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  })
  client.setQueryData(['pricing'], {
    success: true,
    data: models,
    vendors: [],
    group_ratio: { default: 1 },
  })
})
afterEach(() => client.clear())
async function renderHome(
  enabled: boolean,
  authenticated = false,
  selfUse = false
) {
  client.setQueryData(['status'], {
    register_enabled: enabled,
    self_use_mode_enabled: selfUse,
  })
  const router = createRouter({
    routeTree: createRootRoute({
      component: () => <SimpleHome isAuthenticated={authenticated} />,
    }),
    history: createMemoryHistory({ initialEntries: ['/'] }),
  })
  await router.load()
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
describe('simple public onboarding', () => {
  it('offers registration and sign in when native registration is enabled', async () => {
    await renderHome(true)
    expect(screen.getByRole('link', { name: 'Get started' })).toHaveAttribute(
      'href',
      '/sign-up'
    )
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute(
      'href',
      '/sign-in'
    )
    expect(screen.queryByText(/40\+/)).not.toBeInTheDocument()
  })
  it('honors native registration disablement without linking users to a closed flow', async () => {
    await renderHome(false)
    expect(
      screen.queryByRole('link', { name: 'Get started' })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sign in' })).toBeVisible()
  })
  it('keeps the public hero centered without account previews or decorative images', async () => {
    await renderHome(true)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(
      'AI, within reach.'
    )
    expect(
      screen.getByRole('heading', { level: 1 }).closest('section')
    ).toHaveClass('text-center')
    expect(screen.getByRole('main').querySelector('img')).toBeNull()
    expect(screen.queryByText(/API key|balance|Codex/i)).not.toBeInTheDocument()
  })
  it('takes a signed-in visitor to their workspace without showing private data on the homepage', async () => {
    await renderHome(true, true)
    expect(screen.getByRole('link', { name: 'Top up' })).toHaveAttribute(
      'href',
      '/wallet'
    )
    expect(screen.getByRole('link', { name: 'My workspace' })).toHaveAttribute(
      'href',
      '/dashboard/overview'
    )
    expect(
      screen.queryByRole('link', { name: 'Get started' })
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/API key|balance/i)).not.toBeInTheDocument()
  })
  it('does not offer registration in self-use mode', async () => {
    await renderHome(true, false, true)
    expect(
      screen.queryByRole('link', { name: 'Get started' })
    ).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sign in' })).toBeVisible()
  })
  it('uses configured model rates and updates the local estimate without a paid request', async () => {
    const user = userEvent.setup()
    await renderHome(true)
    const estimate = screen.getByRole('status', { name: 'Estimated cost' })
    expect(estimate).toHaveTextContent('$0.24')
    await user.click(screen.getByRole('button', { name: /GPT-6 Sol/ }))
    expect(estimate).toHaveTextContent('$0.72')
    fireEvent.change(screen.getByRole('slider', { name: 'Input tokens' }), {
      target: { value: '200000' },
    })
    expect(estimate).toHaveTextContent('$1.32')
    expect(
      within(screen.getByRole('button', { name: /GPT-6 Astra/ })).getAllByText(
        '$12.00'
      )
    ).toHaveLength(2)
    fireEvent.change(screen.getByRole('slider', { name: 'Input tokens' }), {
      target: { value: '0' },
    })
    fireEvent.change(screen.getByRole('slider', { name: 'Output tokens' }), {
      target: { value: '0' },
    })
    expect(estimate).toHaveTextContent('$0.00')
  })
  it('shows all eight models in recommended order in a responsive grid with rates from the public pricing API', async () => {
    client.removeQueries({ queryKey: ['pricing'] })
    const get = vi.spyOn(api, 'get').mockResolvedValue({
      data: {
        success: true,
        data: models,
        vendors: [],
        group_ratio: { default: 1 },
      },
    })
    await renderHome(true)
    const catalog = screen.getByRole('group', { name: 'Choose a model' })
    expect(catalog).toHaveClass(
      'grid-cols-1',
      'sm:grid-cols-2',
      'xl:grid-cols-3',
      'gap-5'
    )
    expect(
      within(catalog)
        .getAllByRole('heading', { level: 3 })
        .map((heading) => heading.textContent)
    ).toEqual([
      'GPT-6.1 Sol',
      'GPT-6 Astra',
      'GPT-6 Sol',
      'GPT-6 Luna',
      'GPT-5.6 Sol',
      'GPT-5.6 Terra',
      'GPT-5.6 Luna',
      'GPT-5.5',
    ])
    await waitFor(() => expect(get).toHaveBeenCalledWith('/api/pricing'))
    for (const [name, input, output] of [
      ['GPT-5.6 Sol', '$4.00', '$8.00'],
      ['GPT-5.6 Terra', '$3.00', '$9.00'],
      ['GPT-5.6 Luna', '$0.50', '$2.00'],
      ['GPT-5.5', '$5.00', '$10.00'],
    ]) {
      const card = within(catalog).getByRole('button', {
        name: new RegExp(name.replaceAll('.', '\\.')),
      })
      expect(await within(card).findByText(input)).toBeVisible()
      expect(within(card).getByText(output)).toBeVisible()
    }
  })
  it('keeps an individual missing model rate unavailable and excludes it from the estimate', async () => {
    const user = userEvent.setup()
    client.setQueryData(['pricing'], {
      success: true,
      data: models.filter((model) => model.model_name !== 'gpt-5.6-terra'),
      vendors: [],
      group_ratio: { default: 1 },
    })
    await renderHome(true)
    const terra = screen.getByRole('button', { name: /GPT-5\.6 Terra/ })
    expect(
      within(terra).getByText('Rates are currently unavailable.')
    ).toBeVisible()
    await user.click(terra)
    expect(terra).toHaveAttribute('aria-pressed', 'true')
    expect(
      screen.getByRole('status', { name: 'Estimated cost' })
    ).toHaveTextContent('—')
    expect(
      within(screen.getByRole('button', { name: /GPT-5\.6 Luna/ })).getByText(
        '$0.50'
      )
    ).toBeVisible()
  })
  it('does not invent rates when the public catalog is unavailable', async () => {
    client.setQueryData(['pricing'], {
      success: true,
      data: [],
      vendors: [],
      group_ratio: {},
    })
    await renderHome(true)
    expect(
      screen.getAllByText('Rates are currently unavailable.')
    ).toHaveLength(8)
    expect(
      screen.getByRole('status', { name: 'Estimated cost' })
    ).toHaveTextContent('—')
  })
  it('preserves zero rates and refuses to estimate unsupported or invalid prices', () => {
    expect(standardTokenRates(models[0], 0)).toEqual({ input: 0, output: 0 })
    expect(
      standardTokenRates({ ...models[0], completion_ratio: 0 }, 2)
    ).toEqual({ input: 4, output: 0 })
    expect(standardTokenRates(models[0], undefined)).toBeNull()
    expect(
      standardTokenRates({ ...models[0], billing_mode: 'tiered_expr' }, 1)
    ).toBeNull()
    expect(standardTokenRates({ ...models[0], quota_type: 1 }, 1)).toBeNull()
    expect(standardTokenRates({ ...models[0], model_ratio: -1 }, 1)).toBeNull()
  })
  it.each([
    [0, 1, 5],
    [272000, 1, 5],
    [272001, 2, 7.5],
    [1000000, 2, 7.5],
  ])(
    'uses the configured standard-mode tier for %s input tokens without treating cache prices as ordinary input',
    (input, inputRate, outputRate) => {
      expect(standardTokenRates(expressionModel, 0.5, input, 20000)).toEqual({
        input: inputRate,
        output: outputRate,
      })
      expect(standardTokenRates(expressionModel, 0, input, 20000)).toEqual({
        input: 0,
        output: 0,
      })
    }
  )
  it('keeps expression card rates at short context and adjusts the estimate as the input slider enters long context', async () => {
    const user = userEvent.setup()
    client.setQueryData(['pricing'], {
      success: true,
      data: [expressionModel],
      vendors: [],
      group_ratio: { default: 0.5 },
    })
    await renderHome(true)
    const sol = screen.getByRole('button', { name: /GPT-6 Sol/ })
    await user.click(sol)
    const estimate = screen.getByRole('status', { name: 'Estimated cost' })
    expect(estimate).toHaveTextContent('$0.20')
    fireEvent.change(screen.getByRole('slider', { name: 'Input tokens' }), {
      target: { value: '280000' },
    })
    expect(estimate).toHaveTextContent('$0.71')
    fireEvent.change(screen.getByRole('slider', { name: 'Input tokens' }), {
      target: { value: '1000000' },
    })
    expect(estimate).toHaveTextContent('$2.15')
    expect(within(sol).getByText('$1.00')).toBeVisible()
    expect(within(sol).getByText('$5.00')).toBeVisible()
    expect(
      screen.getByText(
        'Standard-mode rates for short contexts, including group pricing.'
      )
    ).toBeVisible()
    expect(
      screen.getByText(
        'The estimate uses uncached input and adjusts for long contexts.'
      )
    ).toBeVisible()
    expect(
      screen.getByText(
        'Text estimates exclude image and tool charges. Cached input and priority modes use separate rates.'
      )
    ).toBeVisible()
    expect(
      screen.queryByText(
        'Cached input, priority modes, images, and tools are billed by actual usage.'
      )
    ).not.toBeInTheDocument()
  })
  it.each([
    'max(p * 2, 100)',
    'tier("custom", p * p + c * 10)',
    'param("unknown") == "special" ? tier("special", p * 4 + c * 10) : tier("base", p * 2 + c * 5)',
    'tier("cache only", cr * 0.2)',
    'tier("fixed", fixed(0.01))',
    'unsupported(p)',
  ])('does not extract a misleading unit rate from %s', (billing_expr) => {
    expect(
      standardTokenRates(
        { ...expressionModel, billing_expr },
        0.5,
        100000,
        20000
      )
    ).toBeNull()
  })
})
