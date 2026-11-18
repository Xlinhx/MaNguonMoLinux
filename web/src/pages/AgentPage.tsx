import { useEffect, useRef, useState } from 'react'
import { askQuestion, cancelJob, fetchJob, fetchLatestRunJob, fetchRunMessages } from '../api'
import { BottomSheet } from '../components/BottomSheet'
import { EmptyRunState } from '../components/EmptyRunState'
import { FixtureMode } from '../fixtures/mockupData'
import { AgentMessage, JobRecord, Run, ToolResult } from '../types'

interface AgentPageProps {
  selectedRun: Run | null
  detailError: string | null
  loadingDetail: boolean
  onRetryDetail: () => void
  onGoToTraffic: () => void
  onOpenRunSelector: () => void
  trackedJob: JobRecord | null
  onTrackedJobChange: (runId: string, job: JobRecord | null) => void
  isMobile: boolean
  fixtureMode: FixtureMode
}

function renderFormattedAnswer(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return parts.map((part, idx) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={idx}>{part.slice(2, -2)}</strong>
    }
    return <span key={idx}>{part}</span>
  })
}

function formatToolOutput(tc: ToolResult): string {
  if (!tc.payload) {
    return tc.error_message ?? tc.status
  }
  if (tc.tool_name === 'count_status_codes') {
    const ok = tc.payload.status_200 ?? 0
    const nf = tc.payload.status_404 ?? 0
    return `200: ${ok} · 404: ${nf}`
  }
  if (tc.tool_name === 'top_error_paths') {
    const paths = (tc.payload.paths as Array<{ path: string; count: number }> | undefined) ?? []
    return paths.map((p) => `${p.path} (${p.count})`).join(', ') || 'No errors'
  }
  if (tc.tool_name === 'recent_http_errors') {
    const errs = (tc.payload.errors as Array<{ seq: number; status: number; path: string }> | undefined) ?? []
    return errs.slice(0, 3).map((e) => `#${e.seq} ${e.status} ${e.path}`).join(' · ') || 'No errors'
  }
  return JSON.stringify(tc.payload)
}

