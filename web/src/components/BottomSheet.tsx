import { ReactNode, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

interface BottomSheetProps {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

export function BottomSheet({ open, title, onClose, children }: BottomSheetProps) {
  const panelRef = useRef<HTMLDivElement | null>(null)
  const closeBtnRef = useRef<HTMLButtonElement | null>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    previousFocusRef.current = document.activeElement as HTMLElement | null

    const rootEl = document.getElementById('root')
    const hadInert = rootEl ? rootEl.hasAttribute('inert') : false
    const prevAriaHidden = rootEl ? rootEl.getAttribute('aria-hidden') : null
    if (rootEl) {
      rootEl.setAttribute('inert', '')
      rootEl.setAttribute('aria-hidden', 'true')
    }

    const timer = window.setTimeout(() => {
      closeBtnRef.current?.focus()
    }, 10)

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
        return
      }
      if (e.key === 'Tab' && panelRef.current) {
        const focusables = Array.from(
          panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
        ).filter((el) => el.offsetParent !== null || el === document.activeElement)
        if (focusables.length === 0) {
          e.preventDefault()
          return
        }
        const first = focusables[0]
        const last = focusables[focusables.length - 1]
        if (e.shiftKey) {
          if (document.activeElement === first || !panelRef.current.contains(document.activeElement)) {
            e.preventDefault()
            last.focus()
          }
        } else if (document.activeElement === last || !panelRef.current.contains(document.activeElement)) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('keydown', onKeyDown)
      if (rootEl) {
        if (!hadInert) rootEl.removeAttribute('inert')
        if (prevAriaHidden === null) rootEl.removeAttribute('aria-hidden')
        else rootEl.setAttribute('aria-hidden', prevAriaHidden)
      }
      previousFocusRef.current?.focus?.()
    }
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div
      className="sheet-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={panelRef}
        className="sheet-panel"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="sheet-handle" aria-hidden="true" />
        <div className="sheet-header">
          <h2>{title}</h2>
          <button
            ref={closeBtnRef}
            type="button"
            className="sheet-close-btn"
            aria-label={`Close ${title}`}
            onClick={onClose}
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path
                d="M5 5L15 15M15 5L5 15"
                stroke="currentColor"
                strokeWidth="1.9"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
