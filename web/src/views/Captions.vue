<script setup>
// 자막 탭: 다운로드만 한(보류) 영상을 내려받아 직접 편집 → 편집본을 올리면 자막·업로드 자동 처리.
// 피드와 상관없는 새 영상도 올릴 수 있다.
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../lib/api'
import { on } from '../lib/ws'
import { STATUS_LABEL, statusColor, store, toast, toastError } from '../lib/store'
import { ago, fmt, resolveSchedule } from '../lib/time'
import StepBar from '../components/StepBar.vue'
import SchedulePicker from '../components/SchedulePicker.vue'
import PresetPicker from '../components/PresetPicker.vue'

const held = ref([])
const recent = ref([])
const loading = ref(false)

// 항목별 옵션 (id → {preset, editVideo, schedule, file, pct})
const opts = reactive({})
function o(key) {
  if (!opts[key]) opts[key] = { preset: store.defaultPreset, editVideo: false, schedule: null, file: null, pct: null }
  return opts[key]
}

async function load() {
  loading.value = true
  try {
    const r = await api.get('/manual/items')
    held.value = r.held
    recent.value = r.recent
  } catch (e) { toastError(e) } finally { loading.value = false }
}

let t = null
const offs = []
onMounted(() => {
  load()
  offs.push(on('item', () => { clearTimeout(t); t = setTimeout(load, 700) }))
  offs.push(on('item_deleted', load), on('poll', load))
})
onBeforeUnmount(() => offs.forEach(f => f()))

// 진행률이 보이는 업로드 (fetch는 업로드 진행률을 주지 않음)
function postFile(url, opt) {
  const sch = resolveSchedule(opt.schedule)
  if (sch === null) { toast('선택한 시각이 이미 지났습니다', 'error'); return Promise.reject(new Error('past')) }
  const fd = new FormData()
  fd.append('file', opt.file)
  fd.append('edit_preset', opt.preset || '')
  fd.append('edit_video', opt.editVideo ? 'true' : 'false')
  fd.append('publish_at', sch.publish_at || '')
  fd.append('use_next_slot', sch.use_next_slot ? 'true' : 'false')
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.withCredentials = true
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) opt.pct = Math.round(e.loaded / e.total * 100) }
    xhr.onload = () => {
      opt.pct = null
      let data = null
      try { data = JSON.parse(xhr.responseText) } catch { /* 무시 */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data)
      else reject(new Error(data?.detail || `업로드 실패 (HTTP ${xhr.status})`))
    }
    xhr.onerror = () => { opt.pct = null; reject(new Error('네트워크 오류로 업로드하지 못했습니다')) }
    xhr.send(fd)
  })
}

async function uploadEdited(it) {
  const opt = o(it.id)
  if (!opt.file) return toast('올릴 영상 파일을 고르세요', 'error')
  try {
    await postFile(`/items/${it.id}/manual-video`, opt)
    opt.file = null
    toast('편집본을 올렸습니다 - 자막과 업로드를 자동으로 진행합니다', 'success')
    load()
  } catch (e) { if (e.message !== 'past') toastError(e) }
}

async function continueAuto(it) {
  const opt = o(it.id)
  const sch = resolveSchedule(opt.schedule)
  if (sch === null) return toast('선택한 시각이 이미 지났습니다', 'error')
  if (!confirm('편집본 없이 프리셋으로 자동 편집해 자막·업로드까지 진행할까요?')) return
  try {
    await api.post(`/items/${it.id}/continue`, { edit_preset: opt.preset || null, ...sch })
    toast('자동 처리를 시작했습니다', 'success')
    load()
  } catch (e) { toastError(e) }
}

async function approve(it) {
  try {
    await api.post(`/items/${it.id}/approve`)
    toast('업로드 대기열에 넣었습니다', 'success')
    load()
  } catch (e) { toastError(e) }
}

async function skip(it) {
  if (!confirm('이 항목을 건너뛸까요? (파이프라인에서 다시 볼 수 있음)')) return
  try { await api.post(`/items/${it.id}/skip`); load() } catch (e) { toastError(e) }
}

// 새 영상
const fresh = reactive({ preset: '', editVideo: false, schedule: null, file: null, pct: null })
const dragging = ref(false)
function onDrop(e) {
  dragging.value = false
  const f = e.dataTransfer.files?.[0]
  if (f) fresh.file = f
}
async function uploadFresh() {
  if (!fresh.file) return toast('올릴 영상 파일을 고르세요', 'error')
  try {
    await postFile('/manual/upload', fresh)
    fresh.file = null
    toast('영상을 올렸습니다 - 자막과 업로드를 자동으로 진행합니다', 'success')
    load()
  } catch (e) { if (e.message !== 'past') toastError(e) }
}
onMounted(() => { fresh.preset = store.defaultPreset })

const mb = (f) => f ? `${(f.size / 1e6).toFixed(1)}MB` : ''
const fileName = (it) => `${it.feed_author || 'reel'}_${it.id}.mp4`
</script>

