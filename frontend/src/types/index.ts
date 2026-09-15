// 类型定义

export interface Location {
  longitude: number
  latitude: number
}

export interface Attraction {
  name: string
  address: string
  location: Location
  visit_duration: number
  description: string
  category?: string
  rating?: number
  image_url?: string
  ticket_price?: number
}

export interface Meal {
  type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  address?: string
  location?: Location
  description?: string
  estimated_cost?: number
}

export interface Hotel {
  name: string
  address: string
  location?: Location
  price_range: string
  rating: string
  distance: string
  type: string
  estimated_cost?: number
}

export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
}

export interface DayPlan {
  date: string
  day_index: number
  description: string
  transportation: string
  accommodation: string
  hotel?: Hotel
  attractions: Attraction[]
  meals: Meal[]
}

export interface WeatherInfo {
  date: string
  day_weather: string
  night_weather: string
  day_temp: number
  night_temp: number
  wind_direction: string
  wind_power: string
}

export interface TripPlan {
  city: string
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: WeatherInfo[]
  overall_suggestions: string
  budget?: Budget
}

export interface TripConstraints {
  max_budget?: number | null
  max_daily_attractions?: number | null
  max_daily_visit_minutes: number
  max_route_minutes: number
}

export interface TripFormData {
  city: string
  start_date: string
  end_date: string
  travel_days: number
  transportation: string
  accommodation: string
  preferences: string[]
  free_text_input: string
  constraints?: TripConstraints
}

export interface RetryError {
  attempt: number
  category: string
  message: string
  retryable: boolean
}

export interface ValidationIssue {
  code: string
  severity: 'warning' | 'error' | string
  message: string
  day_index?: number | null
  details?: Record<string, unknown>
}

export interface ValidationReport {
  passed: boolean
  blocking_issue_count: number
  issues: ValidationIssue[]
  checked_route_segments: number
  route_source_counts: Record<string, number>
}

export interface ExecutionTraceEvent {
  id: string
  agent: string
  task: string
  tool?: string | null
  status: 'running' | 'success' | 'failed' | 'fallback' | 'needs_revision' | 'degraded' | string
  started_at: string
  finished_at?: string
  duration_ms: number
  attempts?: number
  error?: string | null
  error_category?: string | null
  retry_errors?: RetryError[]
  result_preview?: string
  degraded?: boolean
  validation_passed?: boolean
  validation_report?: ValidationReport
  retried_steps?: number
}

export interface TripPlanResponse {
  success: boolean
  message: string
  session_id?: string
  data?: TripPlan
  execution_trace?: ExecutionTraceEvent[]
}
