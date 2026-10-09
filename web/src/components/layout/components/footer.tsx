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
import { Fragment } from 'react'
import { useTranslation } from 'react-i18next'

import { useStatus } from '@/hooks/use-status'
import { useSystemConfig } from '@/hooks/use-system-config'
import { DEFAULT_LOGO, DEFAULT_SYSTEM_NAME } from '@/lib/constants'
import { cn } from '@/lib/utils'

import { HeaderLogo } from './header-logo'

interface FooterLink {
  text: string
  href: string
}

interface FooterColumnProps {
  title: string
  links: FooterLink[]
}

interface FooterProps {
  logo?: string
  name?: string
  columns?: FooterColumnProps[]
  copyright?: string
  className?: string
}

function FooterLinkItem(props: { link: FooterLink }) {
  const { t } = useTranslation()
  const isExternal = props.link.href.startsWith('http')
  const label = t(props.link.text)

  if (isExternal) {
    return (
      <a
        href={props.link.href}
        target='_blank'
        rel='noopener noreferrer'
        className='text-muted-foreground hover:text-foreground text-sm transition-colors duration-200'
      >
        {label}
      </a>
    )
  }

  return (
    <Link
      to={props.link.href}
      className='text-muted-foreground hover:text-foreground text-sm transition-colors duration-200'
    >
      {label}
    </Link>
  )
}

// Renders User Agreement / Privacy Policy links inline with the parent's
// copyright row when either is configured in System Settings → Site. Emits
// fragmented siblings so the parent flex container's gap controls spacing.
function LegalLinks(props: { leadingSeparator?: boolean }) {
  const { t } = useTranslation()
  const { status } = useStatus()
  const items: { key: string; label: string; href: string }[] = []
  if (status?.user_agreement_enabled) {
    items.push({
      key: 'user-agreement',
      label: t('User Agreement'),
      href: '/user-agreement',
    })
  }
  if (status?.privacy_policy_enabled) {
    items.push({
      key: 'privacy-policy',
      label: t('Privacy Policy'),
      href: '/privacy-policy',
    })
  }
  if (items.length === 0) {
    return null
  }
  return (
    <>
      {items.map((item, index) => (
        <Fragment key={item.key}>
          {(props.leadingSeparator || index > 0) && (
            <span aria-hidden='true' className='text-muted-foreground/30'>
              ·
            </span>
          )}
          <Link
            to={item.href}
            className='hover:text-foreground transition-colors duration-200'
          >
            {item.label}
          </Link>
        </Fragment>
      ))}
    </>
  )
}

export function Footer(props: FooterProps) {
  const { t } = useTranslation()
  const {
    systemName,
    logo: systemLogo,
    footerHtml,
    demoSiteEnabled,
  } = useSystemConfig()
  const displayLogo = systemLogo || props.logo || DEFAULT_LOGO
  const displayName = systemName || props.name || DEFAULT_SYSTEM_NAME
  const currentYear = new Date().getFullYear()

  return (
    <footer
      className={cn('border-border/40 relative border-t', props.className)}
    >
      <div className='mx-auto flex max-w-6xl flex-col gap-6 px-6 py-6'>
        <div className='flex flex-wrap items-center justify-between gap-4'>
          {footerHtml ? (
            <div
              className='custom-footer text-muted-foreground min-w-0 text-sm'
              dangerouslySetInnerHTML={{ __html: footerHtml }}
            />
          ) : (
            <Link
              to='/'
              aria-label={displayName}
              className='flex items-center gap-2.5'
            >
              <HeaderLogo
                src={displayLogo}
                alt={displayName}
                loading={false}
                logoLoaded
              />
            </Link>
          )}
          <div className='text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-2 text-xs'>
            <span>
              &copy; {currentYear} {displayName}
              {props.copyright && <>. {props.copyright}</>}
            </span>
            <LegalLinks leadingSeparator />
          </div>
        </div>
        {demoSiteEnabled && props.columns && props.columns.length > 0 && (
          <div className='grid gap-6 sm:grid-cols-3'>
            {props.columns.map((column) => (
              <div key={column.title} className='flex flex-col gap-3'>
                <p className='text-sm font-medium'>{t(column.title)}</p>
                <ul className='flex flex-col gap-2'>
                  {column.links.map((link) => (
                    <li key={link.href}>
                      <FooterLinkItem link={link} />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
    </footer>
  )
}
