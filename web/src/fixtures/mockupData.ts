import {
  AgentMessage,
  CheckResult,
  JobRecord,
  LogQueryResponse,
  RequestRecord,
  Run,
  RunDetailResponse,
  RunListResponse,
  ToolResult,
} from '../types'

const RUN_014_ID = '00000000-0000-4000-8000-000000000014'
const RUN_013_ID = '00000000-0000-4000-8000-000000000013'
const RUN_012_ID = '00000000-0000-4000-8000-000000000012'

const MOCKUP_PATTERN: Array<[string, number, string]> = [
  ['/demo', 200, '14:31:59'],
  ['/demo', 200, '14:32:00'],
  ['/demo', 200, '14:32:01'],
  ['/missing', 404, '14:32:02'],
  ['/demo', 200, '14:32:03'],
  ['/demo', 200, '14:32:04'],
  ['/missing', 404, '14:32:05'],
  ['/demo', 200, '14:32:06'],
  ['/demo', 200, '14:32:07'],
  ['/missing', 404, '14:32:08'],
]

function buildFixtureRequests(runId: string, count: number): RequestRecord[] {
  const list: RequestRecord[] = []
  for (let i = 0; i < count; i++) {
    const seq = i + 1
    const template = MOCKUP_PATTERN[i] ?? ['/missing', 404, '14:32:09']
    const [path, status, time] = template
    list.push({
      id: `${runId}-req-${String(seq).padStart(2, '0')}`,
      run_id: runId,
      seq,
      timestamp: `2026-11-18T${time}Z`,
      display_time: time,
      method: 'GET',
      path,
      status,
      status_text: status === 404 ? '404 Not Found' : '200 OK',
      bytes_sent: 30,
      request_time: 0.001,
    })
  }
  return list
}

const BASE_FIXTURE_RUNS: Run[] = Array.from({ length: 24 }, (_, idx) => {
  const num = 14 - idx
  const display = num > 0 ? num : 24 + num
  const id =
    display === 14
      ? RUN_014_ID
      : display === 13
        ? RUN_013_ID
        : display === 12
          ? RUN_012_ID
          : `00000000-0000-4000-8000-000000000${String(display).padStart(3, '0')}`
  return {
    id,
    display_number: idx === 0 ? 14 : 14 - idx,
    label: `Run ${String(display).padStart(3, '0')}`,
    created_at: '2026-11-18T14:32:08Z',
    status: 'ready',
    revision: 1,
    snapshot_id: `${id}-snap-1`,
    request_count: 10,
    expected_count: 10,
    logged_count: 10,
    ok_count: 7,
    not_found_count: 3,
    is_current: idx === 0,
    error_code: null,
    error_message: null,
  }
})

export type FixtureMode = 'none' | 'mockup' | 'empty' | 'pending' | 'error' | 'readonly' | 'stale'

export function getFixtureMode(): FixtureMode {
  const params = new URLSearchParams(window.location.search)
  const f = params.get('fixture')
  if (
    f === 'mockup' ||
    f === 'empty' ||
    f === 'pending' ||
    f === 'error' ||
    f === 'readonly' ||
    f === 'stale'
  ) {
    return f
  }
  return 'none'
}

interface FixtureState {
  runs: Run[]
  extra404ByRun: Record<string, number>
  messagesByRun: Record<string, AgentMessage[]>
  checksByRun: Record<string, CheckResult[]>
  jobs: Record<string, JobRecord>
}

const stateByMode = new Map<FixtureMode, FixtureState>()

function defaultMessagesForRun(run: Run): AgentMessage[] {
  const nf = run.not_found_count
  const total = run.request_count
  const ok = run.ok_count
  const tc: ToolResult = {
    call_id: `fixture-call-${run.id}-${run.revision}`,
    tool_name: 'count_status_codes',
    args: { run_id: run.id },
    run_id: run.id,
    snapshot_id: run.snapshot_id ?? `${run.id}-snap-1`,
    revision: run.revision,
    payload: { total_requests: total, status_200: ok, status_404: nf },
    duration_ms: 145,
    status: 'completed',
    error_code: null,
    error_message: null,
    created_at: '2026-11-18T14:32:10Z',
  }
  return [
    {
      id: `fixture-msg-user-${run.id}-1`,
      run_id: run.id,
      role: 'user',
      content: 'How many 404s are in this run?',
      snapshot_id: tc.snapshot_id,
      revision: 1,
      tool_call_ids: [],
      tool_calls: [],
      status: 'completed',
      error_code: null,
      error_message: null,
      created_at: '2026-11-18T14:32:09Z',
    },
    {
      id: `fixture-msg-asst-${run.id}-1`,
      run_id: run.id,
      role: 'assistant',
      content: `${nf} of ${total} requests returned HTTP 404.`,
      snapshot_id: tc.snapshot_id,
      revision: 1,
      tool_call_ids: [tc.call_id],
      tool_calls: [tc],
      status: 'completed',
      error_code: null,
      error_message: null,
      created_at: '2026-11-18T14:32:10Z',
    },
  ]
}

