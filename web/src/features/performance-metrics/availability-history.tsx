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
import { useTranslation } from 'react-i18next'

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { toIntlLocale } from '@/i18n/languages'
import { cn } from '@/lib/utils'

import { formatUptimePct, getSuccessRateDotClass } from './lib/format'

// Existing pricing sparkline is fixed to 24 slots; this view supports server
// windows of 24 or 168 hours and explicitly fills unsampled hours.
export function AvailabilityHistory(props: {
  points: { ts: number; success_rate: number | null }[]
  start: number
  hours: number
}) {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const rates = new Map(
    props.points.map((point) => [point.ts, point.success_rate])
  )
  return (
    <div
      className='flex h-5 w-full min-w-0 gap-px'
      role='group'
      aria-label={t('Hourly success rate')}
    >
      {Array.from({ length: props.hours }, (_, i) => {
        const ts = props.start + i * 3600
        const rate = rates.get(ts)
        const label = `${new Date(ts * 1000).toLocaleString(locale)} · ${rate == null ? t('No data') : formatUptimePct(rate)}`
        return (
          <Tooltip key={ts}>
            <TooltipTrigger
              render={
                <span
                  tabIndex={0}
                  aria-label={label}
                  className={cn(
                    'min-w-0 flex-1 rounded-xs',
                    rate == null
                      ? 'bg-muted-foreground/15'
                      : getSuccessRateDotClass(rate)
                  )}
                />
              }
            />
            <TooltipContent>{label}</TooltipContent>
          </Tooltip>
        )
      })}
    </div>
  )
}
