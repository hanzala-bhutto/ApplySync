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
  filter_options: FilterOptions
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