function defaultChecksForRun(run: Run): CheckResult[] {
  const snapId = run.snapshot_id ?? `${run.id}-snap-1`
  const rev = Math.max(1, run.revision)
  return [
    {
      check_id: `fixture-chk-read-${snapId}`,
      run_id: run.id,
      snapshot_id: snapId,
      revision: rev,
      name: 'read_log',
      title: 'Read log',
      policy_version: 'sandbox-policy-v1',
      expected: 'Allowed',
      observed: 'Allowed',
      verdict: 'pass',
      control_result: { kind: 'host_snapshot_digest', expected_sha256: 'fixture', observed_sha256: 'fixture' },
      evidence: { summary: 'Worker read snapshot with matching SHA-256.', uid: 10001 },
      exit_code: 0,
      stderr_snippet: '',
      created_at: '2026-11-18T14:32:12Z',
    },
    {
      check_id: `fixture-chk-write-${snapId}`,
      run_id: run.id,
      snapshot_id: snapId,
      revision: rev,
      name: 'write_log',
      title: 'Write log',
      policy_version: 'sandbox-policy-v1',
      expected: 'Blocked',
      observed: 'Blocked',
      verdict: 'pass',
      control_result: { kind: 'writable_copy_control', write_succeeded: true, marker_found: true, summary: 'Write succeeded · Marker found' },
      evidence: {
        read_only_mount: 'Write rejected · Hash unchanged',
        writable_control: 'Write succeeded · Marker found',
      },
      exit_code: 1,
      stderr_snippet: '',
      created_at: '2026-11-18T14:32:13Z',
    },
    {
      check_id: `fixture-chk-net-${snapId}`,
      run_id: run.id,
      snapshot_id: snapId,
      revision: rev,
      name: 'reach_test_service',
      title: 'Reach test service',
      policy_version: 'sandbox-policy-v1',
      expected: 'Blocked',
      observed: 'Blocked',
      verdict: 'pass',
      control_result: { kind: 'network_control', reachable: true, status_code: 200, summary: 'Control reached test-service (HTTP 200)' },
      evidence: { sandbox_network: 'none', sandbox_blocked: true, control_network: 'sandbox_lab_net', control_status: 200 },
      exit_code: 1,
      stderr_snippet: '',
      created_at: '2026-11-18T14:32:14Z',
    },
  ]
}

function getOrInitState(mode: FixtureMode): FixtureState {
  const cached = stateByMode.get(mode)
  if (cached) return cached

  const runs: Run[] =
    mode === 'empty'
      ? []
      : BASE_FIXTURE_RUNS.map((r, i) => {
          if (mode === 'pending' && i === 0) {
            return {
              ...r,
              status: 'pending_log' as const,
              revision: 0,
              snapshot_id: null,
              request_count: 8,
              logged_count: 8,
              ok_count: 6,
              not_found_count: 2,
              error_code: 'LOG_INCOMPLETE',
              error_message: '8 of 10 requests logged.',
            }
          }
          if (mode === 'error' && i === 0) {
            return {
              ...r,
              status: 'error' as const,
              revision: 0,
              snapshot_id: null,
              request_count: 0,
              logged_count: 0,
              ok_count: 0,
              not_found_count: 0,
              error_code: 'NGINX_UNAVAILABLE',
              error_message: 'Nginx is unavailable.',
            }
          }
          if (mode === 'stale' && i === 0) {
            return {
              ...r,
              revision: 2,
              snapshot_id: `${r.id}-snap-2`,
              request_count: 11,
              expected_count: 11,
              logged_count: 11,
              ok_count: 7,
              not_found_count: 4,
            }
          }
          return { ...r }
        })

  const state: FixtureState = {
    runs,
    extra404ByRun: {},
    messagesByRun: {},
    checksByRun: {},
    jobs: {},
  }
  stateByMode.set(mode, state)
  return state
}

export function getFixtureRuns(
  mode: FixtureMode,
  search: string,
  page: number,
  pageSize: number,
): RunListResponse {
  const st = getOrInitState(mode)
  const q = search.trim().toLowerCase()
  const filtered = q ? st.runs.filter((r) => r.label.toLowerCase().includes(q)) : st.runs
  const start = (page - 1) * pageSize
  const current = st.runs.find((r) => r.is_current) ?? st.runs[0] ?? null
  return {
    items: filtered.slice(start, start + pageSize),
    total: filtered.length,
    page,
    page_size: pageSize,
    current_run_id: current?.id ?? null,
  }
}

