import { useEffect, useRef, useState } from 'react'
import { fetchRuns } from '../api'
import { FixtureMode } from '../fixtures/mockupData'
import { Run } from '../types'
import { BottomSheet } from './BottomSheet'

interface RunSelectorProps {
  open: boolean
  isMobile: boolean
  selectedRun: Run | null
  fixtureMode: FixtureMode
  onClose: () => void
  onSelectRun: (runId: string) => void
  refreshNonce: number
}

const PAGE_SIZE = 3

export function RunSelector({
  open,
  isMobile,
  selectedRun,
  fixtureMode,
  onClose,
  onSelectRun,
  refreshNonce,
}: RunSelectorProps) {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [runs, setRuns] = useState<Run[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [retryToken, setRetryToken] = useState(0)
  const searchRef = useRef<HTMLInputElement | null>(null)
  const popoverRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const timer = window.setTimeout(() => {
      searchRef.current?.focus()
    }, 20)
    return () => window.clearTimeout(timer)
  }, [open])

  useEffect(() => {
    if (!open || isMobile) return
    const handleOutside = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null
      if (!target) return
      if (popoverRef.current?.contains(target)) return
      if (target.closest('[data-run-trigger="true"]')) return
      onClose()
    }
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    document.addEventListener('mousedown', handleOutside)
    window.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleOutside)
      window.removeEventListener('keydown', handleEsc)
    }
  }, [open, isMobile, onClose])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchRuns(search, page, PAGE_SIZE, fixtureMode)
      .then((res) => {
        if (cancelled) return
        setRuns(res.items)
        setTotal(res.total)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Could not load runs.')
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, search, page, fixtureMode, refreshNonce, retryToken])

  if (!open) return null

  const startIdx = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const endIdx = Math.min(page * PAGE_SIZE, total)
  const hasPrev = page > 1
  const hasNext = endIdx < total

  const content = (
    <div>
      {!isMobile && <h3 className="popover-title">Switch run</h3>}
      <div className="search-box">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <circle cx="7" cy="7" r="4.8" stroke="currentColor" strokeWidth="1.5" />
          <path d="M10.7 10.7L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <input
          ref={searchRef}
          type="search"
          className="search-input"
          placeholder="Search runs..."
          aria-label="Search runs"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value)
            setPage(1)
          }}
        />
      </div>

      {loading ? (
        <div style={{ padding: '14px 4px' }} role="status">
          <div style={{ fontSize: '0.92rem', color: 'var(--ink-muted)' }}>Loading runs…</div>
          <div className="skeleton-line" style={{ width: '92%' }} />
          <div className="skeleton-line" style={{ width: '74%' }} />
        </div>
      ) : error ? (
        <div style={{ padding: '12px 2px' }} role="alert">
          <div style={{ fontWeight: 650, fontSize: '0.95rem' }}>Could not load runs</div>
          <div style={{ fontSize: '0.88rem', color: 'var(--ink-muted)', marginTop: 4 }}>
            Your current run is still selected.
          </div>
          <button
            type="button"
            className="btn btn-primary"
            style={{ marginTop: 10 }}
            onClick={() => setRetryToken((t) => t + 1)}
          >
            Retry
          </button>
        </div>
      ) : runs.length === 0 ? (
        <div style={{ padding: '14px 2px' }}>
          <div style={{ fontWeight: 650, fontSize: '0.95rem' }}>No matching runs</div>
          <button
            type="button"
            className="btn btn-outline"
            style={{ marginTop: 10 }}
            onClick={() => {
              setSearch('')
              setPage(1)
            }}
          >
            Clear search
          </button>
        </div>
      ) : (
        <>
          <ul className="run-list" role="listbox" aria-label="Available runs">
            {runs.map((run) => {
              const isSelected = selectedRun?.id === run.id
              return (
                <li key={run.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    className={`run-option ${isSelected ? 'is-selected' : ''}`}
                    onClick={() => {
                      onSelectRun(run.id)
                      onClose()
                    }}
                  >
                    <span>
                      <span>{run.label}</span>
                      <span className="run-option-meta">
                        {' · '}
                        {run.is_current ? 'Current' : 'Read-only'}
                      </span>
                    </span>
                    {isSelected && (
                      <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
                        <path
                          d="M4 9.5L7.3 12.8L14 5.5"
                          stroke="#0065C9"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
          <div className="popover-footer">
            <span className="sequence-range">
              {startIdx}–{endIdx} of {total}
            </span>
            <div className="arrow-group">
              <button
                type="button"
                className="icon-page-btn"
                aria-label="Previous runs page"
                disabled={!hasPrev}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path
                    d="M10 3.5L5.5 8L10 12.5"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
              <button
                type="button"
                className="icon-page-btn"
                aria-label="Next runs page"
                disabled={!hasNext}
                onClick={() => setPage((p) => p + 1)}
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path
                    d="M6 3.5L10.5 8L6 12.5"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )

  if (isMobile) {
    return (
      <BottomSheet open={open} title="Switch run" onClose={onClose}>
        {content}
      </BottomSheet>
    )
  }

  return (
    <div ref={popoverRef} className="run-popover" role="dialog" aria-label="Switch run">
      {content}
    </div>
  )
}
