import { useEffect, useRef, useState } from 'react'
import { executeRunChecks, fetchRunChecks } from '../api'
import { BottomSheet } from '../components/BottomSheet'
import { EmptyRunState } from '../components/EmptyRunState'
import { FixtureMode } from '../fixtures/mockupData'
import { CheckResult, Run } from '../types'

interface ChecksPageProps {
  selectedRun: Run | null
  detailError: string | null
  loadingDetail: boolean
  onRetryDetail: () => void
  onGoToTraffic: () => void
  onOpenRunSelector: () => void
  isMobile: boolean
  fixtureMode: FixtureMode
}

function VerdictBadge({ verdict }: { verdict: CheckResult['verdict'] }) {
  if (verdict === 'pass') return <span className="status-pill is-pass">Pass</span>
  if (verdict === 'fail') return <span className="status-pill is-fail">Fail</span>
  if (verdict === 'error') return <span className="status-pill is-error">Error</span>
  return <span className="status-pill is-info">Not run</span>
}

function EvidenceRows({ check }: { check: CheckResult }) {
  if (check.name === 'write_log') {
    const roText = String(
      check.evidence?.read_only_mount ??
        (check.verdict === 'error' ? (check.evidence?.summary ?? check.observed) : check.observed),
    )
    const ctrlSummary = String(
      (check.control_result?.summary as string | undefined) ??
        (check.verdict === 'error' ? 'Control not verified (execution error)' : 'No control summary recorded'),
    )
    return (
      <div>
        <div className="inspector-row" style={{ gridTemplateColumns: '1fr' }}>
          <dt>Read-only mount</dt>
          <dd>{roText}</dd>
        </div>
        <div className="inspector-row" style={{ gridTemplateColumns: '1fr' }}>
          <dt>Writable control</dt>
          <dd>{ctrlSummary}</dd>
        </div>
        {check.stderr_snippet && check.verdict === 'error' && (
          <div className="inspector-row" style={{ gridTemplateColumns: '1fr' }}>
            <dt>Error detail</dt>
            <dd className="mono" style={{ fontSize: '0.82rem' }}>{check.stderr_snippet}</dd>
          </div>
        )}
      </div>
    )
  }

  if (check.name === 'reach_test_service') {
    const sbStatus = check.evidence?.sandbox_status_code
    const sbBlocked =
      typeof check.evidence?.summary === 'string' && check.evidence.summary
        ? check.evidence.summary
        : check.verdict === 'error'
          ? check.observed
          : check.evidence?.sandbox_blocked === true
            ? 'Blocked under --network none'
            : sbStatus !== undefined && sbStatus !== null
              ? `Allowed (HTTP ${sbStatus})`
              : check.observed
    const ctrlSummary = String(
      (check.control_result?.summary as string | undefined) ??
        (check.verdict === 'error' ? 'Control not verified (execution error)' : check.observed),
    )
    return (
      <div>
        <div className="inspector-row" style={{ gridTemplateColumns: '1fr' }}>
          <dt>Sandbox (--network none)</dt>
          <dd>{sbBlocked}</dd>
        </div>
        <div className="inspector-row" style={{ gridTemplateColumns: '1fr' }}>
          <dt>Networked control</dt>
          <dd>{ctrlSummary}</dd>
        </div>
        {check.stderr_snippet && check.verdict === 'error' && (
          <div className="inspector-row" style={{ gridTemplateColumns: '1fr' }}>
            <dt>Error detail</dt>
            <dd className="mono" style={{ fontSize: '0.82rem' }}>{check.stderr_snippet}</dd>
          </div>
        )}
      </div>
    )
  }

  const summary = String(check.evidence?.summary ?? check.observed)
  const expectedSha = String(check.control_result?.expected_sha256 ?? '')
  return (
    <div>
      <div className="inspector-row" style={{ gridTemplateColumns: '1fr' }}>
        <dt>Read-only snapshot</dt>
        <dd>{summary}</dd>
      </div>
      {expectedSha && (
        <div className="inspector-row" style={{ gridTemplateColumns: '1fr' }}>
          <dt>Snapshot SHA-256</dt>
          <dd className="mono" style={{ fontSize: '0.82rem' }}>
            {expectedSha.slice(0, 20)}…
          </dd>
        </div>
      )}
      {check.stderr_snippet && check.verdict === 'error' && (
        <div className="inspector-row" style={{ gridTemplateColumns: '1fr' }}>
          <dt>Error detail</dt>
          <dd className="mono" style={{ fontSize: '0.82rem' }}>{check.stderr_snippet}</dd>
        </div>
      )}
    </div>
  )
}

