<template>
  <div class="trace-page">
    <div class="trace-header">
      <div>
        <div class="eyebrow">AGENT OBSERVABILITY</div>
        <h1>Agent Execution Trace</h1>
        <p>查看一次旅行规划请求中各 Agent 的任务、工具调用、状态与耗时。</p>
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
          <div class="metric-label">Agent Steps</div>
          <div class="metric-value">{{ trace.length }}</div>
        </a-card>
        <a-card :bordered="false">
          <div class="metric-label">Total Duration</div>
          <div class="metric-value">{{ totalDuration }} ms</div>
        </a-card>
        <a-card :bordered="false">
          <div class="metric-label">Failed Steps</div>
          <div class="metric-value">{{ failedCount }}</div>
        </a-card>
      </div>

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
            <div class="node-meta"><span>{{ plannerStep.duration_ms }} ms</span></div>
          </div>
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
              <span>{{ item.duration_ms }} ms</span>
            </div>
            <div class="timeline-task">{{ item.task }}</div>
            <div v-if="item.tool" class="timeline-tool">Tool · {{ item.tool }}</div>
            <div v-if="item.error" class="timeline-error">{{ item.error }}</div>
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
const failedCount = computed(() => trace.value.filter(item => ['failed', 'fallback'].includes(item.status)).length)
const totalDuration = computed(() => {
  const orchestrator = trace.value.find(item => item.agent === 'Orchestrator')
  if (orchestrator) return orchestrator.duration_ms
  return Math.round(trace.value.reduce((sum, item) => sum + Number(item.duration_ms || 0), 0) * 100) / 100
})

const statusColor = (status: string) => {
  if (status === 'success') return 'green'
  if (status === 'running') return 'blue'
  if (status === 'fallback') return 'orange'
  return 'red'
}

const timelineColor = (status: string) => {
  if (status === 'success') return 'green'
  if (status === 'fallback') return 'orange'
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
  grid-template-columns: repeat(4, minmax(0, 1fr));
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
.status-fallback { border-left: 4px solid #f59e0b; }
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
}

.flow-arrow {
  padding: 14px 0;
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
}

.planner-node {
  width: min(520px, 100%);
  background: #eef2ff;
}

.timeline-title span {
  color: #94a3b8;
  font-size: 12px;
}

.timeline-tool {
  display: inline-block;
  margin-top: 8px;
  padding: 3px 8px;
  border-radius: 6px;
  background: #eef2ff;
  color: #4f46e5;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.timeline-error {
  margin-top: 8px;
  color: #dc2626;
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

@media (max-width: 900px) {
  .summary-grid,
  .parallel-group {
    grid-template-columns: 1fr;
  }

  .trace-header {
    flex-direction: column;
  }
}
</style>
