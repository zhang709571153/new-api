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
  Activity,
  MessageSquare,
  CreditCard,
  ChartNoAxesCombined,
  Box,
  FileText,
  Radio,
  Settings,
  User,
  Users,
  BookOpen,
  HeartPulse,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { SidebarData } from '@/components/layout/types'
import { hasPermission } from '@/lib/admin-permissions'
import { useAuthStore } from '@/stores/auth-store'

export function useSidebarData(): SidebarData {
  const { t } = useTranslation()
  const canReadCustomers = useAuthStore((state) =>
    hasPermission(state.auth.user, 'team', 'read')
  )
  return {
    navGroups: [
      {
        id: 'general',
        title: t('Personal'),
        items: [
          {
            title: t('My workspace'),
            url: '/dashboard/overview',
            icon: Activity,
          },
          { title: t('AI chat'), url: '/playground', icon: MessageSquare },
          { title: t('My usage'), url: '/usage', icon: FileText },
          {
            title: t('Usage trends'),
            url: '/dashboard/models?scope=self',
            configUrls: ['/dashboard/models'],
            icon: ChartNoAxesCombined,
          },
          { title: t('Plans and PAYGO'), url: '/wallet', icon: CreditCard },
          { title: t('My team'), url: '/team', icon: Users },
          { title: t('Model catalog'), url: '/pricing', icon: Box },
          { title: t('Documentation'), url: '/docs', icon: BookOpen },
          {
            title: t('Service status'),
            url: '/service-status',
            icon: HeartPulse,
          },
          { title: t('Account'), url: '/profile', icon: User },
        ],
      },
      {
        id: 'admin',
        title: t('Supplier management'),
        items: [
          {
            title: t('Usage analytics'),
            url: '/dashboard/models?scope=all',
            configUrls: ['/dashboard/models'],
            icon: ChartNoAxesCombined,
          },
          {
            title: t('All usage logs'),
            url: '/usage-logs/common',
            icon: FileText,
          },
          ...(canReadCustomers
            ? [{ title: t('Customer usage'), url: '/supplier', icon: Users }]
            : []),
          { title: t('Users'), url: '/users', icon: Users },
          { title: t('Channels'), url: '/channels', icon: Radio },
          { title: t('Models'), url: '/models/metadata', icon: Box },
          {
            title: t('Subscription Management'),
            url: '/subscriptions',
            icon: CreditCard,
          },
          {
            title: t('System Settings'),
            url: '/system-settings/site',
            activeUrls: ['/system-settings'],
            icon: Settings,
          },
        ],
      },
    ],
  }
}
