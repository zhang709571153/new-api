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
import { VChart } from '@visactor/react-vchart'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { EmptyState } from '@/components/empty-state'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useTheme } from '@/context/theme-provider'
import { DEFAULT_TIME_GRANULARITY } from '@/features/dashboard/constants'
import { getDashboardChartColors } from '@/features/dashboard/lib/charts'
import { aggregateUsageCharts } from '@/features/dashboard/lib/usage-charts'
import type {
  ModelAnalyticsChartTab,
  QuotaDataItem,
  UsageMetric,
} from '@/features/dashboard/types'
import { toIntlLocale } from '@/i18n/languages'
import { formatQuotaWithCurrency } from '@/lib/currency'
import dayjs from '@/lib/dayjs'
import { formatNumber, quotaUnitsToDollars } from '@/lib/format'
import type { TimeGranularity } from '@/lib/time'
import { VCHART_OPTION } from '@/lib/vchart'
import { useSystemConfigStore } from '@/stores/system-config-store'

export function ModelCharts(props: {
  title?: string
  data: QuotaDataItem[]
  loading?: boolean
  timeGranularity?: TimeGranularity
  defaultChartTab?: ModelAnalyticsChartTab
  defaultMetric?: UsageMetric
}) {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const { resolvedTheme } = useTheme()
  useSystemConfigStore((state) => state.config.currency)
  const [selection, setSelection] = useState<{
    defaults: string
    metric: UsageMetric
    chart: ModelAnalyticsChartTab
  }>()
  const defaults = `${props.defaultMetric}:${props.defaultChartTab}`
  const metric =
    selection?.defaults === defaults
      ? selection.metric
      : (props.defaultMetric ?? 'quota')
  const chart =
    selection?.defaults === defaults
      ? selection.chart
      : (props.defaultChartTab ?? 'trend')
  const granularity = props.timeGranularity ?? DEFAULT_TIME_GRANULARITY
  const quotaUnitValue = quotaUnitsToDollars(1)
  const data = useMemo(
    () =>
      aggregateUsageCharts(
        props.loading ? [] : props.data,
        metric,
        granularity,
        t('Other'),
        quotaUnitValue
      ),
    [props.data, props.loading, metric, granularity, t, quotaUnitValue]
  )
  const money = (raw: number) =>
    formatQuotaWithCurrency(raw, {
      digitsLarge: 4,
      digitsSmall: 6,
      abbreviate: false,
    })
  const formatValue = (raw: number) =>
    metric === 'quota' ? money(raw) : formatNumber(raw, locale)
  const labels = new Map(data.totals.map((row) => [row.Model, row.Name]))
  const tooltip = {
    mark: {
      title: {
        value: (row: Record<string, unknown>) => String(row.Name ?? ''),
      },
      content: [
        {
          key: t(
            { quota: 'Cost', tokens: 'Token usage', requests: 'Requests' }[
              metric
            ]
          ),
          value: (row: Record<string, unknown>) => formatValue(Number(row.Raw)),
        },
      ],
    },
  }
  const common = {
    background: 'transparent',
    theme: resolvedTheme === 'dark' ? 'dark' : 'light',
    color: {
      type: 'ordinal',
      domain: [...labels.keys()],
      range: getDashboardChartColors(labels.size),
    },
    legends: {
      visible: true,
      orient: 'bottom',
      item: {
        label: { formatMethod: (label: string) => labels.get(label) ?? label },
      },
    },
    tooltip,
  }
  let spec: Record<string, unknown>
  if (chart === 'proportion') {
    spec = {
      ...common,
      type: 'pie',
      data: [{ id: 'usage', values: data.totals }],
      valueField: 'Value',
      categoryField: 'Model',
      outerRadius: 0.8,
      innerRadius: 0.55,
      label: { visible: false },
    }
  } else if (chart === 'top') {
    spec = {
      ...common,
      type: 'bar',
      data: [{ id: 'usage', values: data.totals }],
      xField: 'Model',
      yField: 'Value',
      seriesField: 'Model',
      axes: [
        {
          orient: 'bottom',
          type: 'band',
          label: {
            formatMethod: (label: string) => labels.get(label) ?? label,
          },
        },
        { orient: 'left', type: 'linear', min: 0 },
      ],
    }
  } else {
    spec = {
      ...common,
      type: 'area',
      data: [{ id: 'usage', values: data.trend }],
      xField: 'Time',
      yField: 'Value',
      seriesField: 'Model',
      stack: true,
      point: { visible: data.trend.length <= data.totals.length },
      line: { style: { curveType: 'linear' } },
      axes: [
        {
          orient: 'bottom',
          type: 'band',
          label: {
            formatMethod: (timestamp: string) =>
              dayjs
                .unix(Number(timestamp))
                .format(granularity === 'hour' ? 'MM-DD HH:mm' : 'YYYY-MM-DD'),
          },
        },
        { orient: 'left', type: 'linear', min: 0 },
      ],
    }
  }
  return (
    <section
      className='overflow-hidden rounded-lg border'
      aria-label={props.title ?? t('Usage trends')}
    >
      <div className='flex flex-wrap items-center gap-3 border-b px-3 py-3 sm:px-5'>
        <Tabs
          className='max-w-full'
          value={metric}
          onValueChange={(value) =>
            setSelection({ defaults, metric: value as UsageMetric, chart })
          }
        >
          <TabsList
            aria-label={t('Metric')}
            className='max-w-full flex-wrap group-data-horizontal/tabs:h-auto'
          >
            <TabsTrigger value='quota'>{t('Cost')}</TabsTrigger>
            <TabsTrigger value='requests'>{t('Requests')}</TabsTrigger>
            <TabsTrigger value='tokens'>{t('Token usage')}</TabsTrigger>
          </TabsList>
        </Tabs>
        <span
          className='text-muted-foreground text-sm tabular-nums'
          aria-live='polite'
        >
          {t('Total:')} {formatValue(data.total)}
        </span>
        <Tabs
          className='max-w-full sm:ml-auto'
          value={chart}
          onValueChange={(value) =>
            setSelection({
              defaults,
              metric,
              chart: value as ModelAnalyticsChartTab,
            })
          }
        >
          <TabsList
            aria-label={t('Chart type')}
            className='max-w-full flex-wrap group-data-horizontal/tabs:h-auto'
          >
            <TabsTrigger value='top'>{t('Bar Chart')}</TabsTrigger>
            <TabsTrigger value='proportion'>
              {t('Distribution chart')}
            </TabsTrigger>
            <TabsTrigger value='trend'>{t('Trend chart')}</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      <div
        className='h-[320px] p-2 sm:h-[420px]'
        data-testid='usage-chart'
        data-metric={metric}
        data-chart={chart}
      >
        {props.loading && <Skeleton className='h-full w-full' />}
        {!props.loading && data.total === 0 && (
          <EmptyState title={t('No data available')} />
        )}
        {!props.loading && data.total !== 0 && (
          <VChart
            key={`${metric}-${chart}-${resolvedTheme}`}
            spec={spec}
            option={VCHART_OPTION}
          />
        )}
      </div>
    </section>
  )
}
