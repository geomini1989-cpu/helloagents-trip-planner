<template>
  <div class="trace-page">
    <div class="trace-header">
      <div>
        <div class="eyebrow">AGENT OBSERVABILITY</div>
        <h1>Agent Execution Trace</h1>
        <p>查看检索、规划、GIS 路线优化、约束校验、自动修正、锁定保护、重试与耗时。</p>
      </div>
      <a-space>
        <a-button @click="loadTrace" :loading="loading">刷新</a-button>
        <a-button type="primary" @click="router.push('/result')">返回行程</a-button>
      </a-space>
    </div>

    <a-alert
      v-if="!sessionId"
      type="info"
      show-icon
      message="暂无会话"
      description="请先生成一次旅行计划，再查看 Agent 执行轨迹。"
    />

    <template v-else>
      <div class="summary-grid">
        <a-card :bordered="false">
          <div class="metric-label">Session</div>
          <div class="metric-value session-value">{{ shortSessionId }}</div>
        </a-card>
        <a-card :bordered="false">
          <div class="metric-label">Trace Events</div>
          <div class="metric-value">{{ trace.length }}</div>
        </a-card>
        <a-card :bordered="false">
          <div class="metric-label">Total Duration</div>
          <div class="metric-value">{{ totalDuration }} ms</div>
        </a-card>
        <a-card :bordered="false">
          <div class="metric-label">GIS Saved</div>
          <div class="metric-value">{{ gisSavedMinutes }} min</div>
        </a-card>
        <a-card :bordered="false">
          <div class="metric-label">Problem Events</div>
          <div class="metric-value">{{ problemCount }}</div>
        </a-card>
      </div>

      <a-alert
        v-if="orchestratorStep?.degraded"
        type="warning"
        show-icon
        class="degraded-alert"
        message="本次任务存在降级或未完全解决的约束冲突"
        description="可能是 Agent / Tool / GIS 失败、Planner fallback，或自动修正后仍未通过 Validator。请查看下方执行明细。"
      />

      <a-alert
        v-if="lockRestoreCount > 0"
        type="warning"
        show-icon
        class="degraded-alert"
        :message="`Lock Guard 已恢复 ${lockRestoreCount} 处被误改的锁定内容`"
        description="Revision Agent 的候选修改触碰了用户锁定字段，后端已恢复原值；未锁定部分仍保留修改结果。"
      />

      <a-card class="pipeline-card" :bordered="false" title="任务编排链路">
        <div class="pipeline">
          <div class="parallel-group">
            <div
              v-for="item in retrievalSteps"
              :key="item.id"
              class="agent-node"
              :class="`status-${item.status}`"
            >
              <div class="node-top">
                <strong>{{ item.agent }}</strong>
                <a-tag :color="statusColor(item.status)">{{ item.status }}</a-tag>
              </div>
              <div class="node-task">{{ item.task }}</div>
              <div class="node-meta">
                <span v-if="item.tool">Tool: {{ item.tool }}</span>
                <span>{{ item.duration_ms }} ms</span>
                <span>Attempts: {{ item.attempts || 1 }}</span>
              </div>
            </div>
          </div>

          <div class="flow-arrow">↓ fan-in</div>

          <div
            v-if="plannerStep"
            class="agent-node planner-node"
            :class="`status-${plannerStep.status}`"
          >
            <div class="node-top">
              <strong>{{ plannerStep.agent }}</strong>
              <a-tag :color="statusColor(plannerStep.status)">{{ plannerStep.status }}</a-tag>
            </div>
            <div class="node-task">{{ plannerStep.task }}</div>
            <div class="node-meta">
              <span>{{ plannerStep.duration_ms }} ms</span>
              <span>Attempts: {{ plannerStep.attempts || 1 }}</span>
            </div>
          </div>

          <template v-for="item in downstreamSteps" :key="item.id">
            <div class="flow-arrow">↓</div>
            <div
              class="agent-node downstream-node"
              :class="[
                `status-${item.status}`,
                {
                  'repair-node': item.agent === 'Repair Agent',
                  'gis-node': item.agent.includes('GIS'),
                  'lock-node': item.agent === 'Revision Lock Guard'
                }
              ]"
            >
              <div class="node-top">
                <strong>{{ item.agent }}</strong>
                <a-tag :color="statusColor(item.status)">{{ item.status }}</a-tag>
              </div>
              <div class="node-task">{{ item.task }}</div>
              <div class="node-meta">
                <span v-if="item.validation_report">
                  Blocking issues: {{ item.validation_report.blocking_issue_count }}
                </span>
                <span v-if="item.route_optimization">
                  Saved: {{ item.route_optimization.saved_minutes }} min · Reordered: {{ item.route_optimization.reordered_days }} day(s)
                </span>
                <span v-if="item.violations?.length">
                  Restored: {{ item.violations.length }} locked field(s)
                </span>
                <span>{{ item.duration_ms }} ms</span>
              </div>
            </div>
          </template>
        </div>
      </a-card>

      <a-card class="detail-card" :bordered="false" title="执行明细">
        <a-timeline>
          <a-timeline-item
            v-for="item in trace"
            :key="item.id"
            :color="timelineColor(item.status)"
          >
            <div class="timeline-title">
              <strong>{{ item.agent }}</strong>
              <span>{{ item.duration_ms }} ms · {{ item.attempts || 1 }} attempt(s)</span>
            </div>
            <div class="timeline-task">{{ item.task }}</div>
            <div v-if="item.tool" class="timeline-tool">Tool · {{ item.tool }}</div>
            <div v-if="item.error_category" class="error-category">Error · {{ item.error_category }}</div>
            <div v-if="item.error" class="timeline-error">{{ item.error }}</div>

            <div v-if="item.route_optimization" class="gis-panel">
              <div class="panel-title">GIS Route Optimization</div>
              <div class="validation-meta">
                <span>Reordered days: {{ item.route_optimization.reordered_days }}</span>
                <span>Estimated saving: {{ item.route_optimization.saved_minutes }} min</span>
                <span>
                  Sources:
                  {{ Object.entries(item.route_optimization.source_counts).map(([k, v]) => `${k}=${v}`).join(', ') || '-' }}
                </span>
              </div>
              <div
                v-for="day in item.route_optimization.days"
                :key="`${item.id}-gis-${day.day_index}`"
                class="gis-day"
              >
                <div class="gis-day-header">
                  <strong>Day {{ day.day_index + 1 }}</strong>
                  <span>{{ day.before_minutes }} → {{ day.after_minutes }} min</span>
                </div>
                <div class="route-order">
                  <span>Before · {{ day.original_order.join(' → ') || '-' }}</span>
                  <span>After · {{ day.optimized_order.join(' → ') || '-' }}</span>
                </div>
              </div>
            </div>

            <div v-if="item.validation_report" class="validation-panel">
              <div class="panel-title">
                Validation · {{ item.validation_report.passed ? 'passed' : 'needs revision' }}
              </div>
              <div class="validation-meta">
                <span>Route segments: {{ item.validation_report.checked_route_segments }}</span>
                <span>
                  Sources:
                  {{ Object.entries(item.validation_report.route_source_counts).map(([k, v]) => `${k}=${v}`).join(', ') || '-' }}
                </span>
              </div>
              <div
                v-for="issue in item.validation_report.issues"
                :key="`${item.id}-${issue.code}-${issue.day_index ?? 'all'}`"
                class="validation-issue"
                :class="`issue-${issue.severity}`"
              >
                <strong>{{ issue.code }}</strong>
                <span>{{ issue.message }}</span>
              </div>
            </div>

            <div v-if="item.violations?.length" class="lock-panel">
              <div class="panel-title">Revision Lock Guard · restored</div>
              <div
                v-for="(violation, index) in item.violations"
                :key="`${item.id}-lock-${index}`"
                class="lock-violation"
              >
                <strong>{{ violation.type }}</strong>
                <span>{{ violation.message }}</span>
              </div>
            </div>

            <div v-if="item.retry_errors?.length" class="retry-panel">
              <div class="retry-title">Retry history</div>
              <div v-for="retry in item.retry_errors" :key="`${item.id}-${retry.attempt}`" class="retry-row">
                <span>#{{ retry.attempt }}</span>
                <span>{{ retry.category }}</span>
                <span>{{ retry.message }}</span>
              </div>
            </div>

            <div v-if="item.result_preview" class="preview">{{ item.result_preview }}</div>
          </a-timeline-item>
        </a-timeline>
      </a-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import type { ExecutionTraceEvent } from '@/types'

