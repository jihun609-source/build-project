<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../lib/api'
import { on } from '../lib/ws'
import { STATUS_LABEL, toast, toastError } from '../lib/store'
import { fmt, ago } from '../lib/time'

const d = ref(null)
const CARDS = [
  ['seen', '기록됨', '/feed'], ['fetched', '가져옴', '/pipeline'], ['editing', '편집중', '/pipeline'],
  ['captioning', '자막중', '/pipeline'], ['review', '검토 대기', '/pipeline?review=1'],
  ['upload_wait', '업로드 대기', '/pipeline'], ['uploaded', '업로드됨', '/pipeline'], ['failed', '실패', '/pipeline?failed=1'],
]
const WORKER_LABEL = { downloader: '다운로드', editor: '편집', captioner: '자막', uploader: '업로드' }

async function load() {
  try { d.value = await api.get('/system/dashboard') } catch (e) { toastError(e) }
}
let t = null
const offs = []
onMounted(() => {
  load()
  offs.push(on('item', () => { clearTimeout(t); t = setTimeout(load, 800) }), on('poll', load), on('worker', load))
})
onBeforeUnmount(() => offs.forEach(f => f()))

async function retryAll() {
  try {
    const r = await api.post('/items/retry-failed')
    toast(`${r.retried}건 재시도`, 'success')
    load()
  } catch (e) { toastError(e) }
}
async function worker(name, action) {
  try { await api.post(`/system/workers/${name}/${action}`); load() } catch (e) { toastError(e) }
}
</script>

<template>
  <div v-if="d" class="space-y-4">
    <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
      <router-link v-for="[k, label, to] in CARDS" :key="k" :to="to"
        :class="['card p-3 hover:border-indigo-300', k === 'failed' && d.cards[k] ? 'border-red-300 bg-red-50' : '',
                 k === 'review' && d.cards[k] ? 'border-amber-300 bg-amber-50' : '']">
        <div class="text-xs text-slate-500">{{ label }}</div>
        <div class="text-2xl font-bold">{{ d.cards[k] }}</div>
      </router-link>
    </div>

    <div class="grid lg:grid-cols-3 gap-4">
      <section class="card p-4 space-y-3">
        <h2 class="font-semibold">업로드</h2>
        <div class="grid grid-cols-3 gap-2 text-center">
          <div><div class="text-xs text-slate-500">오늘</div><div class="text-xl font-bold">{{ d.uploads_today }}</div></div>
          <div><div class="text-xs text-slate-500">이번 주</div><div class="text-xl font-bold">{{ d.uploads_week }}</div></div>
          <div><div class="text-xs text-slate-500">남은 슬롯(7일)</div><div class="text-xl font-bold">{{ d.free_slots_7d }}</div></div>
        </div>
        <h3 class="text-sm font-medium pt-2">다음 게시 예정</h3>
        <p v-if="!d.upcoming.length" class="text-sm text-slate-400">예약된 항목이 없습니다</p>
        <ul class="divide-y text-sm">
          <li v-for="u in d.upcoming" :key="u.id" class="py-1.5 flex gap-2 items-center">
            <span class="font-mono text-xs text-indigo-700 w-28 shrink-0">{{ fmt(u.publish_at) }}</span>
            <router-link :to="`/pipeline/${u.id}`" class="truncate flex-1 hover:underline">{{ u.title || `#${u.id}` }}</router-link>
            <span class="text-[11px] text-slate-400">{{ STATUS_LABEL[u.status] }}</span>
          </li>
        </ul>
      </section>

      <section class="card p-4 space-y-3">
        <h2 class="font-semibold">오늘 AI 호출</h2>
        <p v-if="!Object.keys(d.ai_usage).length" class="text-sm text-slate-400">아직 호출이 없습니다</p>
        <table v-else class="w-full text-sm">
          <thead class="text-xs text-slate-500"><tr><th class="text-left">프로바이더</th><th>호출</th><th>실패</th><th>폴백 성공</th></tr></thead>
          <tbody>
            <tr v-for="(u, name) in d.ai_usage" :key="name" class="text-center">
              <td class="text-left font-medium">{{ name }}</td><td>{{ u.calls }}</td><td>{{ u.failures }}</td><td>{{ u.fallbacks }}</td>
            </tr>
          </tbody>
        </table>
        <p class="text-xs text-slate-500">폴백 발생: {{ d.ai_fallbacks }}회</p>

        <h2 class="font-semibold pt-2">워커</h2>
        <ul class="space-y-1.5">
          <li v-for="w in d.workers" :key="w.name" class="flex items-center gap-2 text-sm">
            <span :class="['w-2 h-2 rounded-full', w.running ? (w.current_item ? 'bg-indigo-500 animate-pulse' : 'bg-emerald-500') : 'bg-slate-300']" />
            <span class="w-16">{{ WORKER_LABEL[w.name] }}</span>
            <span class="flex-1 text-xs text-slate-500 truncate">
              {{ w.running ? (w.stopping ? '정지 중…' : w.current_item ? `#${w.current_item} 처리 중` : '대기') : '정지됨' }}
            </span>
            <button v-if="w.running" class="btn btn-sm" @click="worker(w.name, 'stop')">정지</button>
            <button v-else class="btn btn-sm btn-primary" @click="worker(w.name, 'start')">시작</button>
          </li>
        </ul>
      </section>

      <section class="card p-4 space-y-2">
        <div class="flex items-center">
          <h2 class="font-semibold flex-1">최근 실패</h2>
          <button class="btn btn-sm" :disabled="!d.failures.length" @click="retryAll">모두 재시도</button>
        </div>
        <p v-if="!d.failures.length" class="text-sm text-slate-400">실패한 항목이 없습니다</p>
        <ul class="divide-y">
          <li v-for="f in d.failures" :key="f.id" class="py-2 text-sm">
            <div class="flex gap-2">
              <router-link :to="`/pipeline/${f.id}`" class="font-medium hover:underline truncate flex-1">
                #{{ f.id }} {{ f.title || f.author || '' }}</router-link>
              <span class="text-[11px] text-red-600">{{ STATUS_LABEL[f.status] }}</span>
            </div>
            <p class="text-xs text-slate-500 line-clamp-2" :title="f.error">{{ f.error }}</p>
            <p class="text-[11px] text-slate-400">{{ ago(f.updated_at) }}</p>
          </li>
        </ul>
      </section>
    </div>
  </div>
  <p v-else class="text-slate-400">불러오는 중…</p>
</template>
