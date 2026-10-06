<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, qs } from '../lib/api'
import { on } from '../lib/ws'
import { loadStatus, store, toast, toastError } from '../lib/store'
import { ago, fmt } from '../lib/time'
import HistoryPanel from '../components/HistoryPanel.vue'

const logs = ref([])
const worker = ref('')
const level = ref('info')
const follow = ref(true)
const logBox = ref(null)
const storage = ref(null)
const cleanDays = ref(30)
const historyOpen = ref(false)
const restoreBusy = ref(false)
const WORKERS = ['', 'downloader', 'editor', 'captioner', 'uploader', 'api']
const ORDER = ['debug', 'info', 'warning', 'error']

async function loadLogs() {
  try {
    logs.value = (await api.get('/system/logs' + qs({ worker: worker.value, level: level.value, tail: 300 }))).logs
    scroll()
  } catch (e) { toastError(e) }
}
function scroll() {
  if (!follow.value) return
  nextTick(() => { if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight })
}
async function loadStorage() {
  try {
    storage.value = await api.get('/system/storage')
    cleanDays.value = storage.value.done_keep_days
  } catch (e) { toastError(e) }
}
const offs = []
onMounted(() => {
  loadLogs()
  loadStorage()
  offs.push(on('log', (l) => {
    if (worker.value && l.worker !== worker.value) return
    if (ORDER.indexOf(l.level) < ORDER.indexOf(level.value)) return
    logs.value.push(l)
    if (logs.value.length > 1000) logs.value.splice(0, 200)
    scroll()
  }))
})
onBeforeUnmount(() => offs.forEach(f => f()))
watch([worker, level], loadLogs)

async function cleanup() {
  if (!confirm(`${cleanDays.value}일 지난 done 파일과, 업로드 후 ${cleanDays.value}일 지난 항목의 원본·작업 파일을 지울까요?`)) return
  try {
    const r = await api.post('/system/cleanup', { days: cleanDays.value })
    toast(`${r.removed}개 파일 삭제, ${(r.freed_bytes / 1e6).toFixed(1)}MB 확보`, 'success')
    loadStorage()
  } catch (e) { toastError(e) }
}
async function restore(e) {
  const file = e.target.files[0]
  e.target.value = ''
  if (!file || !confirm(`${file.name} 으로 DB·설정·프리셋·프롬프트를 복원할까요? 워커가 잠시 멈추고, 현재 상태는 .history/pre-restore-*.zip 으로 남습니다.`)) return
  restoreBusy.value = true
  try {
    const r = await api.upload('/system/restore', file)
    toast(`복원 완료 (${r.restored.length}개 파일)`, 'success')
    loadStatus()
  } catch (err) { toastError(err) } finally { restoreBusy.value = false }
}
const mb = (b) => (b / 1e6).toFixed(1) + 'MB'
const s = computed(() => store.status)
const lvCls = (l) => ({ error: 'text-red-400', warning: 'text-amber-300', debug: 'text-slate-500' }[l] || 'text-slate-200')
</script>

