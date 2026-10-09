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

import { isDevelopmentSite } from '@/lib/development-site'

export function DevelopmentBanner() {
  const { t } = useTranslation()
  if (!isDevelopmentSite()) return null
  return (
    <div
      role='status'
      className='sticky top-0 z-50 bg-amber-100 px-4 py-2 text-center text-sm text-amber-950'
    >
      {t(
        'Development site: test data only. Changes do not affect your live balance.'
      )}
      {' · '}
      <a href='https://api.realyu.fun' className='font-medium underline'>
        {t('Open live site')}
      </a>
    </div>
  )
}
