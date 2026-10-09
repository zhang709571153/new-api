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
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { AllowanceDialog } from '@/features/workspace/allowance-dialog'
import { UserSubscriptionSummary } from '@/features/workspace/subscription-summary'

import { isUserDeleted } from '../constants'
import type { User } from '../types'

export function UserSubscriptionCell(props: { user: User }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return (
    <div className='space-y-1'>
      <UserSubscriptionSummary userId={props.user.id} />
      {!isUserDeleted(props.user) && (
        <Button
          variant='link'
          size='sm'
          className='h-auto p-0'
          onClick={() => setOpen(true)}
        >
          {t('Edit subscription')}
        </Button>
      )}
      {open && (
        <AllowanceDialog
          user={props.user}
          onClose={() => setOpen(false)}
          onSuccess={() => {}}
        />
      )}
    </div>
  )
}
