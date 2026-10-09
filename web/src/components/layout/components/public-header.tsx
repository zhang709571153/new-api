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
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'

import { NotificationPopover } from '@/components/notification-popover'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ThemeSwitch } from '@/components/theme-switch'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useNotifications } from '@/hooks/use-notifications'
import { useStatus } from '@/hooks/use-status'
import { useSystemConfig } from '@/hooks/use-system-config'
import { useTopNavLinks } from '@/hooks/use-top-nav-links'
import { isDevelopmentSite } from '@/lib/development-site'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'

import type { TopNavLink } from '../types'
import { HeaderLogo } from './header-logo'
import { TopNav } from './top-nav'

export interface PublicHeaderProps {
  navLinks?: TopNavLink[]
  mobileLinks?: TopNavLink[]
  navContent?: React.ReactNode
  showThemeSwitch?: boolean
  showLanguageSwitcher?: boolean
  logo?: React.ReactNode
  siteName?: string
  homeUrl?: string
  leftContent?: React.ReactNode
  rightContent?: React.ReactNode
  showNavigation?: boolean
  showAuthButtons?: boolean
  showNotifications?: boolean
  className?: string
}

export function PublicHeader(props: PublicHeaderProps) {
  const { t } = useTranslation()
  const { status } = useStatus()
  const { systemName, logo, loading, logoLoaded } = useSystemConfig()
  const user = useAuthStore((state) => state.auth.user)
  const notifications = useNotifications()
  const defaultLinks = useTopNavLinks()
  const displaySiteName = props.siteName || systemName
  const canRegister =
    status?.register_enabled !== false && !status?.self_use_mode_enabled

  return (
    <header
      className={cn(
        'bg-background/85 text-foreground border-border/50 z-40 border-b backdrop-blur-xl',
        isDevelopmentSite() ? 'relative' : 'fixed inset-x-0 top-0',
        props.className
      )}
    >
      <nav className='mx-auto flex w-full max-w-6xl items-center justify-between gap-2 px-6 py-5 sm:px-8'>
        <Link
          to={props.homeUrl || '/'}
          aria-label={displaySiteName}
          className='focus-visible:ring-ring shrink-0 rounded-md outline-none focus-visible:ring-2'
        >
          {loading ? (
            <Skeleton className='h-8 w-36 md:h-10 md:w-45' />
          ) : (
            props.logo || (
              <HeaderLogo
                src={logo}
                alt={displaySiteName}
                loading={loading}
                logoLoaded={logoLoaded}
                className='h-auto w-36 md:w-45'
              />
            )
          )}
        </Link>
        {props.leftContent}
        {props.showNavigation !== false &&
          (props.navContent ?? (
            <TopNav links={props.navLinks ?? defaultLinks} />
          ))}
        <div className='flex min-w-0 items-center gap-1 sm:gap-2'>
          {props.showThemeSwitch !== false && <ThemeSwitch direct />}
          {props.showNotifications && (
            <NotificationPopover
              open={notifications.popoverOpen}
              onOpenChange={notifications.setPopoverOpen}
              unreadCount={notifications.unreadCount}
              activeTab={notifications.activeTab}
              onTabChange={notifications.setActiveTab}
              notice={notifications.notice}
              announcements={notifications.announcements}
              loading={notifications.loading}
            />
          )}
          {props.rightContent}
          {props.showAuthButtons !== false && (
            <>
              {loading && <Skeleton className='h-11 w-24 rounded-xl' />}
              {!loading && user && <ProfileDropdown />}
              {!loading && !user && (
                <>
                  <Button
                    variant='ghost'
                    className='h-11 rounded-xl px-3 text-sm'
                    role='link'
                    render={<Link to='/sign-in' />}
                  >
                    {t('Sign in')}
                  </Button>
                  {canRegister && (
                    <Button
                      className='hidden h-11 rounded-xl bg-[#22b8df] px-5 text-sm font-semibold text-slate-950 hover:bg-[#50bfdc] sm:inline-flex'
                      role='link'
                      render={<Link to='/sign-up' />}
                    >
                      {t('Get started')}
                    </Button>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </nav>
    </header>
  )
}
