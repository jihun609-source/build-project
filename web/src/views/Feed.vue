<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api, qs } from '../lib/api'
import { on } from '../lib/ws'
import { STATUS_LABEL, statusColor, store, toast, toastError } from '../lib/store'
import { ago, fmt, resolveSchedule, scheduleLabel } from '../lib/time'
import StepBar from '../components/StepBar.vue'
import SchedulePicker from '../components/SchedulePicker.vue'
import PresetPicker from '../components/PresetPicker.vue'
import Modal from '../components/Modal.vue'

const rows = ref([])
const counts = ref({})
const loading = ref(false)
const more = ref(true)
const f = reactive({ status: 'seen', author: '', date: '', q: '' })
const PAGE = 60

// 카드별 선택값 (편집 프리셋·업로드 시각)
const choice = reactive({})
function ch(id) {
  // auto: 자동 업로드. 끄면 다운로드만 하고 자막 탭에서 이어서 처리
  if (!choice[id]) choice[id] = { preset: store.defaultPreset, schedule: null, auto: false }
  return choice[id]
}

const checked = ref([])            // 체크한 순서 유지
const bulk = reactive({ preset: '', schedule: null, sequential: false, auto: false })

async function load(append = false) {
  loading.value = true
  try {
    const r = await api.get('/feed/items' + qs({ ...f, limit: PAGE, offset: append ? rows.value.length : 0 }))
    rows.value = append ? [...rows.value, ...r.items] : r.items
    counts.value = r.counts
    more.value = r.items.length === PAGE
  } catch (e) { toastError(e) } finally { loading.value = false }
}

function matchesFilter(r) {
  if (f.status && r.status !== f.status) return false
  if (f.author && !(r.author || '').includes(f.author.replace('@', ''))) return false
  if (f.q && !(r.caption || '').includes(f.q)) return false
  return true
}
function upsert(r) {
  if (!r) return
  const i = rows.value.findIndex(x => x.id === r.id)
  if (i >= 0) {
    // 상태 필터에서 벗어나도 방금 조작한 카드는 진행 표시를 보이도록 유지
    rows.value[i] = r
  } else if (matchesFilter(r) && !f.date) {
    rows.value.unshift(r)
  }
}
const offs = []
onMounted(() => {
  bulk.preset = store.defaultPreset
  load()
  offs.push(on('feed', upsert))
  offs.push(on('item', (it) => {
    const row = rows.value.find(x => x.id === it.feed_item_id)
    if (row && (!row.item || row.item.id <= it.id)) row.item = it
  }))
  offs.push(on('item_deleted', (d) => {
    const row = rows.value.find(x => x.id === d.feed_item_id)
    if (row && row.item?.id === d.id) row.item = null
  }))
  offs.push(on('poll', () => load()))
})
onBeforeUnmount(() => offs.forEach(x => x()))

function toggle(id) {
  const i = checked.value.indexOf(id)
  if (i >= 0) checked.value.splice(i, 1)
  else checked.value.push(id)
}
const selectable = computed(() => rows.value.filter(r => r.status === 'seen'))
function checkAll() {
  checked.value = checked.value.length ? [] : selectable.value.map(r => r.id)
}

async function fetchOne(r) {
  const c = ch(r.id)
  const sch = c.auto ? resolveSchedule(c.schedule) : { publish_at: null, use_next_slot: false }
  if (sch === null) return toast('선택한 시각이 이미 지났습니다', 'error')
  try {
    const res = await api.post(`/feed/${r.id}/fetch`, { edit_preset: c.preset || null, auto_upload: c.auto, ...sch })
    upsert(res.feed)
    toast(c.auto ? '가져오기 시작 (자동 업로드)' : '다운로드만 시작 - 끝나면 자막 탭에서 이어서 처리', 'success')
  } catch (e) { toastError(e) }
}
async function bulkFetch() {
  const sch = !bulk.auto || bulk.sequential ? { publish_at: null } : resolveSchedule(bulk.schedule)
  if (sch === null) return toast('선택한 시각이 이미 지났습니다', 'error')
  try {
    const res = await api.post('/feed/bulk', {
      ids: checked.value, edit_preset: bulk.preset || null, auto_upload: bulk.auto,
      sequential_slots: bulk.auto && bulk.sequential, ...sch,
    })
    const ok = res.results.filter(x => x.ok).length
    const bad = res.results.filter(x => !x.ok)
    toast(`${ok}건 가져오기` + (bad.length ? `, ${bad.length}건 실패: ${bad[0].error}` : ''), bad.length ? 'error' : 'success')
    checked.value = []
    load()
  } catch (e) { toastError(e) }
}
async function bulkExclude() {
  try {
    await api.post('/feed/bulk', { ids: checked.value, action: 'exclude' })
    checked.value = []
    load()
  } catch (e) { toastError(e) }
}
async function exclude(r) {
  try { upsert(await api.post(`/feed/${r.id}/exclude`)) } catch (e) { toastError(e) }
}
async function unexclude(r) {
  try { upsert(await api.post(`/feed/${r.id}/restore`)) } catch (e) { toastError(e) }
}
async function retry(r) {
  try { await api.post(`/items/${r.item.id}/retry`) } catch (e) { toastError(e) }
}

