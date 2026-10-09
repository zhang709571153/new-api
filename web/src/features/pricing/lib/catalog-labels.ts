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
import type { TFunction } from 'i18next'

export function catalogTierLabel(
  label: string | undefined,
  t: TFunction
): string {
  if (!label) return t('Default')
  if (label === 'standard') return t('Standard')
  if (label === 'long') return t('Long context')
  return label
}

export function catalogEndpointLabel(endpoint: string, t: TFunction): string {
  switch (endpoint) {
    case 'openai':
      return t('Chat')
    case 'openai-response':
      return t('Responses API')
    case 'image-generation':
      return t('Image generation')
    case 'embeddings':
      return t('Embeddings')
    case 'jina-rerank':
      return t('Rerank')
    case 'openai-video':
      return t('Video')
    default:
      return endpoint
  }
}

export function catalogGroupLabel(group: string, t: TFunction): string {
  return group === 'default' ? t('Default') : group
}