export function AgentPage({
  selectedRun,
  detailError,
  loadingDetail,
  onRetryDetail,
  onGoToTraffic,
  onOpenRunSelector,
  trackedJob,
  onTrackedJobChange,
  isMobile,
  fixtureMode,
}: AgentPageProps) {
  const [messages, setMessages] = useState<AgentMessage[]>([])
  const [input, setInput] = useState('')
  const [activeCall, setActiveCall] = useState<ToolResult | null>(null)
  const [sheetCall, setSheetCall] = useState<ToolResult | null>(null)
  const [activeJob, setActiveJob] = useState<JobRecord | null>(() =>
    selectedRun && trackedJob?.run_id === selectedRun.id ? trackedJob : null,
  )
  const [syncingResult, setSyncingResult] = useState(false)
  const [finalResultError, setFinalResultError] = useState<{
    job: JobRecord
    runId: string
    message: string
  } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [lastQuestion, setLastQuestion] = useState<string>('')
  const [loadingMessages, setLoadingMessages] = useState(false)
  const [readError, setReadError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [reloadNonce, setReloadNonce] = useState(0)

  const currentRunIdRef = useRef<string | null>(selectedRun?.id ?? null)
  currentRunIdRef.current = selectedRun?.id ?? null
  const trackedJobRef = useRef<JobRecord | null>(trackedJob)
  trackedJobRef.current = trackedJob
  const opSeqRef = useRef(0)
  const messagesEpochRef = useRef(0)
  const terminalSyncInFlightRef = useRef<string | null>(null)

  const updateJob = (runId: string, job: JobRecord | null) => {
    setActiveJob(job)
    onTrackedJobChange(runId, job)
  }

  const syncTerminalMessages = async (targetRunId: string, terminalJob: JobRecord, opSeq: number) => {
    const syncKey = `${targetRunId}:${terminalJob.job_id}:${opSeq}`
    if (terminalSyncInFlightRef.current === syncKey) return
    terminalSyncInFlightRef.current = syncKey
    // Advance message epoch immediately so any earlier intermediate/mount GET /messages cannot overwrite terminal state
    const syncEpoch = ++messagesEpochRef.current
    setSyncingResult(true)
    setFinalResultError(null)
    let lastErr: unknown = null
    try {
      for (let attempt = 0; attempt < 3; attempt++) {
        if (
          opSeq !== opSeqRef.current ||
          currentRunIdRef.current !== targetRunId ||
          messagesEpochRef.current !== syncEpoch
        ) {
          return
        }
        try {
          const msgs = await fetchRunMessages(targetRunId, fixtureMode)
          if (
            opSeq !== opSeqRef.current ||
            currentRunIdRef.current !== targetRunId ||
            messagesEpochRef.current !== syncEpoch
          ) {
            return
          }
          setMessages(msgs)
          setReadError(null)
          updateJob(targetRunId, terminalJob)
          setFinalResultError(null)
          setSyncingResult(false)
          const latestWithCall = [...msgs].reverse().find((m) => m.tool_calls && m.tool_calls.length > 0)
          if (latestWithCall) {
            setActiveCall(latestWithCall.tool_calls[latestWithCall.tool_calls.length - 1])
          }
          return
        } catch (err: unknown) {
          lastErr = err
          if (attempt < 2) {
            await new Promise((r) => window.setTimeout(r, 350))
          }
        }
      }
      if (
        opSeq !== opSeqRef.current ||
        currentRunIdRef.current !== targetRunId ||
        messagesEpochRef.current !== syncEpoch
      ) {
        return
      }
      setSyncingResult(false)
      // Always exit active polling/running lock cleanly when terminal job's messages fail to fetch
      updateJob(targetRunId, terminalJob)
      setFinalResultError({
        job: terminalJob,
        runId: targetRunId,
        message: lastErr instanceof Error ? lastErr.message : 'Could not load final response.',
      })
    } finally {
      if (terminalSyncInFlightRef.current === syncKey) {
        terminalSyncInFlightRef.current = null
      }
    }
  }

  useEffect(() => {
    const mountOpSeq = ++opSeqRef.current
    const mountEpoch = ++messagesEpochRef.current
    terminalSyncInFlightRef.current = null
    const remembered =
      selectedRun && trackedJobRef.current?.run_id === selectedRun.id
        ? trackedJobRef.current
        : null
    setActiveJob(remembered)
    setSyncingResult(false)
    setFinalResultError(null)
    setSubmitting(false)
    setCancelling(false)
    setSubmitError(null)
    if (!selectedRun) {
      setMessages([])
      setActiveCall(null)
      setLoadingMessages(false)
      setReadError(null)
      return
    }
    const targetRunId = selectedRun.id
    let cancelled = false
    setLoadingMessages(true)
    setReadError(null)
    fetchRunMessages(targetRunId, fixtureMode)
      .then((msgs) => {
        if (cancelled || messagesEpochRef.current !== mountEpoch) return
        setMessages(msgs)
        setLoadingMessages(false)
        setReadError(null)
        const latestWithCall = [...msgs].reverse().find((m) => m.tool_calls && m.tool_calls.length > 0)
        setActiveCall(latestWithCall?.tool_calls[latestWithCall.tool_calls.length - 1] ?? null)
      })
      .catch((err: unknown) => {
        if (cancelled || messagesEpochRef.current !== mountEpoch) return
        setLoadingMessages(false)
        setReadError(err instanceof Error ? err.message : 'Could not load conversation.')
      })

    fetchLatestRunJob(targetRunId, fixtureMode)
      .then((latestJob) => {
        if (cancelled || opSeqRef.current !== mountOpSeq || currentRunIdRef.current !== targetRunId) return
        if (!latestJob || typeof latestJob.job_id !== 'string' || typeof latestJob.status !== 'string') return
        const inFlight =
          latestJob.status === 'running' ||
          latestJob.status === 'pending' ||
          latestJob.status === 'cancelling'
        const wasTrackingInFlight = Boolean(
          remembered &&
            (remembered.status === 'running' ||
              remembered.status === 'pending' ||
              remembered.status === 'cancelling'),
        )
        if (inFlight) {
          updateJob(targetRunId, latestJob)
        } else if (wasTrackingInFlight) {
          void syncTerminalMessages(targetRunId, latestJob, mountOpSeq)
        }
      })
      .catch(() => {
        // Non-fatal: active polling handles in-flight jobs if tracked
      })

    return () => {
      cancelled = true
    }
  }, [selectedRun?.id, fixtureMode, reloadNonce])

  // Poll active job until terminal state & reload final messages cleanly with recovery
  useEffect(() => {
    if (
      !activeJob ||
      (activeJob.status !== 'running' && activeJob.status !== 'pending' && activeJob.status !== 'cancelling') ||
      !selectedRun ||
      activeJob.run_id !== selectedRun.id
    ) {
      return
    }
    const targetRunId = selectedRun.id
    const targetJobId = activeJob.job_id
    const opSeq = opSeqRef.current
    let stopped = false
    const timer = window.setInterval(async () => {
      if (stopped || terminalSyncInFlightRef.current) return
      try {
        const updated = await fetchJob(targetJobId, fixtureMode)
        if (stopped || opSeq !== opSeqRef.current || currentRunIdRef.current !== targetRunId) return
        if (updated.status !== 'running' && updated.status !== 'pending' && updated.status !== 'cancelling') {
          stopped = true
          window.clearInterval(timer)
          await syncTerminalMessages(targetRunId, updated, opSeq)
        } else {
          updateJob(targetRunId, updated)
        }
      } catch {
        // Keep polling transiently while job is in flight
      }
    }, 350)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [activeJob?.job_id, activeJob?.status, selectedRun?.id, fixtureMode])

  const isJobInFlight = (job: JobRecord | null, runId: string | null) =>
    Boolean(
      job &&
        runId &&
        job.run_id === runId &&
        (job.status === 'running' || job.status === 'pending' || job.status === 'cancelling'),
    )

  const submitQuestion = async (text: string) => {
    const trimmed = text.trim()
    if (
      !trimmed ||
      !selectedRun ||
      submitting ||
      syncingResult ||
      isJobInFlight(activeJob, selectedRun.id)
    ) {
      return
    }
    const targetRunId = selectedRun.id
    const opSeq = ++opSeqRef.current
    const midEpoch = ++messagesEpochRef.current
    setSubmitting(true)
    setLastQuestion(trimmed)
    setInput('')
    setSubmitError(null)
    setFinalResultError(null)
    try {
      const job = await askQuestion(targetRunId, trimmed, fixtureMode)
      if (opSeq !== opSeqRef.current || currentRunIdRef.current !== targetRunId) return
      const latestJob = await fetchJob(job.job_id, fixtureMode).catch(() => job)
      if (opSeq !== opSeqRef.current || currentRunIdRef.current !== targetRunId) return
      if (latestJob.status !== 'running' && latestJob.status !== 'pending' && latestJob.status !== 'cancelling') {
        await syncTerminalMessages(targetRunId, latestJob, opSeq)
      } else {
        updateJob(targetRunId, latestJob)
        try {
          const msgs = await fetchRunMessages(targetRunId, fixtureMode)
          if (
            opSeq === opSeqRef.current &&
            currentRunIdRef.current === targetRunId &&
            messagesEpochRef.current === midEpoch
          ) {
            setMessages(msgs)
          }
        } catch {
          // Polling will fetch final messages when job finishes
        }
      }
    } catch (err: unknown) {
      if (opSeq !== opSeqRef.current || currentRunIdRef.current !== targetRunId) return
      setSubmitError(err instanceof Error ? err.message : 'Could not ask question.')
    } finally {
      if (opSeq === opSeqRef.current && currentRunIdRef.current === targetRunId) {
        setSubmitting(false)
      }
    }
  }

  const handleCancelJob = async () => {
    if (!activeJob || !selectedRun || cancelling) return
    const targetRunId = selectedRun.id
    const opSeq = opSeqRef.current
    setCancelling(true)
    try {
      const cancelled = await cancelJob(activeJob.job_id, fixtureMode)
      if (opSeq !== opSeqRef.current || currentRunIdRef.current !== targetRunId) return
      await syncTerminalMessages(targetRunId, cancelled, opSeq)
    } catch (err: unknown) {
      if (opSeq !== opSeqRef.current || currentRunIdRef.current !== targetRunId) return
      setSubmitError(err instanceof Error ? err.message : 'Could not cancel worker cleanly.')
    } finally {
      if (opSeq === opSeqRef.current && currentRunIdRef.current === targetRunId) {
        setCancelling(false)
      }
    }
  }

  if (detailError && !selectedRun) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-title">Agent</h1>
            <p className="page-subtitle">Ask about the access log for this run.</p>
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
            <h1 className="page-title">Agent</h1>
            <p className="page-subtitle">Ask about the access log for this run.</p>
          </div>
        </div>
        {isMobile && <p style={{ margin: '10px 0 0', color: 'var(--ink-subtle)' }}>No runs yet</p>}
        <EmptyRunState kind="agent" onGoToTraffic={onGoToTraffic} />
      </div>
    )
  }

  if (!selectedRun) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1 className="page-title">Agent</h1>
            <p className="page-subtitle">Ask about the access log for this run.</p>
          </div>
        </div>
        <div className="skeleton-line" style={{ width: '100%', height: 56, marginTop: 20 }} />
      </div>
    )
  }

  const isRunning =
    submitting ||
    syncingResult ||
    isJobInFlight(activeJob, selectedRun.id)
  const isCancelled = activeJob !== null && activeJob.run_id === selectedRun.id && activeJob.status === 'cancelled'
  const showFinalError = finalResultError !== null && finalResultError.runId === selectedRun.id

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Agent</h1>
          <p className="page-subtitle">Ask about the access log for this run.</p>
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

      <div className="split-workspace">
        <div>
          <h2 className="section-label">Conversation</h2>

          {readError && (
            <div className="status-banner is-error" role="alert" style={{ marginBottom: 16 }}>
              <div className="status-banner-text">
                <strong>Could not load conversation</strong>
                <span>{readError}</span>
              </div>
              <button
                type="button"
                className="btn btn-primary"
                disabled={loadingMessages}
                onClick={() => setReloadNonce((n) => n + 1)}
              >
                Retry
              </button>
            </div>
          )}

          {submitError && (
            <div className="status-banner is-error" role="alert" style={{ marginBottom: 16 }}>
              <div className="status-banner-text">
                <strong>Could not reach agent</strong>
                <span>{submitError}</span>
              </div>
              {lastQuestion && (
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={isRunning}
                  onClick={() => void submitQuestion(lastQuestion)}
                >
                  Retry
                </button>
              )}
            </div>
          )}

          {loadingMessages && messages.length === 0 && !readError && (
            <div style={{ marginBottom: 18 }} role="status">
              <div style={{ fontSize: '0.94rem', color: 'var(--ink-muted)', marginBottom: 8 }}>
                Loading conversation…
              </div>
              <div className="skeleton-line" style={{ width: '100%', height: 52 }} />
            </div>
          )}

          {messages.length === 0 && !isRunning && !loadingMessages && !readError && (
            <div style={{ marginBottom: 18 }}>
              <div style={{ fontSize: '0.93rem', color: 'var(--ink-muted)', marginBottom: 10 }}>
                Ask about this run
              </div>
              <button
                type="button"
                className="agent-prompt-card"
                disabled={isRunning || selectedRun.status !== 'ready'}
                onClick={() => void submitQuestion('How many 404s are in this run?')}
              >
                <span>How many 404s are in this run?</span>
              </button>
            </div>
          )}

          <div className="agent-thread">
            {messages.map((msg, idx) => {
              if (msg.role === 'user') {
                return (
                  <div key={msg.id} className="agent-question-strip">
                    {msg.content}
                  </div>
                )
              }

              const priorUserQuestion =
                [...messages.slice(0, idx)].reverse().find((m) => m.role === 'user')?.content ??
                'How many 404s are in this run?'
              const primaryCall = msg.tool_calls?.[msg.tool_calls.length - 1] ?? null
              const verifiedCountCall =
                msg.tool_calls?.find(
                  (tc) =>
                    tc.status === 'completed' &&
                    tc.tool_name === 'count_status_codes' &&
                    typeof tc.payload?.status_404 === 'number' &&
                    typeof tc.payload?.total_requests === 'number',
                ) ?? null
              const isStale =
                msg.status === 'completed' &&
                msg.revision !== null &&
                selectedRun.revision > msg.revision

              if (msg.status === 'model_unavailable') {
                return (
                  <div key={msg.id} className="status-banner is-error" role="status">
                    <div className="status-banner-text">
                      <strong>Model unavailable</strong>
                      <span>Traffic and Checks are available. {msg.error_message ?? ''}</span>
                    </div>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={isRunning}
                      onClick={() => void submitQuestion(priorUserQuestion)}
                    >
                      Retry
                    </button>
                  </div>
                )
              }

              if (msg.status === 'timed_out') {
                return (
                  <div key={msg.id} className="status-banner is-warning" role="status">
                    <div className="status-banner-text">
                      <strong>Request timed out</strong>
                      <span>{msg.error_message ?? 'Try the same question again.'}</span>
                    </div>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={isRunning}
                      onClick={() => void submitQuestion(priorUserQuestion)}
                    >
                      Retry
                    </button>
                  </div>
                )
              }

              if (msg.status === 'failed') {
                return (
                  <div key={msg.id} className="status-banner is-error" role="status">
                    <div className="status-banner-text">
                      <strong>Tool call failed</strong>
                      <span>{msg.error_message ?? 'The worker could not read the snapshot.'}</span>
                    </div>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={isRunning}
                      onClick={() => void submitQuestion(priorUserQuestion)}
                    >
                      Retry
                    </button>
                  </div>
                )
              }

              return (
                <div key={msg.id}>
                  <div className="agent-answer-block">
                    <p className="agent-answer-text">{renderFormattedAnswer(msg.content)}</p>
                    <div className="agent-answer-meta">
                      {primaryCall && (
                        <button
                          type="button"
                          className={isMobile ? 'btn btn-outline' : 'link-action'}
                          style={isMobile ? { minHeight: 44 } : undefined}
                          onClick={() => {
                            if (isMobile) setSheetCall(primaryCall)
                            else setActiveCall(primaryCall)
                          }}
                        >
                          View tool call →
                        </button>
                      )}
                      {msg.revision !== null && (
                        <span style={{ fontSize: '0.88rem', color: 'var(--ink-muted)' }}>
                          Revision {msg.revision}
                        </span>
                      )}
                      {verifiedCountCall && (
                        <span
                          data-testid="verified-tool-counts"
                          style={{
                            fontSize: '0.84rem',
                            color: 'var(--ink-muted)',
                            background: 'var(--surface-subtle, #f3f5f8)',
                            borderRadius: 999,
                            padding: '2px 9px',
                          }}
                        >
                          Tool counts: {String(verifiedCountCall.payload?.status_404)} 404 /{' '}
                          {String(verifiedCountCall.payload?.total_requests)} total
                        </span>
                      )}
                    </div>
                  </div>

                  {isStale && (
                    <div className="agent-stale-banner" role="status">
                      <span>
                        <strong>Answer uses revision {msg.revision}</strong> · {selectedRun.label} now
                        has {selectedRun.request_count} requests.
                      </span>
                      <button
                        type="button"
                        className="btn btn-primary"
                        style={{ minHeight: 44, padding: '0 14px', fontSize: '0.86rem' }}
                        disabled={isRunning}
                        onClick={() => void submitQuestion(priorUserQuestion)}
                      >
                        Ask again
                      </button>
                    </div>
                  )}
                </div>
              )
            })}

            {isRunning && (
              <div className="status-banner" role="status">
                <div className="status-banner-text">
                  <strong>{cancelling ? 'Cancelling' : 'Calling MCP tool'}</strong>
                  <span>{activeJob?.progress_message || 'Checking the selected log snapshot.'}</span>
                </div>
                <button
                  type="button"
                  className="btn btn-outline"
                  disabled={cancelling}
                  onClick={() => void handleCancelJob()}
                >
                  {cancelling ? 'Cancelling…' : 'Cancel'}
                </button>
              </div>
            )}

            {isCancelled && (
              <div className="status-banner" role="status">
                <div className="status-banner-text">
                  <strong>Cancelled</strong>
                  <span>{activeJob?.progress_message || 'Question cancelled and worker cleaned up.'}</span>
                </div>
              </div>
            )}

            {showFinalError && finalResultError && (
              <div className="status-banner is-error" role="alert">
                <div className="status-banner-text">
                  <strong>Could not load final response</strong>
                  <span>{finalResultError.message}</span>
                </div>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() =>
                    void syncTerminalMessages(
                      finalResultError.runId,
                      finalResultError.job,
                      opSeqRef.current,
                    )
                  }
                >
                  Reload result
                </button>
              </div>
            )}
          </div>

          <form
            className="agent-composer"
            onSubmit={(e) => {
              e.preventDefault()
              void submitQuestion(input)
            }}
          >
            <input
              type="text"
              aria-label="Ask about this run"
              placeholder="Ask about this run..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isRunning || selectedRun.status !== 'ready'}
            />
            <button
              type="submit"
              className="btn btn-primary"
              disabled={isRunning || !input.trim() || selectedRun.status !== 'ready'}
            >
              Send
            </button>
          </form>
        </div>

        {/* Desktop Tool call Inspector */}
        <aside className="inspector-panel inspector-desktop-only" aria-label="Tool call inspector">
          <div className="inspector-head">
            <h2>Tool call</h2>
            {activeCall && (
              <span
                className={`status-pill ${
                  activeCall.status === 'completed'
                    ? 'is-info'
                    : activeCall.status === 'timed_out'
                      ? 'is-warn'
                      : 'is-error'
                }`}
              >
                {activeCall.status === 'completed'
                  ? 'Completed'
                  : activeCall.status === 'timed_out'
                    ? 'Timed out'
                    : 'Failed'}
              </span>
            )}
          </div>

          {activeCall ? (
            <dl className="inspector-dl">
              <div className="inspector-row">
                <dt>Tool</dt>
                <dd className="mono">{activeCall.tool_name}</dd>
              </div>
              <div className="inspector-row">
                <dt>Run</dt>
                <dd>{selectedRun.label}</dd>
              </div>
              <div className="inspector-row">
                <dt>Snapshot</dt>
                <dd>Revision {activeCall.revision}</dd>
              </div>
              <div className="inspector-row">
                <dt>Output</dt>
                <dd className="mono">{formatToolOutput(activeCall)}</dd>
              </div>
            </dl>
          ) : (
            <p style={{ margin: '14px 0 4px', fontSize: '0.92rem', color: 'var(--ink-muted)' }}>
              Ask a question to inspect the sandboxed MCP tool call and pinned snapshot revision.
            </p>
          )}
        </aside>
      </div>

      {/* Mobile Tool Call Bottom Sheet */}
      <BottomSheet
        open={sheetCall !== null}
        title="Tool call"
        onClose={() => setSheetCall(null)}
      >
        {sheetCall && (
          <dl className="detail-dl">
            <dt>Tool</dt>
            <dd className="mono">{sheetCall.tool_name}</dd>
            <dt>Run</dt>
            <dd>{selectedRun.label}</dd>
            <dt>Snapshot</dt>
            <dd>Revision {sheetCall.revision}</dd>
            <dt>Output</dt>
            <dd style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              {sheetCall.tool_name === 'count_status_codes' && sheetCall.payload ? (
                <>
                  <span className="status-pill is-200">
                    HTTP 200: {String(sheetCall.payload.status_200 ?? 0)}
                  </span>
                  <span className="status-pill is-404">
                    HTTP 404: {String(sheetCall.payload.status_404 ?? 0)}
                  </span>
                </>
              ) : (
                <span className="mono">{formatToolOutput(sheetCall)}</span>
              )}
            </dd>
          </dl>
        )}
      </BottomSheet>
    </div>
  )
}
