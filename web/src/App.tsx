import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  append404,
  createRun,
  fetchRunDetail,
  fetchRuns,
  refreshRun,
} from './api'
import { HeaderNav } from './components/HeaderNav'
import { getFixtureMode } from './fixtures/mockupData'
import { AccessLogPage } from './pages/AccessLogPage'
import { AgentPage } from './pages/AgentPage'
import { ChecksPage } from './pages/ChecksPage'
import { TrafficPage } from './pages/TrafficPage'
import { JobRecord, Run, TabId } from './types'

function makeKey(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function parseInitialTab(): TabId {
  const params = new URLSearchParams(window.location.search)
  const tab = params.get('tab')
  if (tab === 'agent' || tab === 'checks' || tab === 'logs' || tab === 'traffic') {
    return tab
  }
  return 'traffic'
}

export function App() {
  const fixtureMode = useMemo(() => getFixtureMode(), [])
  const [activeTab, setActiveTab] = useState<TabId>(parseInitialTab)
  const [isMobile, setIsMobile] = useState<boolean>(() =>
    typeof window !== 'undefined' ? window.innerWidth <= 768 : false,
  )

  const [runs, setRuns] = useState<Run[]>([])
  const [currentRunId, setCurrentRunId] = useState<string | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('run')
  })
  const [selectedRun, setSelectedRun] = useState<Run | null>(null)
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(() => {
    const params = new URLSearchParams(window.location.search)
    return params.get('req')
  })
  const [explicitSelectionByRun, setExplicitSelectionByRun] = useState<Record<string, boolean>>(() => {
    const params = new URLSearchParams(window.location.search)
    const runParam = params.get('run')
    const reqParam = params.get('req')
    return runParam && reqParam ? { [runParam]: true } : {}
  })
  const [agentJobsByRun, setAgentJobsByRun] = useState<Record<string, JobRecord>>({})

  const [loadingDetail, setLoadingDetail] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [adding404RunId, setAdding404RunId] = useState<string | null>(null)
  const [refreshingRunId, setRefreshingRunId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [lastFailedAction, setLastFailedAction] = useState<{
    kind: 'create' | 'add404' | 'refresh'
    runId: string | null
    generation: number
  } | null>(null)
  const [runSelectorOpen, setRunSelectorOpen] = useState(false)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [refreshNonce, setRefreshNonce] = useState(0)

  const createInFlightRef = useRef(false)
  const add404InFlightRunsRef = useRef<Set<string>>(new Set())
  const refreshInFlightRunsRef = useRef<Set<string>>(new Set())
  const createKeyRef = useRef<string | null>(null)
  const add404KeysByRunRef = useRef<Record<string, string>>({})
  const detailFetchCounterRef = useRef(0)
  const selectionGenRef = useRef(0)
  const selectedRunIdRef = useRef<string | null>(selectedRunId)
  selectedRunIdRef.current = selectedRunId

  const selectRun = useCallback((runId: string | null) => {
    selectionGenRef.current += 1
    selectedRunIdRef.current = runId
    setSelectedRunId(runId)
    setSelectedRun(null)
    setDetailError(null)
    setActionError(null)
    setLastFailedAction(null)
    setSelectedRequestId(null)
    if (runId) {
      setExplicitSelectionByRun((prev) => {
        if (!prev[runId]) return prev
        const next = { ...prev }
        delete next[runId]
        return next
      })
    }
  }, [])

  const handleSelectRequest = useCallback(
    (reqId: string | null, explicit = true) => {
      setSelectedRequestId(reqId)
      const targetRunId = selectedRunIdRef.current
      if (explicit && targetRunId && reqId) {
        setExplicitSelectionByRun((prev) => (prev[targetRunId] ? prev : { ...prev, [targetRunId]: true }))
      }
    },
    [],
  )

  const handleTrackedJobChange = useCallback((runId: string, job: JobRecord | null) => {
    setAgentJobsByRun((prev) => {
      if (!job) {
        if (!(runId in prev)) return prev
        const next = { ...prev }
        delete next[runId]
        return next
      }
      return { ...prev, [runId]: job }
    })
  }, [])

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)')
    const onChange = () => setIsMobile(mq.matches)
    onChange()
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // Sync URL query params for reload/deep-link resilience
  useEffect(() => {
    const url = new URL(window.location.href)
    url.searchParams.set('tab', activeTab)
    if (selectedRunId) url.searchParams.set('run', selectedRunId)
    else url.searchParams.delete('run')
    if (selectedRequestId) url.searchParams.set('req', selectedRequestId)
    else url.searchParams.delete('req')
    window.history.replaceState({}, '', url.toString())
  }, [activeTab, selectedRunId, selectedRequestId])

  const loadRunsSummary = useCallback(async () => {
    const res = await fetchRuns('', 1, 25, fixtureMode)
    setRuns(res.items)
    setCurrentRunId(res.current_run_id)
    setSelectedRunId((prev) => {
      // Preserve explicit/deep-linked runId even if outside the first 25 runs.
      if (prev) return prev
      if (fixtureMode === 'readonly' && res.items[1]) return res.items[1].id
      return res.current_run_id ?? res.items[0]?.id ?? null
    })
  }, [fixtureMode])

  useEffect(() => {
    loadRunsSummary().catch(() => {
      // Keep UI operable even if initial summary call fails
    })
  }, [loadRunsSummary, refreshNonce])

  const loadRunDetail = useCallback(
    async (runId: string) => {
      const token = ++detailFetchCounterRef.current
      const originGen = selectionGenRef.current
      setLoadingDetail(true)
      setDetailError(null)
      try {
        const detail = await fetchRunDetail(runId, fixtureMode, false)
        // Guard against stale responses when switching runs quickly
        if (
          token !== detailFetchCounterRef.current ||
          originGen !== selectionGenRef.current ||
          selectedRunIdRef.current !== runId
        ) {
          return
        }
        setSelectedRun(detail.run)
        setDetailError(null)
      } catch (err: unknown) {
        if (
          token !== detailFetchCounterRef.current ||
          originGen !== selectionGenRef.current ||
          selectedRunIdRef.current !== runId
        ) {
          return
        }
        setSelectedRun(null)
        setDetailError(err instanceof Error ? err.message : 'Could not load run details.')
      } finally {
        if (token === detailFetchCounterRef.current && originGen === selectionGenRef.current) {
          setLoadingDetail(false)
        }
      }
    },
    [fixtureMode],
  )

  useEffect(() => {
    if (!selectedRunId) {
      setSelectedRun(null)
      setDetailError(null)
      return
    }
    loadRunDetail(selectedRunId).catch(() => {
      // Errors handled inside loadRunDetail
    })
  }, [selectedRunId, loadRunDetail])

  // Hard invariant: displayed run must match selectedRunId
  const displayedRun = selectedRun && selectedRun.id === selectedRunId ? selectedRun : null

  const handleCreateRun = async () => {
    if (createInFlightRef.current) return
    createInFlightRef.current = true
    const originGen = selectionGenRef.current
    setCreating(true)
    setActionError(null)
    setDetailError(null)
    setLastFailedAction(null)
    if (!createKeyRef.current) {
      createKeyRef.current = makeKey('create')
    }
    try {
      const created = await createRun(createKeyRef.current, fixtureMode)
      if (created.status !== 'error') {
        createKeyRef.current = null
      }
      setRefreshNonce((n) => n + 1)
      // Switch to created run only if user did not change run selection while creating
      if (selectionGenRef.current === originGen) {
        selectionGenRef.current += 1
        selectedRunIdRef.current = created.id
        setSelectedRunId(created.id)
        setSelectedRun(created)
        setSelectedRequestId(null)
        if (created.status === 'error') {
          setActionError(created.error_message ?? 'Nginx is unavailable.')
          setLastFailedAction({ kind: 'create', runId: null, generation: selectionGenRef.current })
        }
      }
    } catch (err: unknown) {
      if (selectionGenRef.current === originGen) {
        setActionError(err instanceof Error ? err.message : 'Nginx is unavailable.')
        setLastFailedAction({ kind: 'create', runId: null, generation: originGen })
      }
    } finally {
      createInFlightRef.current = false
      setCreating(false)
    }
  }

  const handleAdd404 = async (explicitRunId?: string, forceFreshKey = false) => {
    const originRunId = explicitRunId ?? displayedRun?.id
    if (!originRunId || add404InFlightRunsRef.current.has(originRunId)) return
    add404InFlightRunsRef.current.add(originRunId)
    const originGen = selectionGenRef.current
    setAdding404RunId(originRunId)
    setActionError(null)
    setLastFailedAction(null)
    if (forceFreshKey || !add404KeysByRunRef.current[originRunId]) {
      add404KeysByRunRef.current[originRunId] = makeKey('add404')
    }
    const key = add404KeysByRunRef.current[originRunId]
    try {
      const updated = await append404(originRunId, key, fixtureMode)
      if (updated.status !== 'error') {
        delete add404KeysByRunRef.current[originRunId]
      }
      setRefreshNonce((n) => n + 1)
      if (
        selectionGenRef.current === originGen &&
        selectedRunIdRef.current === originRunId &&
        updated.id === originRunId
      ) {
        setSelectedRun(updated)
        setDetailError(null)
        if (updated.status === 'error') {
          setActionError(updated.error_message ?? 'Nginx is unavailable.')
          setLastFailedAction({ kind: 'add404', runId: originRunId, generation: originGen })
        }
      }
    } catch (err: unknown) {
      if (selectionGenRef.current === originGen && selectedRunIdRef.current === originRunId) {
        setActionError(err instanceof Error ? err.message : 'Could not add 404 request.')
        setLastFailedAction({ kind: 'add404', runId: originRunId, generation: originGen })
      }
    } finally {
      add404InFlightRunsRef.current.delete(originRunId)
      setAdding404RunId((cur) => (cur === originRunId ? null : cur))
    }
  }

  const handleRefreshRun = async (explicitRunId?: string) => {
    const originRunId = explicitRunId ?? displayedRun?.id
    if (!originRunId || refreshInFlightRunsRef.current.has(originRunId)) return
    refreshInFlightRunsRef.current.add(originRunId)
    const originGen = selectionGenRef.current
    setRefreshingRunId(originRunId)
    setActionError(null)
    setLastFailedAction(null)
    try {
      const updated = await refreshRun(originRunId, fixtureMode)
      setRefreshNonce((n) => n + 1)
      if (
        selectionGenRef.current === originGen &&
        selectedRunIdRef.current === originRunId &&
        updated.id === originRunId
      ) {
        setSelectedRun(updated)
        setDetailError(null)
        if (updated.status === 'error') {
          setActionError(updated.error_message ?? 'Could not refresh run.')
          setLastFailedAction({ kind: 'refresh', runId: originRunId, generation: originGen })
        }
      }
    } catch (err: unknown) {
      if (selectionGenRef.current === originGen && selectedRunIdRef.current === originRunId) {
        setActionError(err instanceof Error ? err.message : 'Could not refresh run.')
        setLastFailedAction({ kind: 'refresh', runId: originRunId, generation: originGen })
      }
    } finally {
      refreshInFlightRunsRef.current.delete(originRunId)
      setRefreshingRunId((cur) => (cur === originRunId ? null : cur))
    }
  }

  const handleRetryAction = () => {
    // Only retry failed action scoped to active run & selection generation
    if (lastFailedAction?.kind === 'add404') {
      if (
        lastFailedAction.runId &&
        lastFailedAction.runId === selectedRunIdRef.current &&
        lastFailedAction.generation === selectionGenRef.current
      ) {
        void handleAdd404(lastFailedAction.runId, false)
      }
      return
    }
    if (lastFailedAction?.kind === 'refresh') {
      if (
        lastFailedAction.runId &&
        lastFailedAction.runId === selectedRunIdRef.current &&
        lastFailedAction.generation === selectionGenRef.current
      ) {
        void handleRefreshRun(lastFailedAction.runId)
      }
      return
    }
    if (displayedRun?.status === 'pending_log' || displayedRun?.status === 'error') {
      void handleRefreshRun(displayedRun.id)
      return
    }
    if (!createKeyRef.current) createKeyRef.current = makeKey('create')
    void handleCreateRun()
  }

  const currentRun =
    runs.find((r) => r.id === currentRunId) ?? (displayedRun?.is_current ? displayedRun : null)

  return (
    <div className="app-shell">
      <HeaderNav
        activeTab={activeTab}
        onNavigate={(tab) => {
          setRunSelectorOpen(false)
          setActiveTab(tab)
        }}
        selectedRun={displayedRun}
        hasRuns={runs.length > 0 || displayedRun !== null}
        isMobile={isMobile}
        runSelectorOpen={runSelectorOpen}
        onToggleRunSelector={() => {
          setMobileNavOpen(false)
          setRunSelectorOpen((v) => !v)
        }}
        onCloseRunSelector={() => setRunSelectorOpen(false)}
        mobileNavOpen={mobileNavOpen}
        onOpenMobileNav={() => {
          setRunSelectorOpen(false)
          setMobileNavOpen(true)
        }}
        onCloseMobileNav={() => setMobileNavOpen(false)}
        onSelectRun={(runId) => {
          selectRun(runId)
        }}
        fixtureMode={fixtureMode}
        refreshNonce={refreshNonce}
      />

      <main className="main-content">
        <div className="container">
          {activeTab === 'traffic' && (
            <TrafficPage
              selectedRun={displayedRun}
              selectedRunId={selectedRunId}
              currentRun={currentRun}
              selectedRequestId={selectedRequestId}
              hasExplicitSelection={Boolean(
                displayedRun && explicitSelectionByRun[displayedRun.id],
              )}
              onSelectRequest={(reqId, explicit = true) => handleSelectRequest(reqId, explicit)}
              onOpenInAccessLog={(reqId) => {
                handleSelectRequest(reqId, true)
                setActiveTab('logs')
              }}
              onCreateRun={() => {
                createKeyRef.current = makeKey('create')
                void handleCreateRun()
              }}
              onRefreshRun={() => void handleRefreshRun()}
              onAdd404={() => {
                if (displayedRun) {
                  void handleAdd404(displayedRun.id, true)
                }
              }}
              onSwitchToCurrentRun={() => {
                if (currentRunId) {
                  selectRun(currentRunId)
                }
              }}
              onOpenRunSelector={() => setRunSelectorOpen(true)}
              creating={creating}
              adding404={Boolean(selectedRunId && adding404RunId === selectedRunId)}
              refreshing={Boolean(selectedRunId && refreshingRunId === selectedRunId)}
              actionError={actionError}
              detailError={detailError}
              onRetryDetail={() => {
                if (selectedRunId) void loadRunDetail(selectedRunId)
              }}
              onRetryAction={handleRetryAction}
              isMobile={isMobile}
              loadingDetail={loadingDetail}
              fixtureMode={fixtureMode}
              refreshNonce={refreshNonce}
            />
          )}

          {activeTab === 'agent' && (
            <AgentPage
              selectedRun={displayedRun}
              detailError={detailError}
              loadingDetail={loadingDetail}
              onRetryDetail={() => {
                if (selectedRunId) void loadRunDetail(selectedRunId)
              }}
              onGoToTraffic={() => setActiveTab('traffic')}
              onOpenRunSelector={() => setRunSelectorOpen(true)}
              trackedJob={displayedRun ? (agentJobsByRun[displayedRun.id] ?? null) : null}
              onTrackedJobChange={handleTrackedJobChange}
              isMobile={isMobile}
              fixtureMode={fixtureMode}
            />
          )}

          {activeTab === 'checks' && (
            <ChecksPage
              selectedRun={displayedRun}
              detailError={detailError}
              loadingDetail={loadingDetail}
              onRetryDetail={() => {
                if (selectedRunId) void loadRunDetail(selectedRunId)
              }}
              onGoToTraffic={() => setActiveTab('traffic')}
              onOpenRunSelector={() => setRunSelectorOpen(true)}
              isMobile={isMobile}
              fixtureMode={fixtureMode}
            />
          )}

          {activeTab === 'logs' && (
            <AccessLogPage
              selectedRun={displayedRun}
              currentRun={currentRun}
              detailError={detailError}
              loadingDetail={loadingDetail}
              onRetryDetail={() => {
                if (selectedRunId) void loadRunDetail(selectedRunId)
              }}
              onRefreshRun={() => void handleRefreshRun()}
              onSwitchToCurrentRun={() => {
                if (currentRunId) {
                  selectRun(currentRunId)
                }
              }}
              refreshingRun={Boolean(selectedRunId && refreshingRunId === selectedRunId)}
              selectedRequestId={selectedRequestId}
              onSelectRequest={(reqId) => handleSelectRequest(reqId, true)}
              onGoToTraffic={() => setActiveTab('traffic')}
              onOpenRunSelector={() => setRunSelectorOpen(true)}
              isMobile={isMobile}
              fixtureMode={fixtureMode}
            />
          )}
        </div>
      </main>
    </div>
  )
}

export default App
