<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api, qs } from '../lib/api'
import { loadPresets, store, toast, toastError } from '../lib/store'
import { clone, same, useDirty } from '../lib/dirty'
import HistoryPanel from '../components/HistoryPanel.vue'
import Modal from '../components/Modal.vue'

const name = ref('')
const preset = ref(null)
const original = ref(null)
const errors = ref({})
const tab = ref('video')
const fonts = ref([])
const assets = ref([])
const items = ref([])
const itemId = ref(null)
const estimate = ref(null)
const preview = ref(null)
const previewBusy = ref(false)
const historyOpen = ref(false)
const nameModal = ref({ open: false, mode: '', value: '' })

const { dirty } = useDirty('preset', () => preset.value && !same(preset.value, original.value))

async function loadPreset(n) {
  try {
    const r = await api.get(`/presets/edit/${encodeURIComponent(n)}`)
    preset.value = r.preset
    original.value = clone(r.preset)
    name.value = n
    errors.value = {}
    preview.value = null
  } catch (e) { toastError(e) }
}
async function switchTo(n) {
  if (n === name.value) return
  if (dirty.value && !confirm('저장하지 않은 변경이 있습니다. 다른 프리셋으로 바꿀까요?')) return
  await loadPreset(n)
}

onMounted(async () => {
  await loadPresets()
  await loadPreset(store.defaultPreset || 'default')
  const [f, a, it] = await Promise.allSettled([
    api.get('/system/fonts'), api.get('/config/assets'),
    api.get('/items' + qs({ status: 'downloaded,edited,captioned,captioned_review,uploaded,failed_edit,failed_caption,failed_upload', limit: 50 })),
  ])
  if (f.status === 'fulfilled') fonts.value = f.value.fonts
  if (a.status === 'fulfilled') assets.value = a.value.assets
  if (it.status === 'fulfilled') {
    items.value = it.value.items.filter(x => x.source_duration)
    itemId.value = items.value[0]?.id ?? null
  }
})

async function save() {
  try {
    const r = await api.put(`/presets/edit/${encodeURIComponent(name.value)}`, { preset: preset.value })
    preset.value = r.preset
    original.value = clone(r.preset)
    errors.value = {}
    toast('저장했습니다 (새로 처리되는 항목부터 적용)', 'success')
  } catch (e) {
    errors.value = e.fields || {}
    toastError(e)
  }
}
function revert() { preset.value = clone(original.value); errors.value = {} }

function openName(mode) {
  nameModal.value = { open: true, mode, value: mode === 'rename' ? name.value : '' }
}
async function submitName() {
  const { mode, value } = nameModal.value
  try {
    if (mode === 'new') await api.post('/presets/edit', { name: value })
    else if (mode === 'clone') await api.post(`/presets/edit/${encodeURIComponent(name.value)}/clone`, { name: value })
    else await api.post(`/presets/edit/${encodeURIComponent(name.value)}/rename`, { name: value })
    nameModal.value.open = false
    await loadPresets()
    original.value = preset.value     // 전환 확인창 방지
    await loadPreset(value)
    toast('완료', 'success')
  } catch (e) { toastError(e) }
}
async function remove() {
  if (!confirm(`프리셋 '${name.value}' 을 삭제할까요?`)) return
  try {
    await api.del(`/presets/edit/${encodeURIComponent(name.value)}`)
  } catch (e) {
    if (e.status === 409 && confirm(e.message)) {
      try { await api.del(`/presets/edit/${encodeURIComponent(name.value)}?force=true`) } catch (e2) { return toastError(e2) }
    } else return e.status === 409 ? null : toastError(e)
  }
  await loadPresets()
  await loadPreset('default')
  toast('삭제했습니다')
}
async function makeDefault() {
  try {
    await api.post(`/presets/edit/${encodeURIComponent(name.value)}/default`)
    await loadPresets()
    toast('기본 프리셋으로 지정했습니다', 'success')
  } catch (e) { toastError(e) }
}

