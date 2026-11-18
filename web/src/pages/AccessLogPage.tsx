import { useEffect, useState } from 'react'
import { fetchRunLogs } from '../api'
import { BottomSheet } from '../components/BottomSheet'
import { EmptyRunState } from '../components/EmptyRunState'
import { PaginationBar } from '../components/PaginationBar'
import { FixtureMode } from '../fixtures/mockupData'
import { LogQueryResponse, RequestRecord, Run } from '../types'

interface AccessLogPageProps {
  selectedRun: Run | null
  currentRun: Run | null
  detailError: string | null
  loadingDetail: boolean
  onRetryDetail: () => void
  onRefreshRun: () => void
  onSwitchToCurrentRun: () => void
  refreshingRun: boolean
  selectedRequestId: string | null
  onSelectRequest: (requestId: string) => void
  onGoToTraffic: () => void
  onOpenRunSelector: () => void
  isMobile: boolean
  fixtureMode: FixtureMode
}

export function AccessLogPage({
  selectedRun,
  currentRun,
  detailError,
  loadingDetail,
  onRetryDetail,
  onRefreshRun,
  onSwitchToCurrentRun,
  refreshingRun,
  selectedRequestId,
  onSelectRequest,
  onGoToTraffic,
  onOpenRunSelector,
  isMobile,
  fixtureMode,
}: AccessLogPageProps) {
  const [statusFilter, setStatusFilter] = useState<'all' | '200' | '404'>('all')
  const [pathFilter, setPathFilter] = useState('')
  const [cursor, setCursor] = useState<string | null>(null)
  const [locateActive, setLocateActive] = useState<boolean>(() => Boolean(selectedRequestId))
  const [data, setData] = useState<LogQueryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reloadNonce, setReloadNonce] = useState(0)
  const [detailRequest, setDetailRequest] = useState<RequestRecord | null>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const pageSize = fixtureMode !== 'none' ? 5 : 25

  // Reset pagination when switching runs or changing filters
  useEffect(() => {
    setCursor(null)
  }, [selectedRun?.id, statusFilter, pathFilter])

  useEffect(() => {
    setLocateActive(Boolean(selectedRequestId))
  }, [selectedRun?.id])

  useEffect(() => {
    if (!selectedRun) {
      setData(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchRunLogs(
      selectedRun.id,
      {
        status: statusFilter,
        path: pathFilter,
        cursor,
        snapshotId: selectedRun.snapshot_id,
        limit: pageSize,
        locateRequestId: locateActive && cursor === null ? selectedRequestId : null,
      },
      fixtureMode,
    )
      .then((res) => {
        if (cancelled) return
        setData(res)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Could not load log.')
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [
    selectedRun?.id,
    selectedRun?.snapshot_id,
    selectedRun?.revision,
    selectedRun?.logged_count,
    selectedRun?.status,
    statusFilter,
    pathFilter,
    cursor,
    locateActive,
    selectedRequestId,
    pageSize,
    fixtureMode,
    reloadNonce,
  ])

  const handleCopyPath = async (req: RequestRecord) => {
    try {
      await navigator.clipboard.writeText(req.path)
      setCopiedId(req.id)
      window.setTimeout(() => setCopiedId((cur) => (cur === req.id ? null : cur)), 1500)
    } catch {
      setCopiedId(null)
    }
  }

  if (detailError && !selectedRun) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-title">Access log</h1>
            <p className="page-subtitle">Inspect requests for the selected run.</p>
          </div>
        </div>
        <div className="status-banner is-error" role="alert" style={{ marginTop: 20 }}>
          <div className="status-banner-text">
            <strong>Could not load run</strong>
            <span>{detailError}</span>
          </div>
          <button type="button" className="btn btn-primary" onClick={onRetryDetail}>
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (!selectedRun && !loadingDetail) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-title">Access log</h1>
            <p className="page-subtitle">Inspect requests for the selected run.</p>
          </div>
        </div>
        {isMobile && (
          <p style={{ margin: '10px 0 0', color: 'var(--ink-subtle)' }}>No runs yet</p>
        )}
        <EmptyRunState kind="logs" onGoToTraffic={onGoToTraffic} />
      </div>
    )
  }

  if (!selectedRun) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-title">Access log</h1>
            <p className="page-subtitle">Inspect requests for the selected run.</p>
          </div>
        </div>
        <div className="skeleton-line" style={{ width: '100%', height: 56, marginTop: 20 }} />
      </div>
    )
  }

  const items = data?.items ?? []
  const start = data && data.total_matching > 0 ? data.offset + 1 : 0
  const end = data ? data.offset + items.length : 0

  const isHistorical = Boolean(selectedRun && !selectedRun.is_current)

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Access log</h1>
          <p className="page-subtitle">Inspect requests for the selected run.</p>
        </div>
      </div>

      {isMobile && (
        <div className="mobile-run-bar is-single">
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
        </div>
      )}

      {selectedRun.status === 'pending_log' && (
        <div className="status-banner is-warning" role="status" style={{ marginBottom: 16 }}>
          <div className="status-banner-text">
            <strong>{isHistorical ? 'Log pending · Read-only' : 'Log pending'}</strong>
            <span>
              {selectedRun.error_message ??
                `${selectedRun.logged_count} of ${selectedRun.expected_count} requests logged.`}
            </span>
          </div>
          {!isHistorical ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                onRefreshRun()
                setReloadNonce((n) => n + 1)
              }}
              disabled={refreshingRun}
            >
              {refreshingRun ? 'Refreshing…' : 'Refresh'}
            </button>
          ) : (
            currentRun && (
              <button type="button" className="btn btn-outline" onClick={onSwitchToCurrentRun}>
                Switch to {currentRun.label}
              </button>
            )
          )}
        </div>
      )}

      <div className="toolbar-row">
        <div className="toolbar-filters">
          <select
            aria-label="Filter by HTTP status"
            className="select-control"
            value={statusFilter}
            onChange={(e) => {
              setLocateActive(false)
              setCursor(null)
              setStatusFilter(e.target.value as 'all' | '200' | '404')
            }}
          >
            <option value="all">All statuses</option>
            <option value="200">200 OK</option>
            <option value="404">404 Not Found</option>
          </select>

          <div className="search-box">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <circle cx="7" cy="7" r="4.8" stroke="currentColor" strokeWidth="1.5" />
              <path
                d="M10.7 10.7L14 14"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
            <input
              type="search"
              aria-label="Filter path"
              placeholder="Filter path"
              className="search-input filter-path-input"
              value={pathFilter}
              onChange={(e) => {
                setLocateActive(false)
                setCursor(null)
                setPathFilter(e.target.value)
              }}
            />
          </div>
        </div>

        <span className="access-log-summary">
          {data ? `${data.total_matching} requests` : `${selectedRun.request_count} requests`}
          {data?.revision ? ` · Snapshot ${data.revision}` : ''}
        </span>
      </div>

      {data?.located_hidden_by_filter && data.located_request && (
        <div className="status-banner is-warning" role="status">
          <div className="status-banner-text">
            <strong>Request {data.located_request.seq} is hidden by your active filters</strong>
            <span>
              Status {data.located_request.status} ({data.located_request.path}) does not match the
              current filter.
            </span>
          </div>
          <button
            type="button"
            className="btn btn-outline"
            onClick={() => {
              setLocateActive(false)
              setStatusFilter('all')
              setPathFilter('')
              setCursor(null)
            }}
          >
            Clear filters
          </button>
        </div>
      )}

      {loading ? (
        <div style={{ marginTop: 24 }} role="status">
          <div style={{ fontSize: '0.94rem', color: 'var(--ink-muted)', marginBottom: 8 }}>
            Loading requests…
          </div>
          <div className="skeleton-line" style={{ width: '100%' }} />
          <div className="skeleton-line" style={{ width: '82%' }} />
        </div>
      ) : error ? (
        <div className="status-banner is-error" role="alert">
          <div className="status-banner-text">
            <strong>Could not load log</strong>
            <span>Your filters are saved. ({error})</span>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setReloadNonce((n) => n + 1)}
          >
            Retry
          </button>
        </div>
      ) : data && data.total_in_snapshot === 0 ? (
        <div className="status-banner" role="status">
          <div className="status-banner-text">
            <strong>No log entries</strong>
            <span>{isHistorical ? 'Read-only historical run has no logged requests.' : 'Requests are still arriving.'}</span>
          </div>
          {!isHistorical ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                onRefreshRun()
                setReloadNonce((n) => n + 1)
              }}
              disabled={refreshingRun}
            >
              {refreshingRun ? 'Refreshing…' : 'Refresh'}
            </button>
          ) : (
            currentRun && (
              <button type="button" className="btn btn-outline" onClick={onSwitchToCurrentRun}>
                Switch to {currentRun.label}
              </button>
            )
          )}
        </div>
      ) : items.length === 0 ? (
        <div className="status-banner" role="status">
          <div className="status-banner-text">
            <strong>No matching requests</strong>
            <span>Change the status or path filter.</span>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              setLocateActive(false)
              setStatusFilter('all')
              setPathFilter('')
              setCursor(null)
            }}
          >
            Clear filters
          </button>
        </div>
      ) : (
        <>
          <div className="table-wrap">
            <table className="log-table">
              <thead>
                <tr>
                  <th className="col-hide-mobile">Request</th>
                  <th>Time</th>
                  <th className="col-hide-mobile">Method</th>
                  <th>Path</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {items.map((req) => {
                  const isSelected = selectedRequestId === req.id
                  return (
                    <tr
                      key={req.id}
                      className={`is-clickable ${isSelected ? 'is-selected' : ''}`}
                      tabIndex={0}
                      aria-selected={isSelected}
                      onClick={() => {
                        onSelectRequest(req.id)
                        setDetailRequest(req)
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          onSelectRequest(req.id)
                          setDetailRequest(req)
                        }
                      }}
                    >
                      <td className="col-hide-mobile mono" style={{ fontWeight: 700 }}>
                        {String(req.seq).padStart(2, '0')}
                      </td>
                      <td className="mono">{req.display_time}</td>
                      <td className="col-hide-mobile mono">{req.method}</td>
                      <td className="mono path-cell" title={req.path}>
                        {req.path}
                      </td>
                      <td>
                        <span className={`status-pill ${req.status === 404 ? 'is-404' : 'is-200'}`}>
                          {req.status}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <PaginationBar
            start={start}
            end={end}
            total={data?.total_matching ?? 0}
            hasPrev={Boolean(data?.prev_cursor !== null && data?.prev_cursor !== undefined)}
            hasNext={Boolean(data?.next_cursor)}
            onPrev={() => {
              setLocateActive(false)
              setCursor(data?.prev_cursor ?? null)
            }}
            onNext={() => {
              setLocateActive(false)
              setCursor(data?.next_cursor ?? null)
            }}
            compact={isMobile}
          />
        </>
      )}

      <BottomSheet
        open={detailRequest !== null}
        title={detailRequest ? `Request ${detailRequest.seq}` : 'Request'}
        onClose={() => setDetailRequest(null)}
      >
        {detailRequest && (
          <div>
            <dl className="detail-dl">
              <dt>Run</dt>
              <dd>{selectedRun.label}</dd>
              <dt>Time</dt>
              <dd className="mono">{detailRequest.display_time}</dd>
              <dt>Method</dt>
              <dd className="mono">{detailRequest.method}</dd>
              <dt>Path</dt>
              <dd className="mono">{detailRequest.path}</dd>
              <dt>Status</dt>
              <dd>
                <span
                  className={`status-pill ${detailRequest.status === 404 ? 'is-404' : 'is-200'}`}
                >
                  {detailRequest.status_text}
                </span>
              </dd>
            </dl>
            <div style={{ marginTop: 18 }}>
              <button
                type="button"
                className="btn btn-outline"
                onClick={() => handleCopyPath(detailRequest)}
              >
                {copiedId === detailRequest.id ? 'Copied' : 'Copy path'}
              </button>
            </div>
          </div>
        )}
      </BottomSheet>
    </div>
  )
}