// 진행 중 카드의 예약 시각 변경
const sched = reactive({ open: false, row: null, value: null })
function openSchedule(r) {
  if (['uploading', 'uploaded', 'skipped'].includes(r.item.status)) return
  sched.row = r
  sched.value = null
  sched.open = true
}
async function saveSchedule(immediate = false) {
  const sch = immediate ? { publish_at: null, use_next_slot: false } : resolveSchedule(sched.value)
  if (sch === null) return toast('선택한 시각이 이미 지났습니다', 'error')
  try {
    upsert(await api.patch(`/feed/${sched.row.id}/schedule`, sch))
    sched.open = false
    toast('게시 시각을 바꿨습니다', 'success')
  } catch (e) { toastError(e) }
}

function thumbError(e) { e.target.style.display = 'none' }
</script>

<template>
  <div class="space-y-3">
    <!-- 필터 -->
    <div class="card p-3 flex flex-wrap gap-2 items-end">
      <div class="flex gap-1 flex-wrap">
        <button v-for="s in ['seen', 'fetched', 'excluded', '']" :key="s" @click="f.status = s; load()"
          :class="['btn btn-sm', f.status === s && 'btn-primary']">
          {{ s ? STATUS_LABEL[s] : '전체' }} <span class="opacity-70">{{ s ? counts[s] || 0 : '' }}</span>
        </button>
      </div>
      <input v-model="f.author" class="input w-32" placeholder="작성자" @keyup.enter="load()" />
      <input v-model="f.date" class="input w-40" type="date" @change="load()" />
      <input v-model="f.q" class="input w-44" placeholder="캡션 검색" @keyup.enter="load()" />
      <button class="btn" @click="load()">검색</button>
      <span class="flex-1" />
      <button class="btn btn-sm" @click="checkAll">{{ checked.length ? '선택 해제' : '기록됨 전체 선택' }}</button>
    </div>

    <!-- 일괄 가져오기 -->
    <div v-if="checked.length" class="card p-3 sticky top-12 z-10 border-indigo-300 bg-indigo-50 flex flex-wrap gap-2 items-center">
      <span class="text-sm font-semibold">{{ checked.length }}개 선택</span>
      <label class="flex items-center gap-1 text-sm font-medium">
        <input v-model="bulk.auto" type="checkbox" /> 자동 업로드
      </label>
      <PresetPicker v-model="bulk.preset" class="w-36" small />
      <template v-if="bulk.auto">
        <SchedulePicker v-if="!bulk.sequential" v-model="bulk.schedule" small />
        <label class="flex items-center gap-1 text-sm">
          <input v-model="bulk.sequential" type="checkbox" /> 슬롯에 순차 배정
        </label>
      </template>
      <span v-else class="text-xs text-slate-500">다운로드만 → 자막 탭에서 이어서</span>
      <button class="btn btn-primary btn-sm" @click="bulkFetch">일괄 가져오기</button>
      <button class="btn btn-sm" @click="bulkExclude">제외</button>
    </div>

    <p v-if="!rows.length && !loading" class="text-slate-400 text-sm p-6 text-center">
      기록된 릴스가 없습니다. 크롬 확장프로그램을 켜고 인스타그램 릴스를 보면 여기에 쌓입니다.
    </p>

    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
      <div v-for="r in rows" :key="r.id"
        :class="['card overflow-hidden flex flex-col', checked.includes(r.id) && 'ring-2 ring-indigo-500']">
        <div class="relative aspect-[9/16] max-h-72 bg-slate-800">
          <a :href="r.url" target="_blank" rel="noopener" class="absolute inset-0">
            <span class="absolute inset-0 grid place-items-center text-slate-500 text-xs">썸네일 받는 중…</span>
            <img v-if="r.thumb" :src="r.thumb" loading="lazy" class="relative w-full h-full object-cover" @error="thumbError" />
          </a>
          <label v-if="r.status === 'seen'" class="absolute top-1.5 left-1.5 bg-white/90 rounded p-1 cursor-pointer" @click.stop>
            <input type="checkbox" :checked="checked.includes(r.id)" @change="toggle(r.id)" />
            <span v-if="checked.includes(r.id)" class="text-[10px] font-bold text-indigo-700 ml-0.5">{{ checked.indexOf(r.id) + 1 }}</span>
          </label>
          <span :class="['badge absolute top-1.5 right-1.5', statusColor(r.item?.status || r.status)]">
            {{ STATUS_LABEL[r.item?.status || r.status] }}</span>
        </div>
        <div class="p-2 space-y-1.5 flex-1 flex flex-col">
          <div class="flex items-center gap-1 text-xs">
            <span class="font-semibold truncate">@{{ r.author || '?' }}</span>
            <span class="text-slate-400 ml-auto shrink-0">{{ ago(r.seen_at) }}</span>
          </div>
          <p class="text-xs text-slate-600 line-clamp-2 min-h-[2rem]" :title="r.caption">{{ (r.caption || '').slice(0, 60) }}</p>
          <span class="flex-1" />

          <!-- 가져오기 전 -->
          <template v-if="!r.item">
            <div v-if="r.status !== 'excluded'" class="space-y-1">
              <label class="flex items-center gap-1 text-xs font-medium cursor-pointer">
                <input v-model="ch(r.id).auto" type="checkbox" /> 자동 업로드
              </label>
              <PresetPicker v-model="ch(r.id).preset" small />
              <SchedulePicker v-if="ch(r.id).auto" v-model="ch(r.id).schedule" small />
              <div class="flex gap-1 flex-wrap text-[10px]">
                <span class="badge bg-slate-100">✂ {{ ch(r.id).preset }}</span>
                <span v-if="ch(r.id).auto" class="badge bg-slate-100">⏱ {{ scheduleLabel(ch(r.id).schedule) }}</span>
                <span v-else class="badge bg-sky-100 text-sky-700">⬇ 다운로드만</span>
              </div>
              <div class="flex gap-1">
                <button class="btn btn-primary btn-sm flex-1" @click="fetchOne(r)">{{ ch(r.id).auto ? '가져오기' : '다운로드' }}</button>
                <button class="btn btn-sm" title="제외" @click="exclude(r)">제외</button>
              </div>
            </div>
            <button v-else class="btn btn-sm" @click="unexclude(r)">제외 취소</button>
          </template>

          <!-- 진행 표시 -->
          <template v-else>
            <StepBar :stages="r.item.stage_progress" compact />
            <div class="flex flex-wrap gap-1 items-center text-[11px]">
              <span class="badge bg-slate-100">✂ {{ r.item.edit_preset }}</span>
              <button v-if="r.item.status !== 'held'" :class="['badge', r.item.publish_mode === 'scheduled' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100',
                               ['uploading', 'uploaded', 'skipped'].includes(r.item.status) ? 'cursor-default' : 'hover:ring-1 ring-indigo-400']"
                :title="['uploading', 'uploaded'].includes(r.item.status) ? '' : '클릭해 게시 시각 변경'" @click="openSchedule(r)">
                ⏱ {{ r.item.publish_mode === 'scheduled' ? fmt(r.item.publish_at) : '즉시' }}
              </button>
            </div>
            <div class="flex gap-1">
              <router-link v-if="r.item.status === 'held'" to="/captions"
                class="btn btn-sm flex-1 bg-sky-100 border-sky-300">자막 탭에서 이어서</router-link>
              <router-link v-if="r.item.status === 'captioned_review'" :to="`/pipeline/${r.item.id}`"
                class="btn btn-sm flex-1 bg-amber-100 border-amber-300">검토</router-link>
              <button v-if="r.item.status.startsWith('failed')" class="btn btn-sm btn-danger flex-1" @click="retry(r)">재시도</button>
              <a v-if="r.item.video_id" :href="`https://youtu.be/${r.item.video_id}`" target="_blank"
                class="btn btn-sm flex-1 text-red-600">▶ 유튜브</a>
              <router-link :to="`/pipeline/${r.item.id}`" class="btn btn-sm">상세</router-link>
            </div>
            <p v-if="r.item.video_id && r.item.publish_mode === 'scheduled'" class="text-[11px] text-indigo-700">
              게시 예정 {{ fmt(r.item.publish_at) }}</p>
          </template>
        </div>
      </div>
    </div>
    <div class="text-center">
      <button v-if="more && rows.length" class="btn" :disabled="loading" @click="load(true)">더 보기</button>
    </div>

    <Modal v-model="sched.open" title="게시 시각 변경">
      <p class="text-sm text-slate-500 mb-2">업로드가 시작되기 전까지 바꿀 수 있습니다.
        현재: <b>{{ sched.row?.item?.publish_mode === 'scheduled' ? fmt(sched.row.item.publish_at) : '즉시' }}</b></p>
      <SchedulePicker v-model="sched.value" />
      <template #footer>
        <button class="btn" @click="saveSchedule(true)">즉시로 전환</button>
        <button class="btn btn-primary" @click="saveSchedule(false)">저장</button>
      </template>
    </Modal>
  </div>
</template>
