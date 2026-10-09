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
import fs from 'node:fs'
import path from 'node:path'

import type { HtmlTag, HtmlTagHandler } from '@rsbuild/core'
import { expect, test } from 'vitest'

import createConfig from '../../../rsbuild.config'

const webRoot = process.cwd()

test.each(['production', 'development'])(
  'removes automatically injected icons while retaining scripts and styles in %s',
  (envMode) => {
    const config = createConfig({ env: envMode, envMode, command: 'build' })
    const configured = config.html?.tags
    const descriptors = Array.isArray(configured) ? configured : [configured]
    const handlers = descriptors.filter(
      (tag): tag is HtmlTagHandler => typeof tag === 'function'
    )
    expect(handlers).not.toHaveLength(0)
    const retained: HtmlTag[] = [
      { tag: 'link', attrs: { rel: 'stylesheet', href: '/static/index.css' } },
      { tag: 'script', attrs: { src: '/static/index.js' } },
      {
        tag: 'link',
        attrs: { rel: 'manifest', href: '/manifest.webmanifest' },
      },
    ]
    const input: HtmlTag[] = [
      { tag: 'link', attrs: { rel: 'icon', href: '/favicon.ico' } },
      {
        tag: 'link',
        attrs: { rel: 'shortcut icon', href: '/brand/favicon.ico' },
      },
      ...retained,
    ]
    const result = handlers.reduce(
      (tags, handler) =>
        handler(tags, {
          hash: '',
          entryName: 'index',
          outputName: 'index.html',
          publicPath: '/',
        }) ?? tags,
      input
    )
    expect(result).toEqual(retained)
  }
)

test('keeps the explicit blank favicon in the template and retains the original file', () => {
  const template = fs.readFileSync(path.join(webRoot, 'index.html'), 'utf8')
  const document = new DOMParser().parseFromString(template, 'text/html')
  const icons = document.querySelectorAll('link[rel~="icon"]')
  expect(icons).toHaveLength(1)
  expect(icons[0].getAttribute('href')).toBe('data:,')
  expect(fs.existsSync(path.join(webRoot, 'public/favicon.ico'))).toBe(true)
})
