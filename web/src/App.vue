<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, auth } from './lib/api'
import { connect, wsState } from './lib/ws'
import { loadCommon, startStoreSync, store } from './lib/store'
import { dirtyRegistry } from './lib/dirty'
import Login from './views/Login.vue'

const route = useRoute()
const navOpen = ref(false)
const nav = [
  { to: '/dashboard', label: '대시보드', icon: '▦' },
  { to: '/feed', label: '피드', icon: '▶' },
  { to: '/pipeline', label: '파이프라인', icon: '≡' },
  { to: '/captions', label: '자막', icon: '字' },
  { to: '/edit', label: '편집 설정', icon: '✂' },
  { to: '/ai', label: 'AI·프롬프트', icon: '✦' },
  { to: '/upload', label: '업로드 설정', icon: '⇪' },
  { to: '/system', label: '시스템', icon: '⚙' },
]

async function boot() {
  try {
    await api.get('/system/status')
    auth.ok = true
  } catch (e) {
    if (auth.ok !== false) auth.ok = e.status === 401 ? false : true
  }
  if (auth.ok) {
    await loadCommon()
    startStoreSync()
    connect()
  }
}
onMounted(boot)
watch(() => auth.ok, (v, old) => { if (v && old === false) boot() })
watch(() => route.path, () => { navOpen.value = false })

const s = computed(() => store.status)
const ollamaBadge = computed(() => {
  const o = s.value?.ollama
  if (!o) return { text: 'AI …', cls: 'bg-slate-600' }
  if (!o.connected) return { text: 'Ollama 끊김', cls: 'bg-red-600' }
  if (!o.installed) return { text: `${o.model} 미설치`, cls: 'bg-amber-600' }
  return { text: `Ollama · ${o.model}`, cls: 'bg-emerald-700' }
})
</script>

<template>
  <Login v-if="auth.ok === false" />
  <div v-else-if="auth.ok" class="min-h-screen flex">
    <!-- 좌측 네비 -->
    <aside :class="['fixed z-40 inset-y-0 left-0 w-52 bg-slate-900 text-slate-200 flex flex-col transition-transform md:translate-x-0',
                    navOpen ? 'translate-x-0' : '-translate-x-full']">
      <div class="px-4 py-4 text-lg font-bold tracking-tight text-white">AutoSet</div>
      <nav class="flex-1 px-2 space-y-0.5">
        <router-link v-for="n in nav" :key="n.to" :to="n.to"
          class="flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-slate-800"
          active-class="bg-slate-800 text-white font-semibold">
          <span class="w-4 text-center opacity-70">{{ n.icon }}</span>{{ n.label }}
        </router-link>
      </nav>
      <div class="p-3 text-[11px] text-slate-500">{{ s?.server?.timezone }}</div>
    </aside>
    <div v-if="navOpen" class="fixed inset-0 z-30 bg-black/40 md:hidden" @click="navOpen = false" />

    <div class="flex-1 md:ml-52 min-w-0 flex flex-col">
      <!-- 상단 바 -->
      <header class="sticky top-0 z-20 bg-slate-800 text-white px-3 py-2 flex items-center gap-2 flex-wrap text-xs">
        <button class="md:hidden text-lg px-2" @click="navOpen = !navOpen">☰</button>
        <span class="font-semibold text-sm mr-2">{{ route.meta.title }}</span>
        <span v-if="dirtyRegistry.size" class="badge bg-amber-400 text-amber-950">● 저장 안 됨</span>
        <span class="flex-1" />
        <span :class="['badge', wsState.connected ? 'bg-emerald-700' : 'bg-red-600']">
          {{ wsState.connected ? '● 실시간 연결' : '○ 재연결 중 (폴링)' }}</span>
        <span class="badge bg-slate-600">업로드 {{ s?.upload_mode === 'selenium' ? 'D안 · Selenium' : s?.upload_mode === 'extension' ? '확장프로그램' : 'API' }}</span>
        <span :class="['badge', ollamaBadge.cls]">{{ ollamaBadge.text }}</span>
        <span class="badge bg-indigo-600">오늘 업로드 {{ s?.today_uploads ?? '-' }}</span>
      </header>
      <main class="flex-1 p-3 md:p-5 min-w-0">
        <router-view v-slot="{ Component }">
          <component :is="Component" />
        </router-view>
      </main>
    </div>
  </div>
  <div v-else class="min-h-screen grid place-items-center text-slate-400">연결 중…</div>

  <!-- 토스트 -->
  <div class="fixed bottom-3 right-3 z-50 space-y-2 max-w-sm">
    <div v-for="t in store.toasts" :key="t.id"
      :class="['rounded-md px-3 py-2 text-sm shadow-lg text-white',
               t.kind === 'error' ? 'bg-red-600' : t.kind === 'success' ? 'bg-emerald-600' : 'bg-slate-800']">
      {{ t.message }}
    </div>
  </div>
</template>
