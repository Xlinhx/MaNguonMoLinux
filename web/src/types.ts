export type TabId = 'traffic' | 'agent' | 'checks' | 'logs'

export type RunStatus = 'creating' | 'pending_log' | 'ready' | 'error'
export type Verdict = 'not_run' | 'running' | 'pass' | 'fail' | 'error'

export interface Run {
  id: string
  display_number: number
  label: string
  created_at: string
  status: RunStatus
  revision: number
  snapshot_id: string | null
  request_count: number
  expected_count: number
  logged_count: number
  ok_count: number
  not_found_count: number
  is_current: boolean
  error_code: string | null
  error_message: string | null
}

export interface RunListResponse {
  items: Run[]
  total: number
  page: number
  page_size: number
  current_run_id: string | null
}

export interface Snapshot {
  id: string
  run_id: string
  revision: number
  created_at: string
  sha256: string
  request_count: number
  ok_count: number
  not_found_count: number
  file_path: string
}

export interface RequestRecord {
  id: string
  run_id: string
  seq: number
  timestamp: string
  display_time: string
  method: string
  path: string
  status: number
  status_text: string
  bytes_sent: number
  request_time: number
}

export interface RunDetailResponse {
  run: Run
  snapshot: Snapshot | null
  requests: RequestRecord[]
}

export interface LogQueryResponse {
  run_id: string
  snapshot_id: string | null
  revision: number
  total_in_snapshot: number
  total_matching: number
  items: RequestRecord[]
  next_cursor: string | null
  prev_cursor: string | null
  offset: number
  limit: number
  located_request: RequestRecord | null
  located_hidden_by_filter: boolean
}

export interface ToolResult {
  call_id: string
  tool_name: string
  args: Record<string, unknown>
  run_id: string
  snapshot_id: string
  revision: number
  payload: Record<string, unknown> | null
  duration_ms: number
  status: 'running' | 'completed' | 'failed' | 'timed_out' | 'cancelled'
  error_code: string | null
  error_message: string | null
  created_at: string
}

export interface AgentMessage {
  id: string
  run_id: string
  role: 'user' | 'assistant'
  content: string
  snapshot_id: string | null
  revision: number | null
  tool_call_ids: string[]
  tool_calls: ToolResult[]
  status: 'completed' | 'failed' | 'timed_out' | 'model_unavailable'
  error_code: string | null
  error_message: string | null
  created_at: string
}

export interface CheckResult {
  check_id: string
  run_id: string
  snapshot_id: string
  revision: number
  name: string
  title: string
  policy_version: string
  expected: string
  observed: string
  verdict: Verdict
  control_result: Record<string, unknown> | null
  evidence: Record<string, unknown>
  exit_code: number | null
  stderr_snippet: string
  created_at: string
}

export interface JobRecord {
  job_id: string
  kind: 'create_run' | 'append_404' | 'agent_question' | 'run_checks'
  run_id: string | null
  status: 'pending' | 'running' | 'cancelling' | 'completed' | 'failed' | 'cancelled'
  progress_message: string
  result_id: string | null
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface ApiErrorBody {
  code: string
  message: string
}
