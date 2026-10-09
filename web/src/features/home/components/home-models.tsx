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
import {
  ArrowUpRight,
  ChartNoAxesColumn,
  Coins,
  Cpu,
  Sparkles,
  Users,
  Zap,
} from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { usePricingData } from '@/features/pricing/hooks/use-pricing-data'
import { toIntlLocale } from '@/i18n/languages'
import { compareModelNames } from '@/lib/model-catalog'

import { standardTokenRates } from './home-model-rates'

const MODEL_INFO = [
  {
    id: 'gpt-6.1-sol',
    name: 'GPT-6.1 Sol',
    icon: Sparkles,
    description: 'Coding, writing, and everyday work.',
  },
  {
    id: 'gpt-6-astra',
    name: 'GPT-6 Astra',
    icon: Sparkles,
    description: 'Complex questions and deeper thinking.',
  },
  {
    id: 'gpt-6-sol',
    name: 'GPT-6 Sol',
    icon: Cpu,
    description: 'Coding, writing, and everyday work.',
  },
  {
    id: 'gpt-6-luna',
    name: 'GPT-6 Luna',
    icon: Zap,
    description: 'Everyday ideas and lighter tasks.',
  },
  {
    id: 'gpt-5.6-sol',
    name: 'GPT-5.6 Sol',
    icon: Cpu,
    description: 'Coding, writing, and everyday work.',
  },
  {
    id: 'gpt-5.6-terra',
    name: 'GPT-5.6 Terra',
    icon: Cpu,
    description: 'Coding, writing, and everyday work.',
  },
  {
    id: 'gpt-5.6-luna',
    name: 'GPT-5.6 Luna',
    icon: Zap,
    description: 'Everyday ideas and lighter tasks.',
  },
  {
    id: 'gpt-5.5',
    name: 'GPT-5.5',
    icon: Sparkles,
    description: 'Complex questions and deeper thinking.',
  },
] as const

