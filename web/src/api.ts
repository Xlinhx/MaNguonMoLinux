import {
  appendFixture404,
  askFixtureQuestion,
  cancelFixtureJob,
  createFixtureRun,
  executeFixtureChecks,
  fetchFixtureJob,
  FixtureMode,
  getFixtureChecks,
  getFixtureLogs,
  getFixtureMessages,
  getFixtureMode,
  getFixtureRunDetail,
  getFixtureRuns,
  refreshFixtureRun,
} from './fixtures/mockupData'
import {
  AgentMessage,
  ApiErrorBody,
  CheckResult,
  JobRecord,
  LogQueryResponse,
  Run,
  RunDetailResponse,
  RunListResponse,
} from './types'

export class ApiClientError extends Error {
  code: string
  status: number

  constructor(code: string, message: string, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    let body: ApiErrorBody | null = null
    try {
      body = (await res.json()) as ApiErrorBody
    } catch {
      body = null
    }
    throw new ApiClientError(
      body?.code ?? 'HTTP_ERROR',
      body?.message ?? `Request failed (${res.status})`,
      res.status,
    )
  }
  return (await res.json()) as T
}

export async function fetchRuns(
  search = '',
  page = 1,
  pageSize = 3,
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<RunListResponse> {
  if (fixtureMode !== 'none') {
    return getFixtureRuns(fixtureMode, search, page, pageSize)
  }
  const qs = new URLSearchParams({ search, page: String(page), page_size: String(pageSize) })
  return requestJson<RunListResponse>(`/api/runs?${qs.toString()}`)
}

export async function fetchRunDetail(
  runId: string,
  fixtureMode: FixtureMode = getFixtureMode(),
  includeRequests = false,
): Promise<RunDetailResponse> {
  if (fixtureMode !== 'none') {
    return getFixtureRunDetail(fixtureMode, runId, includeRequests)
  }
  const qs = includeRequests ? 'true' : 'false'
  return requestJson<RunDetailResponse>(
    `/api/runs/${encodeURIComponent(runId)}?include_requests=${qs}`,
  )
}

export async function createRun(
  idempotencyKey: string,
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<Run> {
  if (fixtureMode !== 'none') {
    return createFixtureRun(fixtureMode)
  }
  return requestJson<Run>('/api/runs', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  })
}

export async function refreshRun(
  runId: string,
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<Run> {
  if (fixtureMode !== 'none') {
    return refreshFixtureRun(fixtureMode, runId)
  }
  return requestJson<Run>(`/api/runs/${encodeURIComponent(runId)}/refresh`, {
    method: 'POST',
  })
}

export async function append404(
  runId: string,
  idempotencyKey: string,
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<Run> {
  if (fixtureMode !== 'none') {
    return appendFixture404(fixtureMode, runId)
  }
  return requestJson<Run>(`/api/runs/${encodeURIComponent(runId)}/requests/404`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  })
}

export async function fetchRunLogs(
  runId: string,
  opts: {
    status?: string
    path?: string
    cursor?: string | null
    snapshotId?: string | null
    limit?: number
    locateRequestId?: string | null
    order?: 'asc' | 'desc'
  },
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<LogQueryResponse> {
  if (fixtureMode !== 'none') {
    return getFixtureLogs(fixtureMode, runId, opts)
  }
  const qs = new URLSearchParams()
  if (opts.status && opts.status !== 'all') qs.set('status', opts.status)
  if (opts.path) qs.set('path', opts.path)
  if (opts.cursor) qs.set('cursor', opts.cursor)
  if (opts.snapshotId) qs.set('snapshot_id', opts.snapshotId)
  if (opts.limit) qs.set('limit', String(opts.limit))
  if (opts.locateRequestId) qs.set('locate_request_id', opts.locateRequestId)
  if (opts.order) qs.set('order', opts.order)
  return requestJson<LogQueryResponse>(`/api/runs/${encodeURIComponent(runId)}/logs?${qs.toString()}`)
}

export async function fetchRunMessages(
  runId: string,
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<AgentMessage[]> {
  if (fixtureMode !== 'none') {
    return getFixtureMessages(runId, fixtureMode)
  }
  return requestJson<AgentMessage[]>(`/api/runs/${encodeURIComponent(runId)}/messages`)
}

export async function askQuestion(
  runId: string,
  question: string,
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<JobRecord> {
  if (fixtureMode !== 'none') {
    return askFixtureQuestion(fixtureMode, runId, question)
  }
  return requestJson<JobRecord>(`/api/runs/${encodeURIComponent(runId)}/questions`, {
    method: 'POST',
    body: JSON.stringify({ question }),
  })
}

export async function fetchJob(
  jobId: string,
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<JobRecord> {
  if (fixtureMode !== 'none') {
    return fetchFixtureJob(fixtureMode, jobId)
  }
  return requestJson<JobRecord>(`/api/jobs/${encodeURIComponent(jobId)}`)
}

export async function fetchLatestRunJob(
  runId: string,
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<JobRecord | null> {
  if (fixtureMode !== 'none') {
    return null
  }
  return requestJson<JobRecord | null>(`/api/runs/${encodeURIComponent(runId)}/jobs/latest`)
}

export async function cancelJob(
  jobId: string,
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<JobRecord> {
  if (fixtureMode !== 'none') {
    return cancelFixtureJob(fixtureMode, jobId)
  }
  return requestJson<JobRecord>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
  })
}

export async function fetchRunChecks(
  runId: string,
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<CheckResult[]> {
  if (fixtureMode !== 'none') {
    return getFixtureChecks(runId, fixtureMode)
  }
  return requestJson<CheckResult[]>(`/api/runs/${encodeURIComponent(runId)}/checks`)
}

export async function executeRunChecks(
  runId: string,
  fixtureMode: FixtureMode = getFixtureMode(),
): Promise<CheckResult[]> {
  if (fixtureMode !== 'none') {
    return executeFixtureChecks(fixtureMode, runId)
  }
  return requestJson<CheckResult[]>(`/api/runs/${encodeURIComponent(runId)}/checks`, {
    method: 'POST',
  })
}