const router = useRouter()
const sessionId = ref(sessionStorage.getItem('sessionId') || '')
const trace = ref<ExecutionTraceEvent[]>([])
const loading = ref(false)

const shortSessionId = computed(() => sessionId.value ? `${sessionId.value.slice(0, 8)}…${sessionId.value.slice(-4)}` : '-')
const retrievalSteps = computed(() => trace.value.filter(item => ['Attraction Agent', 'Weather Agent', 'Hotel Agent'].includes(item.agent)))
const plannerStep = computed(() => trace.value.find(item => item.agent === 'Planner Agent'))
const downstreamSteps = computed(() => trace.value.filter(item => [
  'GIS Route Optimizer',
  'Trip Validator',
  'Repair Agent',
  'Post-Repair GIS Optimizer',
  'Post-Repair Validator',
  'Revision Agent',
  'Revision Lock Guard',
].includes(item.agent)))
const orchestratorStep = computed(() => trace.value.find(item => item.agent === 'Orchestrator'))
const problemCount = computed(() => trace.value.filter(item => ['failed', 'fallback', 'needs_revision', 'degraded', 'restored'].includes(item.status)).length)
const retriedCount = computed(() => trace.value.filter(item => item.agent !== 'Orchestrator' && Number(item.attempts || 1) > 1).length)
const gisSavedMinutes = computed(() => trace.value.reduce((sum, item) => sum + Number(item.route_optimization?.saved_minutes || 0), 0))
const lockRestoreCount = computed(() => trace.value.reduce((sum, item) => sum + Number(item.violations?.length || 0), 0))
const totalDuration = computed(() => {
  if (orchestratorStep.value) return orchestratorStep.value.duration_ms
  return Math.round(trace.value.reduce((sum, item) => sum + Number(item.duration_ms || 0), 0) * 100) / 100
})