// 결과 길이 예상
let estTimer = null
watch(() => [itemId.value, preset.value?.video], () => {
  clearTimeout(estTimer)
  estTimer = setTimeout(async () => {
    if (!itemId.value || !preset.value) return (estimate.value = null)
    try {
      estimate.value = (await api.post('/edit/estimate', { item_id: itemId.value, preset: name.value, overrides: preset.value })).plan
    } catch (e) { estimate.value = { error: e.message } }
  }, 300)
}, { deep: true })

async function runPreview() {
  if (!itemId.value) return toast('미리볼 항목을 고르세요', 'error')
  previewBusy.value = true
  try {
    const r = await api.post('/edit/preview', { item_id: itemId.value, preset: name.value, overrides: preset.value })
    preview.value = r.url + '?t=' + Date.now()
  } catch (e) {
    errors.value = e.fields || errors.value
    toastError(e)
  } finally { previewBusy.value = false }
}

async function uploadAsset(e, key) {
  const file = e.target.files[0]
  if (!file) return
  try {
    const r = await api.upload('/config/assets', file)
    assets.value = [...new Set([...assets.value, r.path])]
    if (key === 'font_path') preset.value.caption.font_path = r.path
    else preset.value.video[key] = r.path
    if (key === 'font_path') fonts.value.push({ path: r.path, name: `(업로드) ${file.name}` })
    toast('업로드했습니다', 'success')
  } catch (err) { toastError(err) }
  e.target.value = ''
}

const v = computed(() => preset.value?.video)
const c = computed(() => preset.value?.caption)
const pct = (x) => Math.round(x * 100)

