import type { ImgHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

// RealYu logo — sources from /logo.png (single-source-of-truth for visual identity).
// SVGProps signature kept loose-compatible via `any` consumers; most callers pass only `className`.
export function Logo({
  className,
  ...props
}: ImgHTMLAttributes<HTMLImageElement>) {
  return (
    <img
      src='/logo.png'
      alt='RealYu API'
      className={cn('size-6 rounded-md object-cover', className)}
      {...props}
    />
  )
}