export function getFixtureRunDetail(
  mode: FixtureMode,
  runId: string,
  includeRequests = false,
): RunDetailResponse {
  const st = getOrInitState(mode)
  const target = st.runs.find((r) => r.id === runId) ?? st.runs[0]
  if (!target) {
    throw new Error('No fixture runs')
  }
  const count = target.status === 'pending_log' ? 8 : target.status === 'error' ? 0 : target.request_count
  const requests = includeRequests ? buildFixtureRequests(target.id, count) : []
  return {
    run: target,
    snapshot: target.snapshot_id
      ? {
          id: target.snapshot_id,
          run_id: target.id,
          revision: target.revision,
          created_at: target.created_at,
          sha256: 'fixture-sha256-014',
          request_count: target.request_count,
          ok_count: target.ok_count,
          not_found_count: target.not_found_count,
          file_path: 'fixture',
        }
      : null,
    requests,
  }
}

export function createFixtureRun(mode: FixtureMode): Run {
  const st = getOrInitState(mode)
  const nextNum = st.runs.length > 0 ? Math.max(...st.runs.map((r) => r.display_number)) + 1 : 1
  for (const r of st.runs) r.is_current = false
  const id = `00000000-0000-4000-8000-${String(nextNum).padStart(12, '0')}`
  const created: Run = {
    id,
    display_number: nextNum,
    label: `Run ${String(nextNum).padStart(3, '0')}`,
    created_at: new Date().toISOString(),
    status: 'ready',
    revision: 1,
    snapshot_id: `${id}-snap-1`,
    request_count: 10,
    expected_count: 10,
    logged_count: 10,
    ok_count: 7,
    not_found_count: 3,
    is_current: true,
    error_code: null,
    error_message: null,
  }
  st.runs.unshift(created)
  return { ...created }
}

export function refreshFixtureRun(mode: FixtureMode, runId: string): Run {
  const st = getOrInitState(mode)
  const target = st.runs.find((r) => r.id === runId) ?? st.runs[0]
  if (!target) throw new Error('Run not found in fixture')
  target.status = 'ready'
  target.revision = Math.max(1, target.revision)
  target.snapshot_id = target.snapshot_id ?? `${target.id}-snap-${target.revision}`
  target.request_count = target.expected_count
  target.logged_count = target.expected_count
  target.ok_count = 7
  target.not_found_count = target.expected_count - 7
  target.error_code = null
  target.error_message = null
  return { ...target }
}

export function appendFixture404(mode: FixtureMode, runId: string): Run {
  const st = getOrInitState(mode)
  const target = st.runs.find((r) => r.id === runId)
  if (!target) throw new Error('Run not found in fixture')
  if (!target.is_current) {
    throw new Error('Historical runs are read-only.')
  }
  target.revision += 1
  target.snapshot_id = `${target.id}-snap-${target.revision}`
  target.request_count += 1
  target.expected_count += 1
  target.logged_count += 1
  target.not_found_count += 1
  target.status = 'ready'
  target.error_code = null
  target.error_message = null
  delete st.checksByRun[target.id]
  return { ...target }
}

export function getFixtureLogs(
  mode: FixtureMode,
  runId: string,
  opts: {
    status?: string
    path?: string
    cursor?: string | null
    limit?: number
    locateRequestId?: string | null
    order?: 'asc' | 'desc'
  },
): LogQueryResponse {
  const detail = getFixtureRunDetail(mode, runId, true)
  const ordered = opts.order === 'asc' ? [...detail.requests] : [...detail.requests].reverse()
  const statusNorm = (opts.status ?? 'all').toLowerCase()
  const pathNorm = (opts.path ?? '').trim().toLowerCase()
  const filtered = ordered.filter((r) => {
    if (statusNorm !== 'all' && String(r.status) !== statusNorm) return false
    if (pathNorm && !r.path.toLowerCase().includes(pathNorm)) return false
    return true
  })
  const limit = opts.limit ?? 5
  const located = opts.locateRequestId ? ordered.find((r) => r.id === opts.locateRequestId) ?? null : null
  let offset = opts.cursor ? Number(opts.cursor) || 0 : 0
  let hidden = false
  if (!opts.cursor && opts.locateRequestId) {
    const idx = filtered.findIndex((r) => r.id === opts.locateRequestId)
    if (idx >= 0) offset = Math.floor(idx / limit) * limit
    else if (located) hidden = true
  }
  return {
    run_id: detail.run.id,
    snapshot_id: detail.snapshot?.id ?? null,
    revision: detail.run.revision,
    total_in_snapshot: ordered.length,
    total_matching: filtered.length,
    items: filtered.slice(offset, offset + limit),
    next_cursor: offset + limit < filtered.length ? String(offset + limit) : null,
    prev_cursor: offset > 0 ? String(Math.max(0, offset - limit)) : null,
    offset,
    limit,
    located_request: located,
    located_hidden_by_filter: hidden,
  }
}