// 오버레이 미리보기 좌표 (9:16, 1920 기준 비율)
const capStyle = computed(() => {
  if (!c.value) return {}
  const side = c.value.side_margin_ratio ?? 0.037
  const base = { maxWidth: `${Math.min(c.value.max_width_ratio, 1 - 2 * side) * 100}%`, fontSize: `${c.value.font_size / 1920 * 100 * 1.78}cqh`,
    color: c.value.color, WebkitTextStroke: `${c.value.outline_width / 4}px ${c.value.outline_color}`,
    background: c.value.box ? hexA(c.value.box_color, c.value.box_opacity) : 'transparent' }
  if (c.value.position === 'upper') return { ...base, top: '12%' }
  if (c.value.position === 'center') return { ...base, top: '50%', transform: 'translate(-50%, -50%)' }
  return { ...base, bottom: `${c.value.bottom_margin_ratio * 100}%` }
})
function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`
}
const err = (k) => errors.value[k]
</script>

<template>
  <div v-if="preset" class="space-y-4">
    <!-- 프리셋 관리 -->
    <div class="card p-3 flex flex-wrap gap-2 items-center">
      <select class="input w-44" :value="name" @change="switchTo($event.target.value)">
        <option v-for="p in store.presets" :key="p.name" :value="p.name">{{ p.name }}{{ p.default ? ' (기본)' : '' }}</option>
      </select>
      <button class="btn btn-sm" @click="openName('new')">새로 만들기</button>
      <button class="btn btn-sm" @click="openName('clone')">복제</button>
      <button class="btn btn-sm" :disabled="name === 'default'" @click="openName('rename')">이름 변경</button>
      <button class="btn btn-sm btn-danger" :disabled="name === 'default'" @click="remove">삭제</button>
      <button class="btn btn-sm" :disabled="store.defaultPreset === name" @click="makeDefault">기본 프리셋으로 지정</button>
      <button class="btn btn-sm" @click="historyOpen = true">버전 이력</button>
      <span class="flex-1" />
      <span v-if="dirty" class="badge bg-amber-400 text-amber-950">저장 안 됨</span>
      <button class="btn btn-sm" :disabled="!dirty" @click="revert">되돌리기</button>
      <button class="btn btn-primary" :disabled="!dirty" @click="save">저장</button>
    </div>

    <div class="grid lg:grid-cols-[1fr_320px] gap-4">
      <div class="card">
        <div class="flex border-b px-2">
          <button :class="['tab', tab === 'video' && 'tab-active']" @click="tab = 'video'">영상</button>
          <button :class="['tab', tab === 'caption' && 'tab-active']" @click="tab = 'caption'">자막</button>
        </div>

        <!-- 영상 탭: 적용 순서대로 -->
        <div v-if="tab === 'video'" class="p-4 space-y-5">
          <div>
            <label class="label">1. 앞부분 자르기 (초)</label>
            <input v-model.number="v.trim_start_sec" type="number" step="0.1" min="0" class="input w-32" />
            <p v-if="err('video.trim_start_sec')" class="field-error">{{ err('video.trim_start_sec') }}</p>
          </div>
          <div>
            <label class="label">2. 재생 속도 <b class="text-slate-800">×{{ v.speed.toFixed(2) }}</b></label>
            <input v-model.number="v.speed" type="range" min="0.5" max="2" step="0.05" class="w-full max-w-md" />
            <label class="flex items-center gap-2 text-sm mt-1"><input v-model="v.keep_audio" type="checkbox" /> 음성 유지 (끄면 음성 제거)</label>
            <p v-if="err('video.speed')" class="field-error">{{ err('video.speed') }}</p>
          </div>
          <div>
            <label class="label">3. 확대 비율 <b class="text-slate-800">{{ pct(v.scale_factor) }}%</b> (확대 후 중앙 크롭)</label>
            <input :value="pct(v.scale_factor)" type="range" min="100" max="150" step="1" class="w-full max-w-md"
              @input="v.scale_factor = +$event.target.value / 100" />
          </div>
          <label class="flex items-center gap-2 text-sm"><input v-model="v.hflip" type="checkbox" /> 4. 좌우반전</label>
          <div class="grid sm:grid-cols-3 gap-3">
            <div v-for="k in [['watermark_path', '5. 워터마크 (PNG)'], ['intro_path', '6. 인트로 영상'], ['outro_path', '6. 아웃트로 영상']]" :key="k[0]">
              <label class="label">{{ k[1] }}</label>
              <select v-model="v[k[0]]" class="input">
                <option :value="null">없음</option>
                <option v-for="a in assets" :key="a" :value="a">{{ a.replace('assets/', '') }}</option>
              </select>
              <input type="file" class="text-xs mt-1 w-full" :accept="k[0] === 'watermark_path' ? 'image/*' : 'video/*'"
                @change="uploadAsset($event, k[0])" />
              <p v-if="err('video.' + k[0])" class="field-error">{{ err('video.' + k[0]) }}</p>
            </div>
          </div>
          <div>
            <label class="label">워터마크 위치</label>
            <select v-model="v.watermark_position" class="input w-48">
              <option v-for="p in ['top_left', 'top_right', 'bottom_left', 'bottom_right', 'center']" :key="p" :value="p">{{ p }}</option>
            </select>
          </div>
        </div>

        <!-- 자막 탭 -->
        <div v-else class="p-4 grid sm:grid-cols-2 gap-4">
          <div>
            <label class="label">모드</label>
            <select v-model="c.mode" class="input">
              <option value="summary">summary - AI 캡션 고정 표시</option>
              <option value="speech">speech - 대사 자막 (음성 없으면 summary)</option>
              <option value="both">both - 캡션 위쪽 + 대사 하단</option>
            </select>
          </div>
          <div>
            <label class="label">위치</label>
            <select v-model="c.position" class="input">
              <option value="lower_safe">lower_safe - 쇼츠 UI 위 하단</option>
              <option value="upper">upper - 상단 12%</option>
              <option value="center">center - 가운데</option>
            </select>
          </div>
          <div class="sm:col-span-2">
            <label class="label">하단 여백 <b class="text-slate-800">{{ pct(c.bottom_margin_ratio) }}%</b>
              (lower_safe 기준선. 20% 미만이면 쇼츠 채널명·제목 영역과 겹칠 수 있음)</label>
            <input :value="pct(c.bottom_margin_ratio)" type="range" min="0" max="40" step="1" class="w-full max-w-md"
              @input="c.bottom_margin_ratio = +$event.target.value / 100" />
            <p v-if="c.bottom_margin_ratio < 0.2" class="text-[11px] text-amber-600">쇼츠 하단 UI(20%)와 겹치는 위치입니다</p>
          </div>
          <div class="sm:col-span-2">
            <label class="label">좌우 여백 <b class="text-slate-800">{{ (c.side_margin_ratio * 100).toFixed(1) }}%</b>
              ({{ Math.round(c.side_margin_ratio * 1080) }}px) · 한 줄 폭은 최대 폭과 (100% − 좌우 여백×2) 중 작은 값</label>
            <input :value="Math.round(c.side_margin_ratio * 1000) / 10" type="range" min="0" max="20" step="0.5" class="w-full max-w-md"
              @input="c.side_margin_ratio = +$event.target.value / 100" />
            <p v-if="err('caption.side_margin_ratio')" class="field-error">{{ err('caption.side_margin_ratio') }}</p>
          </div>
          <div>
            <label class="label">표시 시작 (초, 비우면 인트로 이후)</label>
            <input :value="c.start_sec ?? ''" type="number" step="0.1" min="0" class="input"
              @input="c.start_sec = $event.target.value === '' ? null : +$event.target.value" />
            <p v-if="err('caption.start_sec')" class="field-error">{{ err('caption.start_sec') }}</p>
          </div>
          <div>
            <label class="label">표시 끝 (초, 비우면 아웃트로 전까지)</label>
            <input :value="c.end_sec ?? ''" type="number" step="0.1" min="0" class="input"
              @input="c.end_sec = $event.target.value === '' ? null : +$event.target.value" />
            <p v-if="err('caption.end_sec')" class="field-error">{{ err('caption.end_sec') }}</p>
          </div>
          <div class="sm:col-span-2">
            <label class="label">폰트</label>
            <div class="flex gap-2">
              <select v-model="c.font_path" class="input">
                <option v-if="!fonts.some(f => f.path === c.font_path)" :value="c.font_path">{{ c.font_path }}</option>
                <option v-for="f in fonts" :key="f.path" :value="f.path">{{ f.name }}</option>
              </select>
              <label class="btn btn-sm cursor-pointer">업로드<input type="file" accept=".ttf,.otf,.ttc" class="hidden"
                @change="uploadAsset($event, 'font_path')" /></label>
            </div>
            <p v-if="err('caption.font_path')" class="field-error">{{ err('caption.font_path') }}</p>
          </div>
          <div>
            <label class="label">크기 / 최소 크기 (px, 1080 폭 기준)</label>
            <div class="flex gap-2">
              <input v-model.number="c.font_size" type="number" class="input" />
              <input v-model.number="c.min_font_size" type="number" class="input" />
            </div>
            <p v-if="err('caption.min_font_size')" class="field-error">{{ err('caption.min_font_size') }}</p>
          </div>
          <div>
            <label class="label">최대 줄 수 (넘치면 글자를 줄이고, 그래도 넘치면 말줄임)</label>
            <input v-model.number="c.max_lines" type="number" min="1" max="8" class="input w-24" />
            <p v-if="err('caption.max_lines')" class="field-error">{{ err('caption.max_lines') }}</p>
          </div>
          <div>
            <label class="label">최대 폭 {{ pct(c.max_width_ratio) }}%</label>
            <input :value="pct(c.max_width_ratio)" type="range" min="50" max="100" class="w-full"
              @input="c.max_width_ratio = +$event.target.value / 100" />
          </div>
          <div class="flex gap-3 items-end">
            <label class="text-xs">글자색<input v-model="c.color" type="color" class="block w-12 h-8" /></label>
            <label class="text-xs">외곽선<input v-model="c.outline_color" type="color" class="block w-12 h-8" /></label>
            <label class="text-xs flex-1">외곽선 두께 {{ c.outline_width }}<input v-model.number="c.outline_width" type="range" min="0" max="12" class="w-full" /></label>
          </div>
          <div class="flex gap-3 items-end">
            <label class="text-xs flex items-center gap-1"><input v-model="c.box" type="checkbox" /> 배경 박스</label>
            <label class="text-xs">박스 색<input v-model="c.box_color" type="color" class="block w-12 h-8" :disabled="!c.box" /></label>
            <label class="text-xs flex-1">투명도 {{ pct(c.box_opacity) }}%<input :value="pct(c.box_opacity)" type="range" min="0" max="100"
              class="w-full" :disabled="!c.box" @input="c.box_opacity = +$event.target.value / 100" /></label>
          </div>
          <div>
            <label class="label">페이드 (ms)</label>
            <input v-model.number="c.fade_ms" type="number" step="50" min="0" class="input w-32" />
          </div>
        </div>
      </div>

      <!-- 오른쪽: 미리보기 -->
      <div class="space-y-3">
        <div class="card p-3 space-y-2">
          <label class="label">미리볼 항목</label>
          <select v-model="itemId" class="input">
            <option v-if="!items.length" :value="null">다운로드된 항목이 없습니다</option>
            <option v-for="it in items" :key="it.id" :value="it.id">#{{ it.id }} {{ it.title || it.feed_author }} ({{ it.source_duration?.toFixed(1) }}초)</option>
          </select>
          <p v-if="estimate && !estimate.error" class="text-sm">
            원본 <b>{{ estimate.source_duration.toFixed(1) }}초</b> → 편집본 <b>{{ estimate.expected_duration.toFixed(1) }}초</b></p>
          <p v-else-if="estimate?.error" class="field-error">{{ estimate.error }}</p>
          <button class="btn btn-primary w-full" :disabled="previewBusy || !itemId" @click="runPreview">
            {{ previewBusy ? '만드는 중…' : '미리보기 (5초 샘플, 저장 안 한 값 포함)' }}</button>
          <video v-if="preview" :src="preview" controls autoplay playsinline loop class="w-full aspect-[9/16] bg-black rounded" />
        </div>
        <!-- 쇼츠 UI 오버레이 -->
        <div class="card p-3">
          <p class="label">쇼츠 화면 배치 (회색: 채널명·버튼 오버레이 영역)</p>
          <div class="relative mx-auto aspect-[9/16] w-56 bg-gradient-to-b from-slate-600 to-slate-800 rounded overflow-hidden"
            style="container-type: size">
            <div class="absolute inset-x-0 bottom-0 h-[20%] bg-black/45 border-t border-dashed border-white/40 text-[9px] text-white/70 p-1">
              @채널명 · 제목 · 구독</div>
            <div class="absolute right-1 bottom-[22%] flex flex-col gap-2">
              <span v-for="i in 4" :key="i" class="w-5 h-5 rounded-full bg-white/30" />
            </div>
            <div class="absolute left-1/2 -translate-x-1/2 text-center font-bold leading-tight px-1 rounded whitespace-pre-line"
              :style="capStyle">자막 미리보기{{ '\n' }}샘플 문장입니다</div>
          </div>
        </div>
      </div>
    </div>

    <HistoryPanel v-model="historyOpen" :title="`프리셋 '${name}' 버전 이력`"
      :list-url="`/presets/edit/${encodeURIComponent(name)}/history`"
      :version-url="(ver) => `/presets/edit/${encodeURIComponent(name)}/history/${encodeURIComponent(ver)}`"
      :restore-url="(ver) => `/presets/edit/${encodeURIComponent(name)}/restore/${encodeURIComponent(ver)}`"
      @restored="loadPreset(name)" />

    <Modal v-model="nameModal.open" :title="{ new: '새 프리셋', clone: `'${name}' 복제`, rename: '이름 변경' }[nameModal.mode]">
      <input v-model="nameModal.value" class="input" placeholder="프리셋 이름 (한글·영문·숫자·_-)" @keyup.enter="submitName" />
      <template #footer>
        <button class="btn" @click="nameModal.open = false">취소</button>
        <button class="btn btn-primary" :disabled="!nameModal.value" @click="submitName">확인</button>
      </template>
    </Modal>
  </div>
  <p v-else class="text-slate-400">불러오는 중…</p>
</template>