<template>
  <div class="space-y-4">
    <div class="grid lg:grid-cols-3 gap-4">
      <section class="card p-4 space-y-2 text-sm">
        <h3 class="font-semibold">연결 상태</h3>
        <p>확장프로그램 마지막 수신: <b>{{ s?.extension_last_seen ? ago(s.extension_last_seen) : '없음' }}</b>
          <span v-if="s?.extension_last_seen" class="text-xs text-slate-400"> ({{ fmt(s.extension_last_seen) }})</span></p>
        <p>Ollama: <b :class="s?.ollama?.connected ? 'text-emerald-600' : 'text-red-600'">{{ s?.ollama?.connected ? '연결됨' : '끊김' }}</b>
          · {{ s?.ollama?.model }} {{ s?.ollama?.installed ? '' : '(미설치)' }}</p>
        <p>YouTube OAuth: <b :class="s?.youtube?.connected ? 'text-emerald-600' : 'text-slate-500'">
          {{ s?.youtube?.connected ? (s.youtube.channel?.title || '연결됨') : '연결 안 됨' }}</b></p>
        <p>실시간 연결 클라이언트: {{ s?.server?.ws_clients }}</p>
        <h4 class="font-medium pt-2">서버 주소 (같은 네트워크의 폰에서 접속)</h4>
        <div v-for="a in s?.server?.addresses || []" :key="a" class="flex items-center gap-3">
          <img :src="`/system/qr?url=${encodeURIComponent(a)}`" class="w-24 h-24 border rounded bg-white" />
          <code class="text-xs break-all">{{ a }}</code>
        </div>
      </section>

      <section class="card p-4 space-y-2 text-sm">
        <h3 class="font-semibold">저장 공간</h3>
        <table v-if="storage" class="w-full text-xs">
          <tr v-for="(v, k) in storage.dirs" :key="k"><td class="font-mono py-0.5">{{ k }}/</td><td>{{ v.files }}개</td><td class="text-right">{{ mb(v.bytes) }}</td></tr>
          <tr class="border-t"><td class="pt-1">디스크 여유</td><td></td><td class="text-right pt-1">{{ (storage.disk.free / 1e9).toFixed(1) }}GB</td></tr>
        </table>
        <div class="flex gap-2 items-end pt-2">
          <label class="flex-1"><span class="label">보관 일수</span><input v-model.number="cleanDays" type="number" min="1" class="input" /></label>
          <button class="btn" @click="cleanup">오래된 파일 정리</button>
        </div>
        <p class="text-[11px] text-slate-400">기본 보관 일수는 config.storage.done_keep_days ({{ storage?.done_keep_days }}일)</p>
      </section>

      <section class="card p-4 space-y-3 text-sm">
        <h3 class="font-semibold">설정 이력·백업</h3>
        <button class="btn w-full" @click="historyOpen = true">config.yaml 버전 이력 (비교·되돌리기)</button>
        <a class="btn btn-primary w-full" href="/system/backup">백업 zip 내려받기 (DB·config·presets·prompts)</a>
        <label :class="['btn w-full cursor-pointer', restoreBusy && 'opacity-50']">
          {{ restoreBusy ? '복원 중…' : 'zip 업로드로 복원' }}
          <input type="file" accept=".zip" class="hidden" :disabled="restoreBusy" @change="restore" /></label>
      </section>
    </div>

    <section class="card p-4 space-y-2">
      <div class="flex flex-wrap gap-2 items-center">
        <h3 class="font-semibold flex-1">로그</h3>
        <select v-model="worker" class="input w-36"><option v-for="w in WORKERS" :key="w" :value="w">{{ w || '전체 워커' }}</option></select>
        <select v-model="level" class="input w-28"><option v-for="l in ORDER" :key="l" :value="l">{{ l }} 이상</option></select>
        <label class="text-xs flex items-center gap-1"><input v-model="follow" type="checkbox" /> 자동 스크롤</label>
        <a v-if="worker && worker !== 'api'" class="btn btn-sm" :href="`/system/logs/download?worker=${worker}`">파일 다운로드</a>
        <a v-else-if="worker === 'api'" class="btn btn-sm" href="/system/logs/download?worker=api">파일 다운로드</a>
      </div>
      <div ref="logBox" class="bg-slate-900 rounded p-2 h-[28rem] overflow-auto font-mono text-xs leading-5">
        <div v-for="l in logs" :key="l.id" class="whitespace-pre-wrap">
          <span class="text-slate-500">{{ fmt(l.created_at, false) }}</span>
          <span class="text-sky-400"> {{ (l.worker || '').padEnd(10) }}</span>
          <span v-if="l.item_id" class="text-violet-300">#{{ l.item_id }} </span>
          <span :class="lvCls(l.level)">{{ l.message }}</span>
        </div>
        <p v-if="!logs.length" class="text-slate-500">로그가 없습니다</p>
      </div>
    </section>

    <HistoryPanel v-model="historyOpen" title="config.yaml 버전 이력" list-url="/config/history"
      :version-url="(v) => `/config/history/${encodeURIComponent(v)}`"
      :restore-url="(v) => `/config/restore/${encodeURIComponent(v)}`" @restored="loadStatus" />
  </div>
</template>