export function ChecksPage({
  selectedRun,
  detailError,
  loadingDetail,
  onRetryDetail,
  onGoToTraffic,
  onOpenRunSelector,
  isMobile,
  fixtureMode,
}: ChecksPageProps) {
  const [checks, setChecks] = useState<CheckResult[]>([])
  const [selectedName, setSelectedName] = useState<string>('write_log')
  const [sheetCheck, setSheetCheck] = useState<CheckResult | null>(null)
  const [loadingChecks, setLoadingChecks] = useState(false)
  const [running, setRunning] = useState(false)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [fetchNonce, setFetchNonce] = useState(0)

  const snapKey = selectedRun ? `${selectedRun.id}:${selectedRun.snapshot_id ?? ''}:${selectedRun.revision}` : ''
  const activeSnapKeyRef = useRef(snapKey)
  activeSnapKeyRef.current = snapKey
  const opSeqRef = useRef(0)

  useEffect(() => {
    setSheetCheck(null)
    setRunning(false)
    setFetchError(null)
    setActionError(null)
    if (!selectedRun) {
      setChecks([])
      setLoadingChecks(false)
      return
    }
    const targetSnapKey = snapKey
    const opSeq = ++opSeqRef.current
    setChecks([])
    setLoadingChecks(true)
    fetchRunChecks(selectedRun.id, fixtureMode)
      .then((items) => {
        if (opSeq !== opSeqRef.current || activeSnapKeyRef.current !== targetSnapKey) return
        setChecks(items)
        setLoadingChecks(false)
        if (items.length > 0 && !items.some((c) => c.name === selectedName)) {
          setSelectedName(items.find((c) => c.name === 'write_log')?.name ?? items[0].name)
        }
      })
      .catch((err: unknown) => {
        if (opSeq !== opSeqRef.current || activeSnapKeyRef.current !== targetSnapKey) return
        setLoadingChecks(false)
        setFetchError(err instanceof Error ? err.message : 'Could not load checks.')
      })
  }, [snapKey, fixtureMode, fetchNonce])

  const handleRunChecks = async () => {
    if (!selectedRun || running) return
    const targetRunId = selectedRun.id
    const targetSnapKey = snapKey
    const opSeq = ++opSeqRef.current
    setRunning(true)
    setFetchError(null)
    setActionError(null)
    try {
      const items = await executeRunChecks(targetRunId, fixtureMode)
      if (opSeq !== opSeqRef.current || activeSnapKeyRef.current !== targetSnapKey) return
      setChecks(items)
      setSelectedName(items.find((c) => c.name === 'write_log')?.name ?? items[0]?.name ?? 'write_log')
    } catch (err: unknown) {
      if (opSeq !== opSeqRef.current || activeSnapKeyRef.current !== targetSnapKey) return
      setActionError(err instanceof Error ? err.message : 'Could not execute checks.')
    } finally {
      if (activeSnapKeyRef.current === targetSnapKey) {
        setRunning(false)
      }
    }
  }

  if (detailError && !selectedRun) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-title">Checks</h1>
            <p className="page-subtitle">Compare Linux worker behavior against the expected policy.</p>
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
            <h1 className="page-title">Checks</h1>
            <p className="page-subtitle">
              Compare Linux worker behavior against the expected policy.
            </p>
          </div>
        </div>
        {isMobile && <p style={{ margin: '10px 0 0', color: 'var(--ink-subtle)' }}>No runs yet</p>}
        <EmptyRunState kind="checks" onGoToTraffic={onGoToTraffic} />
      </div>
    )
  }

  if (!selectedRun) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-title">Checks</h1>
            <p className="page-subtitle">Compare Linux worker behavior against the expected policy.</p>
          </div>
        </div>
        <div className="skeleton-line" style={{ width: '100%', height: 56, marginTop: 20 }} />
      </div>
    )
  }

  const activeCheck = checks.find((c) => c.name === selectedName) ?? checks[0] ?? null

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Checks</h1>
          <p className="page-subtitle">Compare Linux worker behavior against the expected policy.</p>
        </div>
        {checks.length > 0 && (
          <div className="page-header-cta">
            <button
              type="button"
              className="btn btn-primary"
              disabled={running || selectedRun.status !== 'ready'}
              onClick={() => void handleRunChecks()}
            >
              {running ? 'Running…' : 'Run checks'}
            </button>
          </div>
        )}
      </div>

      {isMobile && (
        <div className="mobile-run-bar">
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
          <button
            type="button"
            className="btn btn-primary"
            disabled={running || selectedRun.status !== 'ready'}
            onClick={() => void handleRunChecks()}
          >
            {running ? 'Running…' : 'Run checks'}
          </button>
        </div>
      )}

      {running && (
        <div className="status-banner" role="status" style={{ marginTop: 20 }}>
          <div className="status-banner-text" style={{ width: '100%' }}>
            <strong>Running checks</strong>
            <span>Checking the read-only mount, writable control, and network isolation.</span>
            <div className="progress-track">
              <div className="progress-bar-indeterminate" />
            </div>
          </div>
        </div>
      )}

      {fetchError && !running && (
        <div className="status-banner is-error" role="alert" style={{ marginTop: 20 }}>
          <div className="status-banner-text">
            <strong>Could not load checks</strong>
            <span>{fetchError}</span>
          </div>
          <button type="button" className="btn btn-primary" onClick={() => setFetchNonce((n) => n + 1)}>
            Retry
          </button>
        </div>
      )}

      {actionError && (
        <div className="status-banner is-error" role="alert" style={{ marginTop: 20 }}>
          <div className="status-banner-text">
            <strong>Could not run checks</strong>
            <span>{actionError}</span>
          </div>
          <button type="button" className="btn btn-primary" onClick={() => void handleRunChecks()}>
            Retry
          </button>
        </div>
      )}

      {loadingChecks && !running && checks.length === 0 ? (
        <div style={{ marginTop: 20 }} role="status">
          <div style={{ fontSize: '0.94rem', color: 'var(--ink-muted)', marginBottom: 8 }}>
            Loading checks…
          </div>
          <div className="skeleton-line" style={{ width: '100%', height: 56 }} />
        </div>
      ) : !running && !fetchError && checks.length === 0 ? (
        <div className="status-banner" style={{ marginTop: 24 }}>
          <div className="status-banner-text">
            <strong>Not run</strong>
            <span>Run the checks for this snapshot (Revision {selectedRun.revision}).</span>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            disabled={selectedRun.status !== 'ready'}
            onClick={() => void handleRunChecks()}
          >
            Run checks
          </button>
        </div>
      ) : checks.length > 0 ? (
        <div className="split-workspace">
          <div>
            {isMobile && (
              <div style={{ marginBottom: 12 }}>
                <h2 className="section-label" style={{ marginBottom: 4 }}>
                  Isolation checks
                </h2>
                <div style={{ fontSize: '0.88rem', color: 'var(--ink-muted)' }}>
                  {fixtureMode === 'mockup'
                    ? 'Illustrative results'
                    : `Snapshot Revision ${checks[0]?.revision ?? selectedRun.revision}`}
                </div>
              </div>
            )}

            <div className="checks-ledger">
              <table className="checks-table">
                <thead>
                  <tr>
                    <th>Check</th>
                    <th>Expected</th>
                    <th>Observed</th>
                    <th>Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {checks.map((chk) => {
                    const selected = activeCheck?.check_id === chk.check_id
                    return (
                      <tr
                        key={chk.check_id}
                        className={selected ? 'is-selected' : ''}
                        tabIndex={0}
                        aria-selected={selected}
                        onClick={() => setSelectedName(chk.name)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault()
                            setSelectedName(chk.name)
                          }
                        }}
                      >
                        <td style={{ fontWeight: 700 }}>{chk.title}</td>
                        <td>{chk.expected}</td>
                        <td>{chk.observed}</td>
                        <td>
                          <VerdictBadge verdict={chk.verdict} />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            <div className="checks-mobile-list">
              {checks.map((chk) => {
                const selected = activeCheck?.check_id === chk.check_id
                return (
                  <div
                    key={chk.check_id}
                    className={`check-mobile-card ${selected ? 'is-selected' : ''}`}
                    onClick={() => setSelectedName(chk.name)}
                  >
                    <div className="check-mobile-card-main">
                      <button
                        type="button"
                        aria-pressed={selected}
                        className="check-mobile-row-trigger"
                        onClick={(e) => {
                          e.stopPropagation()
                          setSelectedName(chk.name)
                        }}
                      >
                        <span style={{ fontWeight: 700, fontSize: '1rem', color: 'var(--ink)' }}>
                          {chk.title}
                        </span>
                        <span style={{ fontSize: '0.88rem', color: 'var(--ink-muted)', marginTop: 4 }}>
                          Expected: {chk.expected} · Observed: {chk.observed}
                        </span>
                      </button>
                      {selected && (
                        <button
                          type="button"
                          className="link-action"
                          onClick={(e) => {
                            e.stopPropagation()
                            setSheetCheck(chk)
                          }}
                        >
                          View evidence →
                        </button>
                      )}
                    </div>
                    <VerdictBadge verdict={chk.verdict} />
                  </div>
                )
              })}
            </div>

            {!isMobile && (
              <div style={{ marginTop: 14, fontSize: '0.88rem', color: 'var(--ink-muted)' }}>
                {fixtureMode === 'mockup'
                  ? 'Illustrative results'
                  : `Snapshot Revision ${checks[0]?.revision ?? selectedRun.revision} · Policy ${
                      checks[0]?.policy_version ?? 'sandbox-policy-v1'
                    }`}
              </div>
            )}
          </div>

          {activeCheck && (
            <aside className="inspector-panel inspector-desktop-only" aria-label="Control evidence">
              <div style={{ paddingBottom: 12, borderBottom: '1px solid #d3e7f3' }}>
                <h2 style={{ margin: 0, fontSize: '1.12rem', fontWeight: 700 }}>
                  {activeCheck.title}
                </h2>
                <div style={{ fontSize: '0.9rem', color: 'var(--ink-muted)', marginTop: 2 }}>
                  Control evidence
                </div>
              </div>
              <EvidenceRows check={activeCheck} />
            </aside>
          )}
        </div>
      ) : null}

      <BottomSheet
        open={sheetCheck !== null}
        title={sheetCheck ? `${sheetCheck.title} evidence` : 'Control evidence'}
        onClose={() => setSheetCheck(null)}
      >
        {sheetCheck && (
          <div>
            <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ color: 'var(--ink-muted)', fontSize: '0.9rem' }}>
                Expected: {sheetCheck.expected} · Observed: {sheetCheck.observed}
              </span>
              <VerdictBadge verdict={sheetCheck.verdict} />
            </div>
            <EvidenceRows check={sheetCheck} />
          </div>
        )}
      </BottomSheet>
    </div>
  )
}
