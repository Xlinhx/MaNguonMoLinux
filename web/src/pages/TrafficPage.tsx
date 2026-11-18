import { useEffect, useRef, useState } from 'react'
import { fetchRunLogs } from '../api'
import { FixtureMode } from '../fixtures/mockupData'
import { RequestRecord, Run } from '../types'

interface TrafficPageProps {
  selectedRun: Run | null
  selectedRunId: string | null
  currentRun: Run | null
  selectedRequestId: string | null
  hasExplicitSelection: boolean
  onSelectRequest: (requestId: string, explicit?: boolean) => void
  onOpenInAccessLog: (requestId: string) => void
  onCreateRun: () => void
  onRefreshRun: () => void
  onAdd404: () => void
  onSwitchToCurrentRun: () => void
  onOpenRunSelector: () => void
  creating: boolean
  adding404: boolean
  refreshing: boolean
  actionError: string | null
  detailError: string | null
  onRetryDetail: () => void
  onRetryAction: () => void
  isMobile: boolean
  loadingDetail: boolean
  fixtureMode: FixtureMode
  refreshNonce: number
}

function SequenceRuler({ slots }: { slots: number }) {
  return (
    <svg
      className="sequence-rail"
      viewBox={`0 0 ${slots * 100} 16`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <line x1="2" y1="11" x2={slots * 100 - 2} y2="11" stroke="#237CD4" strokeWidth="1.6" />
      <line x1="2" y1="6" x2="2" y2="15" stroke="#237CD4" strokeWidth="1.6" />
      <line x1={slots * 100 - 2} y1="6" x2={slots * 100 - 2} y2="15" stroke="#237CD4" strokeWidth="1.6" />
      {Array.from({ length: slots }, (_, idx) => {
        const cx = idx * 100 + 50
        return <line key={idx} x1={cx} y1="2" x2={cx} y2="11" stroke="#237CD4" strokeWidth="1.6" />
      })}
    </svg>
  )
}

export function TrafficPage({
  selectedRun,
  selectedRunId,
  currentRun,
  selectedRequestId,
  hasExplicitSelection,
  onSelectRequest,
  onOpenInAccessLog,
  onCreateRun,
  onRefreshRun,
  onAdd404,
  onSwitchToCurrentRun,
  onOpenRunSelector,
  creating,
  adding404,
  refreshing,
  actionError,
  detailError,
  onRetryDetail,
  onRetryAction,
  isMobile,
  loadingDetail,
  fixtureMode,
  refreshNonce,
}: TrafficPageProps) {
  const pageSize = isMobile ? 5 : 10
  const [seqPage, setSeqPage] = useState(1)
  const [manualPage, setManualPage] = useState<number | null>(null)
  const [visibleRequests, setVisibleRequests] = useState<RequestRecord[]>([])
  const [totalRequests, setTotalRequests] = useState(0)
  const [loadingSlice, setLoadingSlice] = useState(false)
  const [logsError, setLogsError] = useState<string | null>(null)
  const [sliceRetryNonce, setSliceRetryNonce] = useState(0)

  const prevRunSigRef = useRef<string>('')
  const prevRunIdRef = useRef<string | null>(null)
  const fetchTokenRef = useRef(0)

  const runSig = selectedRun
    ? `${selectedRun.id}:${selectedRun.revision}:${selectedRun.snapshot_id ?? ''}:${selectedRun.logged_count}:${pageSize}`
    : ''

  useEffect(() => {
    if (!selectedRun) {
      setVisibleRequests([])
      setTotalRequests(0)
      setManualPage(null)
      setLogsError(null)
      prevRunSigRef.current = ''
      prevRunIdRef.current = null
      return
    }

    if (prevRunIdRef.current !== selectedRun.id) {
      prevRunIdRef.current = selectedRun.id
      setVisibleRequests([])
      setTotalRequests(0)
    }

    const isNewSig = prevRunSigRef.current !== runSig
    let effectiveManualPage = manualPage
    if (isNewSig) {
      prevRunSigRef.current = runSig
      effectiveManualPage = null
      if (manualPage !== null) setManualPage(null)
    }

    const knownTotal =
      selectedRun.status === 'pending_log'
        ? selectedRun.logged_count
        : selectedRun.status === 'error'
          ? 0
          : selectedRun.request_count
    const inferredPages = Math.max(1, Math.ceil(knownTotal / pageSize))
    const targetPage = effectiveManualPage ?? inferredPages
    const useLocate =
      effectiveManualPage === null &&
      hasExplicitSelection &&
      selectedRequestId !== null

    const token = ++fetchTokenRef.current
    setLoadingSlice(true)
    setLogsError(null)

    fetchRunLogs(
      selectedRun.id,
      {
        order: 'asc',
        snapshotId: selectedRun.snapshot_id,
        limit: pageSize,
        cursor: useLocate ? null : String(Math.max(0, targetPage - 1) * pageSize),
        locateRequestId: useLocate ? selectedRequestId : null,
      },
      fixtureMode,
    )
      .then((res) => {
        if (token !== fetchTokenRef.current) return
        setVisibleRequests(res.items)
        setTotalRequests(res.total_in_snapshot)
        setLogsError(null)
        const computedPage = Math.floor(res.offset / pageSize) + 1
        setSeqPage(computedPage)
        if (res.items.length > 0 && !res.items.some((r) => r.id === selectedRequestId)) {
          onSelectRequest(res.items[res.items.length - 1].id, false)
        }
        setLoadingSlice(false)
      })
      .catch((err: unknown) => {
        if (token !== fetchTokenRef.current) return
        setLogsError(err instanceof Error ? err.message : 'Could not load request sequence.')
        setLoadingSlice(false)
      })
  }, [runSig, manualPage, refreshNonce, sliceRetryNonce, hasExplicitSelection, fixtureMode])

  const totalPages = Math.max(1, Math.ceil(totalRequests / pageSize))
  const clampedPage = Math.min(Math.max(1, seqPage), totalPages)
  const startIdx = (clampedPage - 1) * pageSize

  const selectedRequest =
    visibleRequests.find((r) => r.id === selectedRequestId) ??
    visibleRequests[visibleRequests.length - 1] ??
    null

  const hasRun = selectedRun !== null
  const isHistorical = Boolean(selectedRun && !selectedRun.is_current)
  const showPaging = totalRequests > pageSize

  const handlePageChange = (nextPage: number) => {
    const p = Math.min(Math.max(1, nextPage), totalPages)
    setManualPage(p)
    setSeqPage(p)
  }

  // Detail loading error banner (block mutations so actions never hit a stale run)
  if (detailError && !selectedRun) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-title">Traffic</h1>
            <p className="page-subtitle">Generate and inspect Nginx requests for this run.</p>
          </div>
          <div className="page-header-cta">
            <button type="button" className="btn btn-primary" onClick={onCreateRun} disabled={creating}>
              {creating ? 'Creating…' : 'Create run'}
            </button>
          </div>
        </div>
        <div className="status-banner is-error" role="alert" style={{ marginTop: 20 }}>
          <div className="status-banner-text">
            <strong>Could not load run</strong>
            <span>{detailError}</span>
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            {selectedRunId && (
              <button type="button" className="btn btn-primary" onClick={onRetryDetail}>
                Retry
              </button>
            )}
            {currentRun && (
              <button type="button" className="btn btn-outline" onClick={onSwitchToCurrentRun}>
                Switch to {currentRun.label}
              </button>
            )}
          </div>
        </div>
      </div>
    )
  }

  // Empty state (no runs yet)
  if (!hasRun && !loadingDetail && !creating && !actionError) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-title">Traffic</h1>
            {!isMobile ? (
              <p className="page-subtitle">Send 10 requests to Nginx to create an access log.</p>
            ) : (
              <p className="page-subtitle" style={{ color: 'var(--ink-subtle)' }}>
                No runs yet
              </p>
            )}
          </div>
          <div className="page-header-cta">
            <button type="button" className="btn btn-primary" onClick={onCreateRun} disabled={creating}>
              Create run
            </button>
          </div>
        </div>

        {isMobile ? (
          <div style={{ marginTop: 18 }}>
            <h2 style={{ margin: 0, fontSize: '1.45rem', fontWeight: 750, letterSpacing: '-0.02em' }}>
              Create your first run
            </h2>
            <p style={{ margin: '8px 0 4px', color: 'var(--ink)' }}>
              Send 10 requests to Nginx to create an access log.
            </p>
            <p style={{ margin: '0 0 16px', fontSize: '0.93rem', color: 'var(--ink-muted)' }}>
              Expected: 7 HTTP 200 · 3 HTTP 404
            </p>
            <button
              type="button"
              className="btn btn-primary"
              style={{ width: '100%', minHeight: 46 }}
              onClick={onCreateRun}
              disabled={creating}
            >
              Create run
            </button>
          </div>
        ) : (
          <div className="empty-intro-line">
            <strong>First run</strong>
            <span className="empty-intro-sep" aria-hidden="true" />
            <span className="empty-intro-expected">Expected: 7 HTTP 200 · 3 HTTP 404</span>
          </div>
        )}

        <section className="sequence-band" aria-label="Request sequence">
          <div className="sequence-head">
            <h2>Request sequence</h2>
          </div>
          <div className="sequence-grid" aria-hidden="true">
            {Array.from({ length: pageSize }, (_, idx) => (
              <div key={idx} className="sequence-vacant" />
            ))}
          </div>
          <SequenceRuler slots={pageSize} />
          <p className="sequence-empty-caption">Requests will appear here after the first run.</p>
        </section>
      </div>
    )
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Traffic</h1>
          <p className="page-subtitle">Generate and inspect Nginx requests for this run.</p>
        </div>
        <div className="page-header-cta">
          <button
            type="button"
            className="btn btn-primary"
            onClick={onCreateRun}
            disabled={creating}
          >
            {creating ? 'Creating…' : 'Create run'}
          </button>
        </div>
      </div>

      {/* Mobile Run + Create run bar */}
      {isMobile && (
        <div className={`mobile-run-bar ${!selectedRun ? 'is-single' : ''}`}>
          {selectedRun && (
            <button
              type="button"
              data-run-trigger="true"
              className="run-trigger"
              onClick={onOpenRunSelector}
            >
              <span>{selectedRun.label}</span>
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path
                  d="M3.2 5.2L7 9L10.8 5.2"
                  stroke="currentColor"
                  strokeWidth="1.7"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          )}
          <button
            type="button"
            className="btn btn-primary"
            onClick={onCreateRun}
            disabled={creating}
          >
            {creating ? 'Creating…' : 'Create run'}
          </button>
        </div>
      )}

      {/* State banners */}
      {creating && (
        <div className="status-banner" role="status">
          <div className="status-banner-text">
            <strong>Creating run</strong>
            <span>Waiting for Nginx access log.</span>
          </div>
          <button type="button" className="btn btn-primary" disabled>
            Creating…
          </button>
        </div>
      )}

      {!creating && (actionError || selectedRun?.status === 'error') && (
        <div className="status-banner is-error" role="alert">
          <div className="status-banner-text">
            <strong>Run failed</strong>
            <span>{actionError ?? selectedRun?.error_message ?? 'Nginx is unavailable.'}</span>
          </div>
          {!isHistorical && (
            <button type="button" className="btn btn-primary" onClick={onRetryAction}>
              Retry
            </button>
          )}
        </div>
      )}

      {!creating && !isHistorical && selectedRun?.status === 'creating' && (
        <div className="status-banner is-warning" role="status">
          <div className="status-banner-text">
            <strong>Run unfinished</strong>
            <span>Check Nginx access log to resume or complete this run.</span>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            onClick={onRefreshRun}
            disabled={refreshing}
          >
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      )}

      {!creating && selectedRun?.status === 'pending_log' && (
        <div className="status-banner is-warning" role="status">
          <div className="status-banner-text">
            <strong>{isHistorical ? 'Log pending · Read-only' : 'Log pending'}</strong>
            <span>
              {selectedRun.error_message ??
                `${selectedRun.logged_count} of ${selectedRun.expected_count} requests logged.`}
            </span>
          </div>
          {!isHistorical && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={onRefreshRun}
              disabled={refreshing}
            >
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
          )}
        </div>
      )}

      {isHistorical && currentRun && (
        <div className="status-banner" role="status">
          <div className="status-banner-text">
            <strong>{selectedRun?.label} · Read-only</strong>
            <span>Open the current run to add a request.</span>
          </div>
          <button type="button" className="btn btn-outline" onClick={onSwitchToCurrentRun}>
            Switch to {currentRun.label}
          </button>
        </div>
      )}

      {/* Counts & Add 404 row */}
      {selectedRun && (
        <div className="counts-row">
          <div className="counts-metrics">
            <div className="metric-total">
              <strong>{selectedRun.request_count}</strong>
              <span>requests</span>
            </div>
            <span className="counts-divider" aria-hidden="true" />
            <div className="legend-item">
              <span className="swatch swatch-200" aria-hidden="true" />
              <span>
                <strong>{selectedRun.ok_count}</strong> successful
              </span>
            </div>
            <div className="legend-item">
              <span className="swatch swatch-404" aria-hidden="true" />
              <span>
                <strong>{selectedRun.not_found_count}</strong> not found
              </span>
            </div>
          </div>

          {!isHistorical && selectedRun.status === 'ready' && (
            <div className="counts-actions-mobile-right">
              <button
                type="button"
                className="btn btn-outline"
                onClick={onAdd404}
                disabled={adding404 || creating || loadingDetail}
              >
                {adding404 ? 'Adding…' : 'Add 404'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* Request Sequence Band */}
      <section className="sequence-band" aria-label="Request sequence">
        <div className="sequence-head">
          <h2>Request sequence</h2>
          <span className="sequence-order">Oldest to newest</span>
        </div>

        {logsError && !loadingDetail && !loadingSlice && (
          <div className="status-banner is-error" role="alert" style={{ marginTop: 12 }}>
            <div className="status-banner-text">
              <strong>Could not load request sequence</strong>
              <span>{logsError}</span>
            </div>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setSliceRetryNonce((n) => n + 1)}
            >
              Retry
            </button>
          </div>
        )}

        {loadingDetail || (loadingSlice && visibleRequests.length === 0) ? (
          <div style={{ padding: '18px 0' }} role="status">
            <div className="skeleton-line" style={{ width: '100%', height: 56 }} />
          </div>
        ) : visibleRequests.length > 0 ? (
          <>
            <div className="sequence-grid">
              {visibleRequests.map((req) => {
                const isSelected = selectedRequest?.id === req.id
                const toneClass = req.status === 404 ? 'is-404' : 'is-200'
                return (
                  <button
                    key={req.id}
                    type="button"
                    className={`sequence-tile ${toneClass} ${isSelected ? 'is-selected' : ''}`}
                    aria-pressed={isSelected}
                    aria-label={`Request ${req.seq}, HTTP ${req.status}, ${req.path}`}
                    onClick={() => onSelectRequest(req.id, true)}
                  >
                    <span className="tile-seq">{String(req.seq).padStart(2, '0')}</span>
                    <span className="tile-code">{req.status}</span>
                  </button>
                )
              })}
            </div>
            <SequenceRuler slots={pageSize} />
          </>
        ) : (
          <>
            <div className="sequence-grid" aria-hidden="true">
              {Array.from({ length: pageSize }, (_, idx) => (
                <div key={idx} className="sequence-vacant" />
              ))}
            </div>
            <SequenceRuler slots={pageSize} />
            <p className="sequence-empty-caption">No logged requests recorded for this run yet.</p>
          </>
        )}

        {showPaging && (
          <div className="sequence-footer">
            <span className="sequence-range">
              {startIdx + 1}–{startIdx + visibleRequests.length} of {totalRequests}
            </span>
            <div className="arrow-group">
              <button
                type="button"
                className="icon-page-btn"
                aria-label="Previous sequence page"
                disabled={clampedPage <= 1 || loadingSlice}
                onClick={() => handlePageChange(clampedPage - 1)}
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
                aria-label="Next sequence page"
                disabled={clampedPage >= totalPages || loadingSlice}
                onClick={() => handlePageChange(clampedPage + 1)}
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
        )}
      </section>

      {/* Selected request strip */}
      {selectedRequest && (
        <section className="selection-strip" aria-label="Selected request details">
          <div className="selection-title">Request {selectedRequest.seq}</div>
          <div className="selection-path">
            {selectedRequest.method} {selectedRequest.path}
          </div>
          <div className="selection-status-cell">
            <span
              className={`status-pill ${selectedRequest.status === 404 ? 'is-404' : 'is-200'}`}
            >
              {selectedRequest.status_text}
            </span>
          </div>
          <div className="selection-time">{selectedRequest.display_time}</div>
          <div className="selection-link-cell">
            <button
              type="button"
              className="link-action"
              onClick={() => onOpenInAccessLog(selectedRequest.id)}
            >
              Open in access log →
            </button>
          </div>
        </section>
      )}
    </div>
  )
}
