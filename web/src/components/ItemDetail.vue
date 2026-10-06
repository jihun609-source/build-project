<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../lib/api'
import { on } from '../lib/ws'
import { STATUS_LABEL, statusColor, store, toast, toastError } from '../lib/store'
import { fmt, fmtFull, resolveSchedule } from '../lib/time'
import { useDirty } from '../lib/dirty'
import StepBar from './StepBar.vue'
import PresetPicker from './PresetPicker.vue'
import SchedulePicker from './SchedulePicker.vue'

const props = defineProps({ id: { type: Number, required: true } })
const emit = defineEmits(['close', 'deleted'])

const d = ref(null)
const form = reactive({ caption: '', title: '', description: '', tags: '' })
const saved = ref('')
const videoTab = ref('final')
const reeditPreset = ref('')
const regen = reactive({ instruction: '', profile: '' })
const profiles = ref([])
const defaultProfile = ref('caption')
const newSchedule = ref(null)
const showRaw = ref(false)
const busy = ref(false)

const BUSY = ['downloading', 'editing', 'captioning', 'uploading']
const isBusy = computed(() => d.value && BUSY.includes(d.value.status))

function fillForm() {
  form.caption = d.value.caption || ''
  form.title = d.value.title || ''
  form.description = d.value.description || ''
  form.tags = (d.value.tags || []).join(', ')
  saved.value = JSON.stringify(form)
}
const { dirty } = useDirty('item-meta', () => d.value && JSON.stringify(form) !== saved.value)

async function load(keepForm = false) {
  try {
    const wasDirty = dirty.value
    d.value = await api.get(`/items/${props.id}`)
    if (!keepForm || !wasDirty) fillForm()
    if (!reeditPreset.value) reeditPreset.value = d.value.edit_preset || store.defaultPreset
    if (!d.value.files.final && d.value.files.edited) videoTab.value = 'edited'
  } catch (e) { toastError(e) }
}
watch(() => props.id, () => { d.value = null; reeditPreset.value = ''; load() })

let t = null
const offs = []
onMounted(async () => {
  load()
  try {
    const r = await api.get('/prompts')
    profiles.value = r.profiles
    defaultProfile.value = r.default
  } catch { /* 무시 */ }
  offs.push(on('item', (it) => {
    if (it.id !== props.id) return
    // 진행률은 바로 반영, 나머지(이벤트·파일)는 잠깐 모아서 다시 읽기
    if (d.value) Object.assign(d.value, { status: it.status, stage_progress: it.stage_progress, progress_pct: it.progress_pct,
      error: it.error, publish_mode: it.publish_mode, publish_at: it.publish_at, video_id: it.video_id })
    clearTimeout(t)
    t = setTimeout(() => load(true), 1200)
  }))
  offs.push(on('poll', () => load(true)))
})
onBeforeUnmount(() => offs.forEach(f => f()))

async function act(path, body, msg) {
  busy.value = true
  try {
    await api.post(`/items/${props.id}/${path}`, body)
    if (msg) toast(msg, 'success')
    await load(true)
  } catch (e) { toastError(e) } finally { busy.value = false }
}

async function saveMeta() {
  try {
    await api.patch(`/items/${props.id}`, {
      caption: form.caption, title: form.title, description: form.description,
      tags: form.tags.split(',').map(s => s.trim()).filter(Boolean),
    })
    saved.value = JSON.stringify(form)
    toast('저장했습니다' + (d.value.caption !== form.caption ? ' (자막 합성 다시 실행)' : ''), 'success')
    load()
  } catch (e) { toastError(e) }
}

async function setSchedule(immediate) {
  const sch = immediate ? { publish_mode: 'immediate' } : resolveSchedule(newSchedule.value)
  if (sch === null) return toast('선택한 시각이 이미 지났습니다', 'error')
  try {
    await api.patch(`/items/${props.id}`, immediate ? sch : { publish_mode: 'scheduled', ...sch })
    toast('게시 설정을 바꿨습니다', 'success')
    load(true)
  } catch (e) { toastError(e) }
}

async function remove() {
  if (!confirm(`#${props.id} 항목과 파일을 삭제할까요? 피드 카드는 '기록됨'으로 돌아갑니다.`)) return
  try {
    await api.del(`/items/${props.id}`)
    toast('삭제했습니다')
    emit('deleted')
  } catch (e) { toastError(e) }
}