const statusColor = (status: string) => {
  if (status === 'success') return 'green'
  if (status === 'running') return 'blue'
  if (['fallback', 'needs_revision', 'degraded', 'restored'].includes(status)) return 'orange'
  return 'red'
}

const timelineColor = (status: string) => {
  if (status === 'success') return 'green'
  if (['fallback', 'needs_revision', 'degraded', 'restored'].includes(status)) return 'orange'
  if (status === 'failed') return 'red'
  return 'blue'
}

const loadTrace = async () => {
  if (!sessionId.value) return
  loading.value = true
  try {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
    const response = await fetch(`${baseUrl}/api/trip/trace/${sessionId.value}`)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const data = await response.json()
    trace.value = data.execution_trace || []
  } catch (error) {
    console.error(error)
    message.error('执行轨迹加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadTrace)
</script>

<style scoped>
.trace-page {
  max-width: 1280px;
  margin: 0 auto;
  padding: 28px 8px 56px;
}

.trace-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
}

.trace-header h1 {
  margin: 4px 0 8px;
  font-size: 32px;
}

.trace-header p {
  margin: 0;
  color: #64748b;
}

.eyebrow {
  color: #6366f1;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.14em;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.metric-label {
  color: #64748b;
  font-size: 13px;
  margin-bottom: 8px;
}

.metric-value {
  font-size: 24px;
  font-weight: 700;
  color: #0f172a;
}

.session-value {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 18px;
}

.degraded-alert {
  margin-bottom: 20px;
}

.pipeline-card,
.detail-card {
  margin-top: 20px;
  border-radius: 14px;
}

.pipeline {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.parallel-group {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.agent-node {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 16px;
}

.status-success { border-left: 4px solid #22c55e; }
.status-failed { border-left: 4px solid #ef4444; }
.status-fallback,
.status-needs_revision,
.status-degraded,
.status-restored { border-left: 4px solid #f59e0b; }
.status-running { border-left: 4px solid #3b82f6; }

.node-top,
.timeline-title,
.node-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.node-task,
.timeline-task {
  margin-top: 10px;
  color: #475569;
}

.node-meta {
  margin-top: 12px;
  color: #94a3b8;
  font-size: 12px;
  flex-wrap: wrap;
}

.flow-arrow {
  padding: 14px 0;
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}

.planner-node,
.downstream-node {
  width: min(680px, 100%);
}

.planner-node { background: #eef2ff; }
.downstream-node { background: #f0fdf4; }
.repair-node { background: #fff7ed; }
.gis-node { background: #f0f9ff; }
.lock-node { background: #fff7ed; }

.timeline-title span {
  color: #94a3b8;
  font-size: 12px;
}

.timeline-tool,
.error-category {
  display: inline-block;
  margin-top: 8px;
  margin-right: 8px;
  padding: 3px 8px;
  border-radius: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.timeline-tool {
  background: #eef2ff;
  color: #4f46e5;
}

.error-category {
  background: #fff7ed;
  color: #c2410c;
}

.timeline-error {
  margin-top: 8px;
  color: #dc2626;
}

.validation-panel,
.gis-panel,
.lock-panel {
  margin-top: 10px;
  padding: 12px;
  border-radius: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}

.gis-panel { background: #f0f9ff; }
.lock-panel { background: #fff7ed; }

.panel-title {
  font-weight: 700;
  color: #334155;
}

.validation-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 6px;
  color: #64748b;
  font-size: 12px;
}

.validation-issue,
.lock-violation {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: 10px;
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 12px;
}

.lock-violation {
  background: #fffbeb;
  color: #92400e;
}

.issue-error {
  background: #fef2f2;
  color: #991b1b;
}

.issue-warning {
  background: #fffbeb;
  color: #92400e;
}

.gis-day {
  margin-top: 10px;
  padding: 9px 10px;
  border-radius: 7px;
  background: #ffffff;
  border: 1px solid #dbeafe;
}

.gis-day-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.route-order {
  display: grid;
  gap: 4px;
  margin-top: 6px;
  color: #475569;
  font-size: 12px;
}

.retry-panel {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fffbeb;
  border: 1px solid #fde68a;
}

.retry-title {
  font-size: 12px;
  font-weight: 700;
  color: #92400e;
  margin-bottom: 6px;
}

.retry-row {
  display: grid;
  grid-template-columns: 36px 100px minmax(0, 1fr);
  gap: 8px;
  font-size: 12px;
  color: #78350f;
  margin-top: 4px;
}

.preview {
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f8fafc;
  color: #64748b;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 1000px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .summary-grid,
  .parallel-group {
    grid-template-columns: 1fr;
  }

  .trace-header {
    flex-direction: column;
  }

  .retry-row,
  .validation-issue,
  .lock-violation {
    grid-template-columns: 1fr;
  }
}
</style>