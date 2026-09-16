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

export interface RevisionLocks {
  locked_day_indexes: number[]
  lock_all_hotels: boolean
  locked_attraction_names: string[]
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

export interface DayRouteOptimization {
  day_index: number
  original_order: string[]
  optimized_order: string[]
  before_minutes: number
  after_minutes: number
  saved_minutes: number
  reordered: boolean
  route_type: string
  source_counts: Record<string, number>
  evaluated_permutations: number
}

export interface RouteOptimizationReport {
  optimized_days: number
  reordered_days: number
  saved_minutes: number
  source_counts: Record<string, number>
  days: DayRouteOptimization[]
}

export interface LockViolation {
  type: string
  day_index?: number
  attraction?: string
  message: string
}

export interface ExecutionPlanNode {
  id: string
  capability: string
  depends_on: string[]
  conditional: boolean
}

export interface ExecutionPlan {
  intent: 'full_plan' | 'general_revision' | 'weather_replan' | 'hotel_change' | 'route_optimize' | 'poi_rules_check' | string
  source: 'llm' | 'heuristic_fallback' | string
  reason: string
  capabilities: string[]
  nodes: ExecutionPlanNode[]
}

export interface KnowledgeSource {
  title: string
  url: string
  score?: number | null
}

export interface KnowledgeClaim {
  attraction: string
  claim_type: string
  claim: string
  verification_status: 'verified' | 'unverified' | 'unsupported' | string
  source_url?: string | null
  source_title?: string | null
}

export interface KnowledgeClaimMetrics {
  total_claims: number
  supported_claims: number
  unsupported_claims: number
  unverified_claims: number
  supported_claim_rate: number
  unsupported_claim_rate: number
  unverified_claim_rate: number
  source_count: number
  claim_parse_error?: string | null
}

export interface ExecutionTraceEvent {
  id: string
  agent: string
  task: string
  tool?: string | null
  status: 'running' | 'success' | 'failed' | 'fallback' | 'needs_revision' | 'degraded' | 'restored' | string
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
  route_optimization?: RouteOptimizationReport
  locks?: RevisionLocks
  violations?: LockViolation[]
  retried_steps?: number
  execution_plan?: ExecutionPlan
  knowledge_provider?: string
  knowledge_sources?: KnowledgeSource[]
  knowledge_claims?: KnowledgeClaim[]
  knowledge_claim_metrics?: KnowledgeClaimMetrics
}

export interface TripPlanResponse {
  success: boolean
  message: string
  session_id?: string
  data?: TripPlan
  execution_trace?: ExecutionTraceEvent[]
  locks?: RevisionLocks
  execution_plan?: ExecutionPlan
}