const ep = computed(() => d.value?.edit_params || {})
const videoUrl = computed(() => d.value?.files?.[videoTab.value])
const counts = (s) => `${[...(s || '')].length}자`
const schedulable = computed(() => d.value && !['uploading', 'uploaded', 'skipped'].includes(d.value.status))
const aiRes = computed(() => d.value?.ai_response)
const creditHint = computed(() => {
  const up = d.value?.upload_preview?.description || ''
  return up !== (d.value?.description || '').trim() ? up.slice((d.value?.description || '').trim().length).trim() : ''
})
function levelCls(l) {
  return l === 'error' ? 'text-red-600' : l === 'warning' ? 'text-amber-600' : 'text-slate-600'
}
</script>

<template>
  <div v-if="d" class="space-y-4">
    <!-- 헤더 -->
    <div class="flex items-start gap-2">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 flex-wrap">
          <h2 class="font-bold text-lg truncate">#{{ d.id }} {{ d.title || '(제목 없음)' }}</h2>
          <span :class="['badge', statusColor(d.status)]">{{ STATUS_LABEL[d.status] }}</span>
          <span v-if="d.ai_confidence" :class="['badge', d.ai_confidence === 'low' ? 'bg-red-100 text-red-700' :
            d.ai_confidence === 'medium' ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-700']">
            confidence {{ d.ai_confidence }}</span>
          <span v-if="dirty" class="badge bg-amber-400 text-amber-950">저장 안 됨</span>
        </div>
        <p class="text-xs text-slate-500 mt-0.5">
          <a v-if="d.feed" :href="d.feed.url" target="_blank" class="hover:underline">@{{ d.feed.author }} · 원본 열기 ↗</a>
          <a v-if="d.video_id" :href="`https://youtu.be/${d.video_id}`" target="_blank" class="ml-2 text-red-600 hover:underline">▶ youtu.be/{{ d.video_id }}</a>
        </p>
      </div>
      <button class="btn btn-sm" @click="emit('close')">닫기</button>
    </div>
    <StepBar :stages="d.stage_progress" />
    <p v-if="d.error" class="text-xs text-red-600 bg-red-50 border border-red-200 rounded p-2 whitespace-pre-wrap">{{ d.error }}</p>

    <!-- 작업 버튼 -->
    <div class="flex flex-wrap gap-2">
      <button v-if="d.status === 'captioned_review'" class="btn btn-primary" :disabled="busy"
        @click="act('approve', {}, '승인했습니다')">승인 후 업로드 대기열로</button>
      <button v-if="d.status.startsWith('failed')" class="btn btn-primary" :disabled="busy" @click="act('retry', {}, '재시도')">재시도</button>
      <button v-if="['captioned', 'captioned_review'].includes(d.status) || (d.publish_mode === 'scheduled' && schedulable)"
        class="btn" :disabled="busy || d.status === 'captioned_review'" @click="act('upload-now', {}, '즉시 업로드로 전환')">즉시 업로드</button>
      <button class="btn" :disabled="busy || isBusy || d.status === 'uploaded'" @click="act('skip', {}, '건너뛰었습니다')">건너뛰기</button>
      <button class="btn btn-danger" :disabled="busy || isBusy" @click="remove">삭제</button>
    </div>

    <div class="grid lg:grid-cols-[minmax(0,300px)_1fr] gap-4">
      <!-- 미리보기 -->
      <div class="space-y-2">
        <div class="flex gap-1">
          <button v-for="t in [['final', '완성본'], ['edited', '편집본'], ['source', '원본']]" :key="t[0]"
            :class="['btn btn-sm', videoTab === t[0] && 'btn-primary']" :disabled="!d.files[t[0]]" @click="videoTab = t[0]">{{ t[1] }}</button>
        </div>
        <video v-if="videoUrl" :key="videoUrl" :src="videoUrl" controls playsinline preload="metadata"
          class="w-full aspect-[9/16] bg-black rounded" />
        <div v-else class="w-full aspect-[9/16] bg-slate-200 rounded grid place-items-center text-sm text-slate-400">파일 없음</div>
      </div>

      <div class="space-y-4 min-w-0">
        <!-- 게시 -->
        <section class="card p-3 space-y-2">
          <h3 class="font-semibold text-sm">게시</h3>
          <p class="text-sm">
            <b>{{ d.publish_mode === 'scheduled' ? `예약 ${fmtFull(d.publish_at)}` : '즉시 게시' }}</b>
            <span v-if="d.slot_assigned" class="badge bg-indigo-100 text-indigo-700 ml-1">슬롯</span>
            <span class="text-xs text-slate-500 ml-2">업로드 방식: {{ d.upload_mode || store.status?.upload_mode }}</span>
          </p>
          <div v-if="schedulable" class="flex flex-wrap gap-2 items-center">
            <SchedulePicker v-model="newSchedule" small />
            <button class="btn btn-sm btn-primary" @click="setSchedule(false)">예약 시각 적용</button>
            <button v-if="d.publish_mode === 'scheduled'" class="btn btn-sm" @click="setSchedule(true)">즉시로 전환</button>
          </div>
          <p v-else class="text-xs text-slate-400">업로드가 시작된 뒤에는 바꿀 수 없습니다</p>
        </section>

        <!-- 편집 정보 -->
        <section class="card p-3 space-y-2">
          <h3 class="font-semibold text-sm">편집</h3>
          <p class="text-sm">프리셋 <b>{{ d.edit_preset }}</b> ·
            원본 {{ ep.source_duration?.toFixed?.(1) ?? d.source_duration?.toFixed?.(1) ?? '-' }}초 →
            편집본 {{ ep.edited_duration?.toFixed?.(1) ?? '-' }}초</p>
          <div v-if="ep.video" class="text-xs text-slate-600 flex flex-wrap gap-x-3 gap-y-1">
            <span>자르기 {{ ep.video.trim_start_sec }}초</span><span>속도 ×{{ ep.video.speed }}</span>
            <span>음성 {{ ep.video.keep_audio ? '유지' : '제거' }}</span><span>확대 {{ Math.round(ep.video.scale_factor * 100) }}%</span>
            <span>반전 {{ ep.video.hflip ? 'on' : 'off' }}</span>
            <span v-if="ep.intro_duration">인트로 {{ ep.intro_duration }}초</span>
            <span v-if="ep.outro_duration">아웃트로 {{ ep.outro_duration }}초</span>
            <span>자막 {{ ep.caption?.mode }} / {{ ep.caption?.position }}</span>
          </div>
          <div class="flex gap-2 items-center">
            <PresetPicker v-model="reeditPreset" class="w-40" small />
            <button class="btn btn-sm" :disabled="busy || isBusy || !d.files.source || d.status === 'uploaded'"
              @click="act('reedit', { edit_preset: reeditPreset }, '편집 다시 실행 (자막까지 이어서)')">편집 다시 실행</button>
          </div>
        </section>

        <!-- 메타데이터 -->
        <section class="card p-3 space-y-2">
          <div class="flex items-center">
            <h3 class="font-semibold text-sm flex-1">자막·메타데이터</h3>
            <button class="btn btn-sm btn-primary" :disabled="!dirty || isBusy" @click="saveMeta">저장</button>
          </div>
          <label class="block">
            <span class="label">하단 캡션 ({{ counts(form.caption) }}, 줄바꿈은 Enter) - 고치면 AI 호출 없이 합성만 다시</span>
            <textarea v-model="form.caption" rows="2" class="input font-medium" />
          </label>
          <label class="block"><span class="label">제목 ({{ counts(form.title) }})</span>
            <input v-model="form.title" class="input" /></label>
          <label class="block"><span class="label">설명 ({{ counts(form.description) }})</span>
            <textarea v-model="form.description" rows="3" class="input" /></label>
          <p v-if="creditHint" class="text-[11px] text-slate-500">업로드 시 설명 끝에 자동 추가: {{ creditHint }}</p>
          <label class="block"><span class="label">태그 (쉼표로 구분)</span>
            <input v-model="form.tags" class="input" /></label>
          <p v-if="d.summary" class="text-xs text-slate-500">요약(내부 기록): {{ d.summary }}</p>
        </section>

        <!-- AI -->
        <section class="card p-3 space-y-3">
          <h3 class="font-semibold text-sm">AI 자막</h3>
          <p class="text-xs text-slate-600">
            프로바이더 <b>{{ d.ai_provider_used || '-' }}</b> · 프롬프트 {{ d.prompt_profile || '-' }}@{{ d.prompt_version || '-' }}
            · 음성 {{ d.has_speech ? '있음' : '없음' }}
            <span v-if="d.stt?.reason" class="text-slate-400">({{ d.stt.reason }})</span>
            <span v-if="d.stt?.device" class="text-slate-400"> · STT {{ d.stt.device }}</span>
          </p>
          <div v-if="d.frames.length" class="flex gap-1 overflow-x-auto">
            <figure v-for="fr in d.frames" :key="fr.file" class="shrink-0">
              <img :src="fr.url" class="h-28 rounded border" loading="lazy" />
              <figcaption class="text-[10px] text-slate-500 text-center">{{ fr.time }}초{{ fr.scene ? ' ✦' : '' }}</figcaption>
            </figure>
          </div>
          <div v-if="d.segments.length" class="max-h-40 overflow-auto text-xs border rounded divide-y">
            <div v-for="(s, i) in d.segments" :key="i" class="px-2 py-1 flex gap-2">
              <span class="font-mono text-slate-400 shrink-0">{{ s.start.toFixed(1) }}–{{ s.end.toFixed(1) }}</span>
              <span>{{ s.text }}</span>
            </div>
          </div>
          <div class="flex flex-wrap gap-2 items-center">
            <input v-model="regen.instruction" class="input flex-1 min-w-[12rem]" placeholder="추가 지시 (예: 더 짧게, 반전을 강조)" />
            <select v-model="regen.profile" class="input w-40" title="다시 생성에 쓸 프롬프트 프로필">
              <option value="">{{ defaultProfile }} (설정된 기본)</option>
              <option v-for="p in profiles.filter(x => x !== defaultProfile)" :key="p" :value="p">{{ p }}</option>
            </select>
            <button class="btn btn-sm" :disabled="busy || isBusy || !d.files.edited || d.status === 'uploaded'"
              @click="act('recaption', { instruction: regen.instruction || null, profile: regen.profile || null }, 'AI 다시 생성')">다시 생성</button>
            <button class="btn btn-sm" :disabled="busy || isBusy || !aiRes || d.status === 'uploaded'"
              @click="act('rerender', {}, '자막만 다시 합성')">자막만 다시 합성</button>
          </div>
          <div v-if="aiRes">
            <button class="text-xs text-indigo-600 hover:underline" @click="showRaw = !showRaw">
              {{ showRaw ? '▾' : '▸' }} AI 원문 응답 ({{ aiRes.calls?.length || 0 }}회 호출, {{ ((aiRes.elapsed_ms || 0) / 1000).toFixed(1) }}초)</button>
            <div v-if="showRaw" class="mt-2 space-y-2">
              <div v-for="(c, i) in aiRes.calls || []" :key="i" class="border rounded p-2 text-xs">
                <p class="font-medium">{{ c.provider }} {{ c.model }}
                  <span v-if="c.skipped" class="text-slate-400">건너뜀: {{ c.skipped }}</span>
                  <span v-if="c.elapsed_ms" class="text-slate-400"> · {{ (c.elapsed_ms / 1000).toFixed(1) }}초</span>
                  <span v-if="c.error" class="text-red-600"> · {{ c.error }}</span></p>
                <p v-for="(fu, j) in c.followups || []" :key="j" class="text-amber-700">재요청: {{ fu }}</p>
                <pre v-if="c.raw" class="whitespace-pre-wrap bg-slate-50 p-1 mt-1 max-h-48 overflow-auto">{{ c.raw }}</pre>
              </div>
              <details class="text-xs"><summary class="cursor-pointer text-slate-500">보낸 프롬프트</summary>
                <pre class="whitespace-pre-wrap bg-slate-50 p-2 mt-1 max-h-72 overflow-auto">{{ aiRes.prompt?.system }}

---- user ----
{{ aiRes.prompt?.user }}</pre></details>
            </div>
          </div>
        </section>

        <!-- 이벤트 -->
        <section class="card p-3">
          <h3 class="font-semibold text-sm mb-2">이벤트 로그</h3>
          <ol class="relative border-l border-slate-200 ml-1 space-y-1.5 max-h-72 overflow-auto">
            <li v-for="e in d.events" :key="e.id" class="ml-3 text-xs">
              <span class="absolute -left-1 mt-1.5 w-2 h-2 rounded-full"
                :class="e.level === 'error' ? 'bg-red-500' : e.level === 'warning' ? 'bg-amber-500' : 'bg-slate-300'" />
              <span class="font-mono text-slate-400">{{ fmt(e.created_at, false) }}</span>
              <span class="text-slate-400 mx-1">[{{ e.worker }}]</span>
              <span :class="levelCls(e.level)" class="whitespace-pre-wrap">{{ e.message }}</span>
            </li>
          </ol>
        </section>
      </div>
    </div>
  </div>
  <p v-else class="text-slate-400">불러오는 중…</p>
</template>