export function HomeModels() {
  const { t, i18n } = useTranslation()
  const { models, groupRatio, isLoading, error } = usePricingData()
  const [selected, setSelected] = useState<string>('gpt-6-luna')
  const [input, setInput] = useState(100000)
  const [output, setOutput] = useState(20000)
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const money = (value: number) =>
    new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 4,
    }).format(value)
  const count = (value: number) => new Intl.NumberFormat(locale).format(value)
  const catalog = [...MODEL_INFO]
    .sort((a, b) => compareModelNames(a.id, b.id))
    .map((info) => ({
      ...info,
      rates: error
        ? null
        : standardTokenRates(
            models.find((model) => model.model_name === info.id),
            groupRatio.default
          ),
    }))
  const active = catalog.find((model) => model.id === selected) ?? catalog[0]
  const estimateRates = error
    ? null
    : standardTokenRates(
        models.find((model) => model.model_name === active.id),
        groupRatio.default,
        input,
        output
      )
  const unavailable = isLoading
    ? t('Loading...')
    : t('Rates are currently unavailable.')

  return (
    <section
      className='realyu-model-section relative mx-auto max-w-6xl px-6 pb-24 sm:px-8'
      aria-labelledby='home-models-title'
    >
      <div className='mb-9 text-center'>
        <span className='realyu-section-kicker'>{t('GPT models')}</span>
        <h2
          id='home-models-title'
          className='mt-4 text-3xl font-semibold tracking-tight sm:text-4xl'
        >
          {t('The right model for your next idea.')}
        </h2>
        <p className='realyu-home-muted mt-4'>
          {t('One connection. A choice of GPT models.')}
        </p>
      </div>
      <div
        className='grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3'
        role='group'
        aria-label={t('Choose a model')}
      >
        {catalog.map((model) => (
          <Button
            key={model.id}
            type='button'
            variant='ghost'
            aria-pressed={selected === model.id}
            onClick={() => setSelected(model.id)}
            className='realyu-model-card group relative block h-auto min-w-0 rounded-3xl p-6 text-left font-normal whitespace-normal text-inherit outline-none hover:text-inherit focus-visible:ring-2 focus-visible:ring-sky-400'
          >
            <div className='flex items-center justify-between'>
              <model.icon
                className='realyu-model-symbol size-7'
                aria-hidden='true'
              />
              <ArrowUpRight
                className='size-5 opacity-40 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5'
                aria-hidden='true'
              />
            </div>
            <h3 className='mt-7 text-2xl font-semibold tracking-tight'>
              {model.name}
            </h3>
            <p className='realyu-home-muted mt-3 min-h-12 text-sm leading-relaxed'>
              {t(model.description)}
            </p>
            <div className='realyu-model-rates mt-5 border-t pt-5 text-sm'>
              {model.rates ? (
                <div className='space-y-2'>
                  <div className='flex justify-between gap-2'>
                    <span className='realyu-home-muted'>{t('Input')}</span>
                    <strong className='font-mono tabular-nums'>
                      {money(model.rates.input)}
                    </strong>
                  </div>
                  <div className='flex justify-between gap-2'>
                    <span className='realyu-home-muted'>{t('Output')}</span>
                    <strong className='font-mono tabular-nums'>
                      {money(model.rates.output)}
                    </strong>
                  </div>
                </div>
              ) : (
                <p className='realyu-home-muted min-h-12'>{unavailable}</p>
              )}
              <p className='realyu-home-muted mt-3 text-xs'>
                {t('USD per 1 million tokens')}
              </p>
            </div>
          </Button>
        ))}
      </div>
      <div className='realyu-home-muted mt-4 space-y-1 text-center text-xs leading-relaxed'>
        <p>
          {t(
            'Standard-mode rates for short contexts, including group pricing.'
          )}
        </p>
        <p>
          {t(
            'Text estimates exclude image and tool charges. Cached input and priority modes use separate rates.'
          )}
        </p>
      </div>

      <div className='realyu-estimator mt-12 grid overflow-hidden rounded-3xl lg:grid-cols-[1.15fr_1fr]'>
        <div className='p-7 sm:p-9'>
          <div className='flex items-center gap-3'>
            <ChartNoAxesColumn
              className='realyu-model-symbol size-6'
              aria-hidden='true'
            />
            <span className='realyu-section-kicker'>
              {t('Usage, made clear.')}
            </span>
          </div>
          <h2 className='mt-5 text-2xl font-semibold sm:text-3xl'>
            {t('See how usage becomes cost.')}
          </h2>
          <p className='realyu-home-muted mt-3 text-sm leading-relaxed'>
            {t('Adjust the tokens to estimate your cost. No request is sent.')}
          </p>
          <p className='realyu-home-muted mt-2 text-xs leading-relaxed'>
            {t(
              'The estimate uses uncached input and adjusts for long contexts.'
            )}
          </p>
          <div className='mt-8 space-y-7'>
            {[
              {
                label: t('Input tokens'),
                value: input,
                change: setInput,
                id: 'estimate-input',
              },
              {
                label: t('Output tokens'),
                value: output,
                change: setOutput,
                id: 'estimate-output',
              },
            ].map((slider) => (
              <div key={slider.id}>
                <label
                  htmlFor={slider.id}
                  className='mb-3 flex items-center justify-between gap-2 text-sm'
                >
                  <span>{slider.label}</span>
                  <span className='font-mono tabular-nums'>
                    {count(slider.value)}
                  </span>
                </label>
                <input
                  id={slider.id}
                  type='range'
                  aria-label={slider.label}
                  min={0}
                  max={1000000}
                  step={10000}
                  value={slider.value}
                  onChange={(event) =>
                    slider.change(Number(event.target.value))
                  }
                  className='realyu-token-range w-full cursor-pointer accent-sky-500'
                />
              </div>
            ))}
          </div>
        </div>
        <div className='realyu-estimate-result flex flex-col justify-between gap-8 p-7 sm:p-9'>
          <div>
            <p className='realyu-home-muted text-sm'>
              {t('Estimated cost')} · {active.name}
            </p>
            <output
              className='mt-4 block font-mono text-5xl font-semibold tracking-tight tabular-nums sm:text-6xl'
              aria-label={t('Estimated cost')}
              aria-live='polite'
            >
              {estimateRates
                ? money(
                    (input * estimateRates.input +
                      output * estimateRates.output) /
                      1000000
                  )
                : '—'}
            </output>
            <p className='realyu-home-muted mt-3 text-sm'>
              {count(input + output)} tokens
            </p>
            <div
              className='realyu-token-meter mt-6 flex h-2 overflow-hidden rounded-full'
              aria-hidden='true'
            >
              <span style={{ width: `${input / 20000}%` }} />
              <span style={{ width: `${output / 20000}%` }} />
            </div>
          </div>
          <div className='grid gap-3 text-sm'>
            {[
              { icon: Coins, text: t('Track spending in dollars') },
              { icon: ChartNoAxesColumn, text: t('Understand usage by model') },
              { icon: Users, text: t('Trace usage to each member') },
            ].map((item) => (
              <div key={item.text} className='flex items-center gap-3'>
                <item.icon
                  className='realyu-model-symbol size-4'
                  aria-hidden='true'
                />
                <span>{item.text}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}