<template>
  <div class="space-y-5">
    <!-- 새 영상 올리기 -->
    <section class="card p-4 space-y-3">
      <h2 class="font-semibold">새 영상 올리기 <span class="text-xs font-normal text-slate-500">- 올리면 자막을 달고 업로드까지 자동으로 진행합니다</span></h2>
      <label :class="['block border-2 border-dashed rounded-lg p-5 text-center cursor-pointer transition',
                      dragging ? 'border-indigo-500 bg-indigo-50' : 'border-slate-300 hover:border-indigo-400']"
        @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="onDrop">
        <input type="file" accept="video/*" class="hidden" @change="fresh.file = $event.target.files[0]" />
        <p v-if="fresh.file" class="text-sm font-medium">{{ fresh.file.name }} <span class="text-slate-400">{{ mb(fresh.file) }}</span></p>
        <p v-else class="text-sm text-slate-500">영상 파일을 끌어다 놓거나 눌러서 고르세요 (mp4, mov 등)</p>
      </label>
      <div class="flex flex-wrap gap-2 items-center">
        <span class="text-xs text-slate-500">자막 스타일</span>
        <PresetPicker v-model="fresh.preset" class="w-36" small />
        <label class="flex items-center gap-1 text-xs" title="끄면 자르기·속도 등은 적용하지 않고 1080x1920 규격만 맞춥니다">
          <input v-model="fresh.editVideo" type="checkbox" /> 프리셋 영상 편집도 적용</label>
        <span class="text-xs text-slate-500 ml-2">업로드</span>
        <SchedulePicker v-model="fresh.schedule" small />
        <button class="btn btn-primary btn-sm" :disabled="!fresh.file || fresh.pct !== null" @click="uploadFresh">
          {{ fresh.pct !== null ? `올리는 중 ${fresh.pct}%` : '올리기' }}</button>
      </div>
    </section>

    <!-- 다운로드만 한 영상 -->
    <section class="space-y-2">
      <h2 class="font-semibold">다운로드만 한 영상 <span class="text-xs font-normal text-slate-500">{{ held.length }}개 ·
        원본을 내려받아 직접 편집한 뒤 편집본을 올리세요</span></h2>
      <p v-if="!held.length && !loading" class="text-sm text-slate-400 card p-4">
        피드에서 "자동 업로드"를 끄고 가져온 영상이 다운로드되면 여기에 나타납니다.</p>
      <div class="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
        <div v-for="it in held" :key="it.id" class="card p-3 flex gap-3">
          <img v-if="it.thumb" :src="it.thumb" class="w-20 aspect-[9/16] object-cover rounded bg-slate-200 shrink-0" />
          <div v-else class="w-20 aspect-[9/16] rounded bg-slate-200 shrink-0" />
          <div class="flex-1 min-w-0 space-y-1.5">
            <div class="flex items-center gap-1 text-xs">
              <b class="truncate">#{{ it.id }} @{{ it.feed_author || '?' }}</b>
              <span class="text-slate-400 ml-auto shrink-0">{{ it.source_duration?.toFixed(1) }}초</span>
            </div>
            <p class="text-[11px] text-slate-500 line-clamp-2">{{ (it.feed_caption || '').slice(0, 80) }}</p>
            <div class="flex gap-1 flex-wrap">
              <a v-if="it.files.source" :href="it.files.source" :download="fileName(it)" class="btn btn-sm">⬇ 원본 내려받기</a>
              <a v-if="it.feed_url && !it.feed_url.startsWith('manual://')" :href="it.feed_url" target="_blank" class="btn btn-sm">인스타 ↗</a>
            </div>
            <label class="block">
              <input type="file" accept="video/*" class="text-[11px] w-full" @change="o(it.id).file = $event.target.files[0]" />
            </label>
            <div class="flex flex-wrap gap-1 items-center">
              <PresetPicker v-model="o(it.id).preset" class="w-28" small />
              <SchedulePicker v-model="o(it.id).schedule" small />
            </div>
            <label class="flex items-center gap-1 text-[11px]" title="끄면 자르기·속도 등은 적용하지 않고 규격만 맞춥니다">
              <input v-model="o(it.id).editVideo" type="checkbox" /> 프리셋 영상 편집도 적용</label>
            <div class="flex gap-1 flex-wrap">
              <button class="btn btn-primary btn-sm" :disabled="!o(it.id).file || o(it.id).pct !== null" @click="uploadEdited(it)">
                {{ o(it.id).pct !== null ? `올리는 중 ${o(it.id).pct}%` : '편집본 올리기 → 자막·업로드' }}</button>
              <button class="btn btn-sm" @click="continueAuto(it)">편집 없이 자동 처리</button>
              <button class="btn btn-sm text-slate-500" @click="skip(it)">건너뛰기</button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 직접 올린 영상 진행 상황 -->
    <section class="space-y-2">
      <h2 class="font-semibold">직접 올린 영상</h2>
      <p v-if="!recent.length" class="text-sm text-slate-400 card p-4">아직 올린 영상이 없습니다.</p>
      <div class="card divide-y">
        <div v-for="it in recent" :key="it.id" class="p-3 flex gap-3 items-center">
          <img v-if="it.thumb" :src="it.thumb" class="w-10 aspect-[9/16] object-cover rounded bg-slate-200 shrink-0" />
          <div class="flex-1 min-w-0 space-y-1">
            <div class="flex items-center gap-2 text-sm">
              <router-link :to="`/pipeline/${it.id}`" class="font-medium truncate hover:underline">
                #{{ it.id }} {{ it.title || it.filename || it.feed_author || '' }}</router-link>
              <span :class="['badge', statusColor(it.status)]">{{ STATUS_LABEL[it.status] }}</span>
              <span class="text-[11px] text-slate-400 ml-auto shrink-0">{{ ago(it.updated_at) }}</span>
            </div>
            <StepBar :stages="it.stage_progress" compact />
            <div class="flex gap-2 text-[11px] text-slate-500">
              <span>{{ it.publish_mode === 'scheduled' ? `예약 ${fmt(it.publish_at)}` : '즉시 게시' }}</span>
              <span>{{ it.edit_video ? '프리셋 편집 적용' : '규격만 맞춤' }} · 자막 {{ it.edit_preset }}</span>
              <button v-if="it.status === 'captioned_review'" class="btn btn-sm btn-primary py-0" @click="approve(it)">바로 올리기</button>
              <a v-if="it.video_id" :href="`https://youtu.be/${it.video_id}`" target="_blank" class="text-red-600">▶ 유튜브</a>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
