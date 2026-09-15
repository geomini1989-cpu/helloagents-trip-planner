<template>
  <div id="app">
    <a-layout style="min-height: 100vh">
      <a-layout-header class="app-header">
        <div class="brand" @click="router.push('/')">
          <span class="brand-mark">◎</span>
          <div>
            <div class="brand-title">Multi-Agent Trip Planner</div>
            <div class="brand-subtitle">MCP-powered travel planning system</div>
          </div>
        </div>

        <a-space>
          <a-button ghost @click="router.push('/')">规划行程</a-button>
          <a-button ghost @click="router.push('/trace')">执行轨迹</a-button>
        </a-space>
      </a-layout-header>

      <a-layout-content style="padding: 24px">
        <router-view />
      </a-layout-content>

      <a-layout-footer style="text-align: center">
        Multi-Agent Trip Planner · HelloAgents + MCP + FastAPI + Vue 3
      </a-layout-footer>
    </a-layout>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<style>
#app {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
    'Noto Sans', sans-serif;
}

.app-header {
  background: #0f172a !important;
  padding: 0 40px !important;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  color: white;
  cursor: pointer;
  line-height: 1.2;
}

.brand-mark {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #4f46e5;
  font-size: 22px;
}

.brand-title {
  font-size: 17px;
  font-weight: 700;
}

.brand-subtitle {
  margin-top: 3px;
  color: #94a3b8;
  font-size: 11px;
}

@media (max-width: 720px) {
  .app-header {
    padding: 0 16px !important;
  }

  .brand-subtitle {
    display: none;
  }
}
</style>