export function getFixtureMessages(runId: string, mode: FixtureMode = getFixtureMode()): AgentMessage[] {
  const st = getOrInitState(mode)
  if (!st.messagesByRun[runId]) {
    const run = st.runs.find((r) => r.id === runId) ?? BASE_FIXTURE_RUNS[0]
    st.messagesByRun[runId] = defaultMessagesForRun(run)
  }
  return st.messagesByRun[runId]
}

export function askFixtureQuestion(mode: FixtureMode, runId: string, question: string): JobRecord {
  const st = getOrInitState(mode)
  const run = st.runs.find((r) => r.id === runId)
  if (!run) throw new Error('Run not found in fixture')
  const msgs = getFixtureMessages(runId, mode)
  const now = new Date().toISOString()
  const snapId = run.snapshot_id ?? `${run.id}-snap-${run.revision}`
  const tc: ToolResult = {
    call_id: `fixture-call-${Date.now()}`,
    tool_name: 'count_status_codes',
    args: { run_id: run.id },
    run_id: run.id,
    snapshot_id: snapId,
    revision: run.revision,
    payload: { total_requests: run.request_count, status_200: run.ok_count, status_404: run.not_found_count },
    duration_ms: 112,
    status: 'completed',
    error_code: null,
    error_message: null,
    created_at: now,
  }
  const userMsg: AgentMessage = {
    id: `fixture-u-${Date.now()}`,
    run_id: runId,
    role: 'user',
    content: question,
    snapshot_id: snapId,
    revision: run.revision,
    tool_call_ids: [],
    tool_calls: [],
    status: 'completed',
    error_code: null,
    error_message: null,
    created_at: now,
  }
  const asstMsg: AgentMessage = {
    id: `fixture-a-${Date.now() + 1}`,
    run_id: runId,
    role: 'assistant',
    content: `${run.not_found_count} of ${run.request_count} requests returned HTTP 404 (Revision ${run.revision}).`,
    snapshot_id: snapId,
    revision: run.revision,
    tool_call_ids: [tc.call_id],
    tool_calls: [tc],
    status: 'completed',
    error_code: null,
    error_message: null,
    created_at: now,
  }
  st.messagesByRun[runId] = [...msgs, userMsg, asstMsg]
  const job: JobRecord = {
    job_id: `fixture-job-${Date.now()}`,
    kind: 'agent_question',
    run_id: runId,
    status: 'completed',
    progress_message: 'Completed in mockup fixture.',
    result_id: asstMsg.id,
    error_code: null,
    error_message: null,
    created_at: now,
    updated_at: now,
  }
  st.jobs[job.job_id] = job
  return job
}

export function fetchFixtureJob(mode: FixtureMode, jobId: string): JobRecord {
  const st = getOrInitState(mode)
  const found = st.jobs[jobId]
  if (!found) {
    const now = new Date().toISOString()
    return {
      job_id: jobId,
      kind: 'agent_question',
      run_id: null,
      status: 'completed',
      progress_message: 'Completed',
      result_id: null,
      error_code: null,
      error_message: null,
      created_at: now,
      updated_at: now,
    }
  }
  return found
}

export function cancelFixtureJob(mode: FixtureMode, jobId: string): JobRecord {
  const st = getOrInitState(mode)
  const job = fetchFixtureJob(mode, jobId)
  job.status = 'cancelled'
  job.progress_message = 'Cancelled in mockup fixture.'
  st.jobs[jobId] = job
  return job
}

export function getFixtureChecks(runId: string, mode: FixtureMode = getFixtureMode()): CheckResult[] {
  const st = getOrInitState(mode)
  if (!st.checksByRun[runId]) {
    const run = st.runs.find((r) => r.id === runId) ?? BASE_FIXTURE_RUNS[0]
    if (run.status !== 'ready') return []
    st.checksByRun[runId] = defaultChecksForRun(run)
  }
  return st.checksByRun[runId]
}

export function executeFixtureChecks(mode: FixtureMode, runId: string): CheckResult[] {
  const st = getOrInitState(mode)
  const run = st.runs.find((r) => r.id === runId) ?? BASE_FIXTURE_RUNS[0]
  st.checksByRun[runId] = defaultChecksForRun(run)
  return st.checksByRun[runId]
}
