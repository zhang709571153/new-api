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
  type ColumnDef,
  type SortingState,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table'
import { type ReactNode, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { DataTableColumnHeader, StaticDataTable } from '@/components/data-table'
import {
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { toIntlLocale } from '@/i18n/languages'
import { formatCurrencyFromUSD } from '@/lib/currency'

import type { WorkspaceMember } from './api'
import { formatWorkspaceTokens } from './token-format'

interface TeamMembersTableProps {
  members: WorkspaceMember[]
  weeklyAllowance?: boolean
  showStatus?: boolean
  balanceDigitsSmall?: number
  renderMember?: (member: WorkspaceMember) => ReactNode
  renderActions?: (member: WorkspaceMember) => ReactNode
}

export function TeamMembersTable(props: TeamMembersTableProps) {
  const { t, i18n } = useTranslation()
  const locale = toIntlLocale(i18n.resolvedLanguage || i18n.language)
  const [sorting, setSorting] = useState<SortingState>([])
  const columns = useMemo<ColumnDef<WorkspaceMember>[]>(
    () => [
      { id: 'id', header: t('ID'), accessorFn: (member) => member.user_id },
      {
        id: 'member',
        header: t('Member'),
        accessorFn: (member) => member.display_name || member.username,
        sortingFn: 'text',
      },
      {
        id: 'balance',
        header: props.weeklyAllowance
          ? t('Weekly remaining')
          : t('Available balance'),
        accessorFn: (member) =>
          Number.isFinite(member.balance_usd) ? member.balance_usd : undefined,
      },
      {
        id: 'weekly_spent',
        header: t('This week used'),
        accessorFn: (member) =>
          Number.isFinite(member.weekly_used_usd)
            ? member.weekly_used_usd
            : undefined,
      },
      {
        id: 'spent',
        header: t('Cumulative usage'),
        accessorFn: (member) =>
          Number.isFinite(member.used_usd) ? member.used_usd : undefined,
      },
      {
        id: 'tokens',
        header: t('Tokens used'),
        accessorFn: (member) => {
          if (
            !Number.isFinite(member.prompt_tokens) ||
            !Number.isFinite(member.completion_tokens)
          ) {
            return undefined
          }
          return member.prompt_tokens + member.completion_tokens
        },
      },
    ],
    [t, props.weeklyAllowance]
  )
  const table = useReactTable({
    data: props.members,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    defaultColumn: { sortingFn: 'basic', sortUndefined: 'last' },
    enableHiding: false,
    getRowId: (member) => String(member.user_id),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })
  const money = (value: number | undefined, digitsSmall?: number) =>
    value == null ? '—' : formatCurrencyFromUSD(value, { locale, digitsSmall })
  return (
    <StaticDataTable>
      <TableHeader>
        <TableRow>
          {table.getFlatHeaders().map((header) => {
            const sorted = header.column.getIsSorted()
            const direction = { asc: 'ascending', desc: 'descending' } as const
            return (
              <TableHead
                key={header.id}
                aria-sort={sorted ? direction[sorted] : 'none'}
              >
                <DataTableColumnHeader
                  column={header.column}
                  title={header.column.columnDef.header as string}
                />
              </TableHead>
            )
          })}
          {props.renderActions && (
            <TableHead className='w-14 text-right'>{t('Actions')}</TableHead>
          )}
        </TableRow>
      </TableHeader>
      <TableBody>
        {table.getRowModel().rows.map((row) => {
          const member = row.original
          const tokens = row.getValue<number | undefined>('tokens')
          return (
            <TableRow key={row.id}>
              <TableCell className='text-muted-foreground tabular-nums'>
                {member.user_id}
              </TableCell>
              <TableCell>
                <p className='font-medium'>
                  {props.renderMember
                    ? props.renderMember(member)
                    : member.display_name || member.username}
                </p>
                {(props.showStatus || member.is_owner) && (
                  <p className='text-muted-foreground text-xs'>
                    {member.is_owner && t('Owner')}
                    {!member.is_owner &&
                      (member.status === 1 ? t('Active') : t('Paused'))}
                  </p>
                )}
              </TableCell>
              <TableCell className='tabular-nums'>
                {money(row.getValue('balance'), props.balanceDigitsSmall)}
              </TableCell>
              <TableCell className='tabular-nums'>
                {money(row.getValue('weekly_spent'), 4)}
              </TableCell>
              <TableCell className='tabular-nums'>
                {money(row.getValue('spent'), 4)}
              </TableCell>
              <TableCell className='tabular-nums'>
                {formatWorkspaceTokens(tokens, locale)}
              </TableCell>
              {props.renderActions && (
                <TableCell className='w-14 text-right'>
                  {props.renderActions(member)}
                </TableCell>
              )}
            </TableRow>
          )
        })}
      </TableBody>
    </StaticDataTable>
  )
}
