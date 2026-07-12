const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

export interface Application {
  id: number
  company_name: string
  job_title: string
  platform: string
  job_url: string | null
  location: string | null
  salary_text: string | null
  applied_date: string
  current_status: string
  created_at: string
  updated_at: string
}

export interface StatusEvent {
  id: number
  application_id: number
  status: string
  event_date: string
  source_email_id: string | null
  raw_extract_json: string | null
  notes: string | null
  created_at: string
}

export interface FilterOptions {
  years: number[]
  platforms: string[]
  statuses: string[]
}

export interface DashboardResponse {
  board: Record<string, Application[]>
  status_order: string[]
  breakdown: { platform: string; total: number; responded: number }[]
  reminders: Application[]
  reminders_total: number
  filter_options: FilterOptions
}

export interface ReminderPageResponse {
  items: Application[]
  total: number
  page: number
  page_size: number
}

export interface DashboardFilters {
  year?: string
  platform?: string
  company?: string
  status?: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    throw new Error(`${init?.method ?? 'GET'} ${path} failed: ${response.status}`)
  }
  return response.json() as Promise<T>
}

export function getDashboard(filters: DashboardFilters): Promise<DashboardResponse> {
  const params = new URLSearchParams()
  if (filters.year) params.set('year', filters.year)
  if (filters.platform) params.set('platform', filters.platform)
  if (filters.company) params.set('company', filters.company)
  if (filters.status) params.set('status', filters.status)
  const query = params.toString()
  return request(`/api/dashboard${query ? `?${query}` : ''}`)
}

export function getReminders(page: number, pageSize = 20): Promise<ReminderPageResponse> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return request(`/api/reminders?${params.toString()}`)
}

export function getApplicationDetail(
  id: number
): Promise<{ application: Application; timeline: StatusEvent[] }> {
  return request(`/api/applications/${id}`)
}

export function patchStatus(id: number, status: string): Promise<Application> {
  return request(`/api/applications/${id}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export function patchFields(
  id: number,
  fields: { company_name: string; job_title: string; platform: string }
): Promise<Application> {
  return request(`/api/applications/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(fields),
  })
}

export function postReprocess(id: number): Promise<Application> {
  return request(`/api/applications/${id}/reprocess`, { method: 'POST' })
}

/** A web-researched company profile. Everything here is web-sourced (verifiable
 * via source_urls), never the application's own email-extracted data - the UI
 * labels it as such and keeps it visually separate. */
export interface CompanyResearch {
  company_name: string
  summary: string | null
  industry: string | null
  company_size: string | null
  headquarters: string | null
  website: string | null
  recent_news: string | null
  source_urls: string[]
  researched_at: string
}

export function postResearchCompany(id: number, refresh = false): Promise<CompanyResearch> {
  const query = refresh ? '?refresh=true' : ''
  return request(`/api/applications/${id}/research${query}`, { method: 'POST' })
}

export interface SourceEmail {
  subject: string
  sender: string
  date: string
  body: string
}

export function getSourceEmail(eventId: number): Promise<SourceEmail> {
  return request(`/api/status-events/${eventId}/email`)
}

export interface GmailStatus {
  connected: boolean
}

export function getGmailStatus(): Promise<GmailStatus> {
  return request('/api/gmail/status')
}

/** Full page navigation, not a fetch - this walks through Google's consent
 * screen and back, so it can't be an XHR/SPA route. `returnTo` is where the
 * backend redirects the browser after the token exchange completes. */
export function gmailConnectUrl(returnTo: string): string {
  const params = new URLSearchParams({ return_to: returnTo })
  return `${API_BASE}/api/gmail/connect?${params.toString()}`
}

export interface PipelineRun {
  id: string
  started_at: string
  finished_at: string | null
  emails_fetched: number
  emails_relevant: number
  applications_created: number
  events_created: number
  errors: string | null
  emails_total: number | null
  emails_scrutinized: number
  emails_extracted: number
  emails_written: number
  updated_at: string
  run_type: 'incremental' | 'full_scan'
  suggestions_created: number
}

export interface SyncStatus {
  in_progress: boolean
  last_error: string | null
  current_run_type: 'incremental' | 'full_scan' | null
  latest_run: PipelineRun | null
  history: PipelineRun[]
}

export function getSyncStatus(): Promise<SyncStatus> {
  return request('/api/sync/status')
}

export function postSync(): Promise<{ status: string }> {
  return request('/api/sync', { method: 'POST' })
}

export function postFullScan(): Promise<{ status: string }> {
  return request('/api/sync/full-scan', { method: 'POST' })
}

export interface ReviewSuggestion {
  id: number
  message_id: string
  application_id: number | null
  action: 'new_application' | 'update_existing' | 'reclassify_irrelevant'
  previous_classification: string
  suggested_classification: string
  previous_extract_json: string | null
  suggested_extract_json: string | null
  status: 'pending' | 'approved' | 'rejected'
  pipeline_run_id: string
  created_at: string
  reviewed_at: string | null
}

export function getReviewSuggestions(): Promise<ReviewSuggestion[]> {
  return request('/api/review-suggestions')
}

export function postApproveSuggestion(id: number): Promise<ReviewSuggestion> {
  return request(`/api/review-suggestions/${id}/approve`, { method: 'POST' })
}

export function postRejectSuggestion(id: number): Promise<ReviewSuggestion> {
  return request(`/api/review-suggestions/${id}/reject`, { method: 'POST' })
}

export function postRejectAllSuggestions(): Promise<{ rejected_count: number }> {
  return request('/api/review-suggestions/reject-all', { method: 'POST' })
}
