// 앱 전역 상태: 시스템 상태, 프리셋 목록, 예약 프리셋, 토스트
import { reactive } from 'vue'
import { api } from './api'
import { on } from './ws'

export const store = reactive({
  status: null,
  presets: [],
  defaultPreset: 'default',
  schedulePresets: [],
  toasts: [],
})

let toastId = 0
export function toast(message, kind = 'info', ms = 3500) {
  const id = ++toastId
  store.toasts.push({ id, message, kind })
  setTimeout(() => { store.toasts = store.toasts.filter(t => t.id !== id) }, ms)
}

export function toastError(e) {
  toast(e?.message || String(e), 'error', 6000)
}

export async function loadStatus() {
  try { store.status = await api.get('/system/status') } catch (e) { /* 401은 로그인 화면으로 */ }
}

export async function loadPresets() {
  const r = await api.get('/presets/edit')
  store.presets = r.presets
  store.defaultPreset = r.default
}

export async function loadSchedulePresets() {
  const r = await api.get('/schedule/presets')
  store.schedulePresets = r.presets
}

export async function loadCommon() {
  await Promise.allSettled([loadStatus(), loadPresets(), loadSchedulePresets()])
}

let started = false
export function startStoreSync() {
  if (started) return
  started = true
  setInterval(loadStatus, 15000)
  on('worker', (w) => {
    if (!store.status) return
    const i = store.status.workers.findIndex(x => x.name === w.name)
    if (i >= 0) store.status.workers[i] = w
  })
  on('config', () => { loadCommon() })
  on('poll', () => loadStatus())
  on('ollama_pull', (p) => { if (store.status) store.status.ollama_pull = p })
}

export const STATUS_LABEL = {
  queued: '다운로드 대기', downloading: '다운로드 중', downloaded: '편집 대기', held: '보류 (다운로드만)', editing: '편집 중',
  edited: '자막 대기', captioning: '자막 중', captioned_review: '검토 대기', captioned: '업로드 대기',
  uploading: '업로드 중', uploaded: '업로드됨', skipped: '건너뜀',
  failed_download: '다운로드 실패', failed_edit: '편집 실패', failed_caption: '자막 실패', failed_upload: '업로드 실패',
  seen: '기록됨', fetched: '가져옴', excluded: '제외',
}

export function statusColor(s) {
  if (!s) return 'bg-slate-100 text-slate-600'
  if (s.startsWith('failed')) return 'bg-red-100 text-red-700'
  if (s === 'uploaded') return 'bg-emerald-100 text-emerald-700'
  if (s === 'captioned_review') return 'bg-amber-100 text-amber-800'
  if (s === 'held') return 'bg-sky-100 text-sky-800'
  if (s === 'excluded' || s === 'skipped') return 'bg-slate-200 text-slate-500'
  if (s === 'seen') return 'bg-sky-100 text-sky-700'
  return 'bg-indigo-100 text-indigo-700'
}
