<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../lib/api'
import { loadCommon, store, toast, toastError } from '../lib/store'
import { clone, same, useDirty } from '../lib/dirty'
import { fmt } from '../lib/time'
import CodeEditor from '../components/CodeEditor.vue'
import YoutubeClientBox from '../components/YoutubeClientBox.vue'

const route = useRoute()
const cfg = ref(null)
const orig = ref(null)
const errors = ref({})
const preview = ref([])
const bridge = ref(null)
const selectors = ref('')
const selectorsOrig = ref('')
const extReply = ref(null)
const seleniumState = ref(null)
const seleniumAction = ref(false)
let seleniumTimer = null
const DAYS = [['mon', '월'], ['tue', '화'], ['wed', '수'], ['thu', '목'], ['fri', '금'], ['sat', '토'], ['sun', '일']]
const CATEGORIES = { 1: '영화/애니메이션', 2: '자동차', 10: '음악', 15: '반려동물/동물', 17: '스포츠', 19: '여행/이벤트',
  20: '게임', 22: '인물/블로그', 23: '코미디', 24: '엔터테인먼트', 25: '뉴스/정치', 26: '노하우/스타일', 27: '교육', 28: '과학기술' }

const pick = (c) => c && { upload: c.upload, schedule: c.schedule }
const { dirty } = useDirty('upload-config', () => cfg.value && !same(pick(cfg.value), pick(orig.value)))
useDirty('selectors', () => selectors.value !== selectorsOrig.value)

async function load() {
  const r = await api.get('/config')
  cfg.value = r.config
  normalizeAccounts(cfg.value)
  orig.value = clone(cfg.value)
  await loadSelenium()
  loadPreview()
  try { bridge.value = await api.get('/upload/status') } catch { /* 무시 */ }
  const s = await api.get('/upload/selectors')
  selectors.value = s.text
  selectorsOrig.value = s.text
}
async function loadPreview() {
  try { preview.value = (await api.get('/config/schedule/preview?days=7')).slots } catch (e) { toastError(e) }
}
async function save() {
  try {
    const r = await api.put('/config', { config: cfg.value })
    cfg.value = r.config
    normalizeAccounts(cfg.value)
    orig.value = clone(cfg.value)
    errors.value = {}
    toast('업로드 설정을 저장했습니다', 'success')
    loadPreview()
    loadCommon()
    await loadSelenium()
  } catch (e) { errors.value = e.fields || {}; toastError(e) }
}

function normalizeAccounts(config) {
  const browser = config.upload.selenium
  if (!browser.accounts?.length) {
    browser.accounts = [{ id: 'default', name: '기본 계정', profile_dir: browser.profile_dir }]
  }
  browser.selected_account ||= 'default'
}
async function loadSelenium(quiet = false) {
  try { seleniumState.value = await api.get('/upload/selenium/status') }
  catch (e) { if (!quiet && up.value?.mode === 'selenium') toastError(e) }
}
function addAccount() {
  const id = `account_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
  up.value.selenium.accounts.push({ id, name: `계정 ${up.value.selenium.accounts.length + 1}`, profile_dir: `.browser/${id}` })
  up.value.selenium.selected_account = id
}
function removeAccount() {
  const browser = up.value.selenium
  if (browser.accounts.length <= 1) return
  browser.accounts = browser.accounts.filter(a => a.id !== browser.selected_account)
  browser.selected_account = browser.accounts[0].id
}
async function seleniumLogin(action = 'open') {
  seleniumAction.value = true
  try {
    const path = action === 'open' ? '/upload/selenium/login' : `/upload/selenium/login/${action}`
    await api.post(path, action === 'open' ? { account_id: up.value.selenium.selected_account } : {})
    if (action === 'open') toast('서버 PC에서 열리는 Chrome에서 로그인하고 채널을 선택하세요.', 'info', 8000)
    await loadSelenium()
  } catch (e) { toastError(e) }
  finally { seleniumAction.value = false }
}
async function saveSelectors() {
  try {
    await api.put('/upload/selectors', { text: selectors.value })
    selectorsOrig.value = selectors.value
    toast('selectors.json 저장 (확장프로그램은 다음 업로드 때 새로 받아감)', 'success')
  } catch (e) { toastError(e) }
}

async function connect() {
  try {
    const r = await api.get('/auth/youtube/url')
    if (location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
      toast(`OAuth 콜백은 ${r.redirect_uri} 로 돌아옵니다. 서버 PC의 브라우저에서 연결하세요.`, 'info', 8000)
    }
    window.open(r.url, '_blank')
  } catch (e) { toastError(e) }
}
async function disconnect() {
  if (!confirm('YouTube 연결을 해제할까요? (토큰 파일 삭제)')) return
  try { await api.del('/auth/youtube'); await loadCommon(); toast('연결을 해제했습니다') } catch (e) { toastError(e) }
}

function addSlot(day) {
  const list = cfg.value.schedule.slots[day] || (cfg.value.schedule.slots[day] = [])
  list.push('12:00')
}
function copyToAll(day) {
  const src = cfg.value.schedule.slots[day] || []
  for (const [d] of DAYS) cfg.value.schedule.slots[d] = [...src]
}
function addPreset() {
  cfg.value.schedule.presets.push({ label: '2시간 후', type: 'offset', minutes: 120 })
}

// 확장프로그램에 "다음 항목 업로드" 요청 (확장프로그램이 이 페이지에 브리지 스크립트를 넣어 둔다)
function onMessage(ev) {
  if (ev.source !== window || ev.data?.source !== 'autoset-extension') return
  extReply.value = ev.data
  if (ev.data.type === 'upload-started') toast('확장프로그램이 업로드를 시작했습니다', 'success')
  if (ev.data.type === 'error') toast(ev.data.message, 'error', 8000)
}
function uploadNext() {
  extReply.value = null
  window.postMessage({ source: 'autoset-ui', type: 'upload-next' }, location.origin)
  setTimeout(() => { if (!extReply.value) toast('확장프로그램 응답이 없습니다. 확장프로그램 팝업에서 이 서버 주소를 저장했는지 확인하세요.', 'error', 8000) }, 2500)
}
onMounted(() => {
  load()
  seleniumTimer = setInterval(() => { if (up.value?.mode === 'selenium') loadSelenium(true) }, 2000)
  window.addEventListener('message', onMessage)
  if (route.query.connected !== undefined) toast(`YouTube 연결됨: ${route.query.connected}`, 'success')
})
onBeforeUnmount(() => {
  window.removeEventListener('message', onMessage)
  clearInterval(seleniumTimer)
})

const yt = computed(() => store.status?.youtube)
const up = computed(() => cfg.value?.upload)
const selectedAccount = computed(() => up.value?.selenium.accounts.find(a => a.id === up.value.selenium.selected_account))
const selectedLogin = computed(() => seleniumState.value?.accounts.find(a => a.id === selectedAccount.value?.id)?.login)
const grouped = computed(() => {
  const out = {}
  for (const s of preview.value) {
    const day = fmt(s.at).split(' ').slice(0, 2).join(' ')
    ;(out[day] ||= []).push(s)
  }
  return out
})
</script>

<template>
  <div v-if="cfg" class="space-y-4">
    <div class="flex items-center gap-2">
      <h2 class="font-bold flex-1">업로드 설정</h2>
      <span v-if="dirty" class="badge bg-amber-400 text-amber-950">저장 안 됨</span>
      <button class="btn btn-sm" :disabled="!dirty" @click="cfg = clone(orig)">되돌리기</button>
      <button class="btn btn-primary" :disabled="!dirty" @click="save">저장</button>
    </div>

    <section class="card p-4 space-y-3">
      <h3 class="font-semibold text-sm">업로드 모드</h3>
      <div class="flex flex-wrap gap-2">
        <label :class="['btn', up.mode === 'api' && 'btn-primary']"><input v-model="up.mode" type="radio" value="api" class="hidden" />A안 · YouTube Data API (권장)</label>
        <label :class="['btn', up.mode === 'extension' && 'btn-primary']"><input v-model="up.mode" type="radio" value="extension" class="hidden" />C안 · 크롬 확장프로그램</label>
        <label :class="['btn', up.mode === 'selenium' && 'btn-primary']"><input v-model="up.mode" type="radio" value="selenium" class="hidden" />D안 · Selenium + undetected-chromedriver</label>
      </div>
      <p v-if="up.mode === 'extension'" class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
        C안은 크롬에 로그인한 상태에서 YouTube 스튜디오 탭을 열어 둔 채로만 동작합니다. YouTube 약관상 자동화 도구 사용은 제재 사유가 될 수 있으니 A안을 기본으로 쓰세요.</p>
      <div class="grid sm:grid-cols-4 gap-3">
        <label><span class="label">기본 공개 범위 (즉시 게시)</span>
          <select v-model="up.privacy" class="input"><option value="public">공개</option><option value="unlisted">일부 공개</option><option value="private">비공개</option></select></label>
        <label><span class="label">카테고리 (API)</span>
          <select v-model="up.category_id" class="input"><option v-for="(n, id) in CATEGORIES" :key="id" :value="String(id)">{{ n }}</option></select></label>
        <label><span class="label">언어 (API)</span><input v-model="up.language" class="input" /></label>
        <label><span class="label">일일 업로드 상한 (API)</span><input v-model.number="up.daily_limit" type="number" min="0" class="input" /></label>
      </div>
      <div class="flex flex-wrap gap-4 text-sm">
        <label class="flex items-center gap-2"><input v-model="up.credit_source" type="checkbox" /> 설명 끝에 "원본: @작성자 / URL" 추가 (credit_source)</label>
        <label class="flex items-center gap-2"><input v-model="up.made_for_kids" type="checkbox" /> 아동용 콘텐츠</label>
      </div>
    </section>

    <div class="grid lg:grid-cols-2 gap-4">
      <section v-if="up.mode === 'api'" class="card p-4 space-y-2">
        <h3 class="font-semibold text-sm">YouTube API 연결 (OAuth)</h3>
        <YoutubeClientBox />
        <hr />
        <p class="text-sm">상태: <b :class="yt?.connected ? 'text-emerald-600' : 'text-slate-500'">{{ yt?.connected ? '연결됨' : '연결 안 됨' }}</b>
          <span v-if="yt?.channel?.title"> · 채널 <b>{{ yt.channel.title }}</b></span></p>
        <p v-if="yt?.error" class="text-xs text-red-600">{{ yt.error }}</p>
        <p class="text-xs">오늘(태평양 시간 기준) API 업로드 {{ store.status?.api_uploads_quota_day }} / {{ up.daily_limit }}</p>
        <div class="flex gap-2">
          <button class="btn btn-primary" :disabled="!yt?.client_secret" @click="connect">연결하기</button>
          <button class="btn" :disabled="!yt?.connected" @click="disconnect">해제</button>
        </div>
      </section>
      <section v-else-if="up.mode === 'extension'" class="card p-4 space-y-2">
        <h3 class="font-semibold text-sm">확장프로그램 업로드</h3>
        <p class="text-sm">대기 중 <b>{{ bridge?.pending ?? '-' }}</b>건</p>
        <p v-if="bridge?.last_result" class="text-xs">마지막 처리: #{{ bridge.last_result.item_id }}
          <b :class="bridge.last_result.ok ? 'text-emerald-600' : 'text-red-600'">{{ bridge.last_result.ok ? '성공' : '실패' }}</b>
          {{ bridge.last_result.error || bridge.last_result.video_id }} · {{ fmt(bridge.last_result.at) }}</p>
        <button class="btn btn-primary" :disabled="dirty" @click="uploadNext">다음 항목 업로드 (한 건)</button>
        <p class="text-[11px] text-slate-500">한 건만 처리하고 멈춥니다. 모드 변경은 먼저 저장하세요.</p>
        <div class="flex items-center pt-2">
          <h4 class="text-sm font-medium flex-1">selectors.json</h4>
          <button class="btn btn-sm" :disabled="selectors === selectorsOrig" @click="saveSelectors">저장</button>
        </div>
        <div class="h-72"><CodeEditor v-model="selectors" lang="json" class="h-72" /></div>
      </section>

      <section v-else-if="up.mode === 'selenium'" class="card p-4 space-y-3">
        <h3 class="font-semibold text-sm">D안 · Chrome 자동 업로드</h3>
        <p class="text-sm">계정을 선택해 저장하고 Chrome에서 로그인하세요. 업로드 워커는 선택한 계정으로 다음 대기 항목부터 처리합니다.</p>
        <div class="border rounded p-3 space-y-3">
          <label><span class="label">업로드할 계정</span>
            <select v-model="up.selenium.selected_account" class="input">
              <option v-for="account in up.selenium.accounts" :key="account.id" :value="account.id">{{ account.name }}</option>
            </select></label>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-sm" @click="addAccount">+ 계정 추가</button>
            <button class="btn btn-sm" :disabled="up.selenium.accounts.length <= 1 || seleniumState?.session.active" @click="removeAccount">목록에서 제거</button>
          </div>
          <template v-if="selectedAccount">
            <label><span class="label">계정 이름</span><input v-model="selectedAccount.name" class="input" /></label>
            <label><span class="label">이 계정의 Chrome 프로필 폴더</span><input v-model="selectedAccount.profile_dir" class="input" /></label>
            <p v-for="(message, field) in errors" v-show="field.startsWith('upload.selenium.accounts') || field === 'upload.selenium.selected_account'" :key="field" class="field-error">{{ message }}</p>
          </template>
          <p v-if="selectedLogin?.verified" class="text-xs text-emerald-700">
            로그인 확인됨 · {{ selectedLogin.channel_name || selectedLogin.channel_id }} · {{ fmt(selectedLogin.checked_at) }}</p>
          <p v-else class="text-xs text-amber-700">로그인 연결이 필요합니다. 설정을 저장한 뒤 Chrome 로그인 버튼을 누르세요.</p>
          <p v-if="selectedLogin?.error" class="field-error">{{ selectedLogin.error }}</p>
          <div class="flex flex-wrap gap-2">
            <button class="btn btn-primary" :disabled="dirty || seleniumAction || seleniumState?.browser_busy" @click="seleniumLogin()">Chrome 로그인 하기</button>
            <button v-if="seleniumState?.session.active" class="btn btn-primary" :disabled="seleniumAction || seleniumState.session.state !== 'ready'" @click="seleniumLogin('finish')">로그인 완료 · 창 닫기</button>
            <button v-if="seleniumState?.session.active" class="btn" :disabled="seleniumAction" @click="seleniumLogin('cancel')">로그인 취소 · 창 닫기</button>
          </div>
          <p v-if="dirty" class="text-xs text-amber-700">계정 추가·변경 후에는 화면 위의 저장 버튼을 먼저 누르세요.</p>
          <p v-if="seleniumState?.session.message" class="text-xs" role="status">{{ seleniumState.session.account_name }} · {{ seleniumState.session.message }}
            <span v-if="seleniumState.session.active && seleniumState.session.channel_id"> · {{ seleniumState.session.channel_name || seleniumState.session.channel_id }}</span></p>
          <p v-if="seleniumState?.browser_busy && !seleniumState?.session.active" class="text-xs text-slate-500">D안 업로드가 진행 중입니다. 완료 후 로그인 창을 열 수 있습니다.</p>
        </div>
        <p class="text-xs text-slate-600">계정마다 다른 프로필에 로그인 상태가 저장됩니다. 열리는 서버 PC의 Chrome에서 로그인하고 사용할 YouTube 채널을 선택한 뒤, 여기서 로그인 완료를 누르세요. 창을 닫았어도 버튼으로 다시 열 수 있습니다.</p>
        <p class="text-xs text-slate-500">목록에서 제거해도 프로필 폴더와 로그인 정보는 남습니다. 로그인 단계에서 실패한 영상은 로그인 완료 후 다시 대기합니다.</p>
        <p class="text-xs text-indigo-700">제목·태그: 글자마다 0.05~0.1초 랜덤 타이핑 · 설명: 한 번에 붙여넣기 · 클릭 전: 1.0~2.5초 랜덤 대기</p>
        <div class="grid sm:grid-cols-2 gap-3">
          <label class="sm:col-span-2"><span class="label">Chrome 실행 파일 (비우면 자동 탐색)</span>
            <input v-model="up.selenium.chrome_binary" class="input" placeholder="C:/Program Files/Google/Chrome/Application/chrome.exe" /></label>
          <label><span class="label">Chrome 주 버전 (0 = 자동)</span>
            <input v-model.number="up.selenium.version_main" type="number" min="0" class="input" />
            <p v-if="errors['upload.selenium.version_main']" class="field-error">{{ errors['upload.selenium.version_main'] }}</p></label>
          <label><span class="label">로그인 대기 (초)</span>
            <input v-model.number="up.selenium.login_timeout_sec" type="number" min="30" max="3600" class="input" />
            <p v-if="errors['upload.selenium.login_timeout_sec']" class="field-error">{{ errors['upload.selenium.login_timeout_sec'] }}</p></label>
          <label><span class="label">단계별 대기 제한 (초)</span>
            <input v-model.number="up.selenium.step_timeout_sec" type="number" min="10" max="600" class="input" />
            <p v-if="errors['upload.selenium.step_timeout_sec']" class="field-error">{{ errors['upload.selenium.step_timeout_sec'] }}</p></label>
          <label><span class="label">업로드 대기 제한 (초)</span>
            <input v-model.number="up.selenium.upload_timeout_sec" type="number" min="60" max="14400" class="input" />
            <p v-if="errors['upload.selenium.upload_timeout_sec']" class="field-error">{{ errors['upload.selenium.upload_timeout_sec'] }}</p></label>
          <label><span class="label">예약 날짜 표시 형식</span><input v-model="up.selenium.date_format" class="input" /></label>
          <label><span class="label">예약 시각 표시 형식</span><input v-model="up.selenium.time_format" class="input" /></label>
        </div>
        <p class="text-xs text-slate-500">아동용·공개 범위·예약 게시를 적용합니다. 게시 완료 확인이 실패하면 스튜디오에서 결과를 확인한 뒤 재시도하세요.</p>
        <div class="flex items-center pt-2">
          <h4 class="text-sm font-medium flex-1">selectors.json (C안·D안 공통)</h4>
          <button class="btn btn-sm" :disabled="selectors === selectorsOrig" @click="saveSelectors">저장</button>
        </div>
        <div class="h-72"><CodeEditor v-model="selectors" lang="json" class="h-72" /></div>
      </section>

      <section class="card p-4 space-y-2">
        <h3 class="font-semibold text-sm">향후 7일 배정 ({{ store.status?.server?.timezone }})</h3>
        <p class="text-[11px] text-slate-500">저장된 슬롯 기준입니다. 슬롯을 바꾼 뒤 저장하면 갱신됩니다.</p>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-96 overflow-auto">
          <div v-for="(slots, day) in grouped" :key="day" class="border rounded p-2">
            <p class="text-xs font-semibold mb-1">{{ day }}</p>
            <p v-for="s in slots" :key="s.at + s.kind" class="text-xs flex gap-1">
              <span class="font-mono">{{ fmt(s.at).split(' ').pop() }}</span>
              <span v-if="s.item_id" :class="s.kind === 'manual' ? 'text-slate-500' : 'text-indigo-700'">
                {{ s.kind === 'manual' ? '(직접)' : '' }} #{{ s.item_id }} {{ s.title || '' }}</span>
              <span v-else class="text-emerald-600">빈 슬롯</span>
            </p>
          </div>
        </div>
      </section>
    </div>

    <section class="card p-4 space-y-3">
      <h3 class="font-semibold text-sm">예약 슬롯 (요일별 게시 시각)</h3>
      <div class="grid sm:grid-cols-2 lg:grid-cols-7 gap-2">
        <div v-for="[d, label] in DAYS" :key="d" class="border rounded p-2 space-y-1">
          <div class="flex items-center"><b class="text-sm flex-1">{{ label }}</b>
            <button class="text-[10px] text-slate-400 hover:text-indigo-600" title="모든 요일에 복사" @click="copyToAll(d)">전체 복사</button></div>
          <div v-for="(t, i) in cfg.schedule.slots[d] || []" :key="i" class="flex gap-1">
            <input v-model="cfg.schedule.slots[d][i]" type="time" class="input py-0.5 text-xs" />
            <button class="text-red-500 text-xs" @click="cfg.schedule.slots[d].splice(i, 1)">×</button>
          </div>
          <button class="btn btn-sm w-full" @click="addSlot(d)">+ 시각</button>
          <p v-if="errors[`schedule.slots.${d}`]" class="field-error">{{ errors[`schedule.slots.${d}`] }}</p>
        </div>
      </div>
      <label class="block w-64"><span class="label">지금부터 이 시간(분) 이내 슬롯은 배정 안 함</span>
        <input v-model.number="cfg.schedule.min_lead_minutes" type="number" min="0" class="input" /></label>
    </section>

    <section class="card p-4 space-y-2">
      <h3 class="font-semibold text-sm">피드 카드 예약 프리셋</h3>
      <div v-for="(p, i) in cfg.schedule.presets" :key="i" class="flex flex-wrap gap-2 items-center">
        <input v-model="p.label" class="input w-36" placeholder="이름" />
        <select v-model="p.type" class="input w-36">
          <option value="immediate">즉시</option><option value="next_slot">다음 예약 슬롯</option>
          <option value="offset">N분 후</option><option value="at">특정 날의 시각</option>
        </select>
        <template v-if="p.type === 'offset'"><input v-model.number="p.minutes" type="number" class="input w-24" /><span class="text-xs">분 후</span></template>
        <template v-if="p.type === 'at'">
          <select v-model.number="p.day_offset" class="input w-24"><option :value="0">오늘</option><option :value="1">내일</option><option :value="2">모레</option></select>
          <input v-model="p.time" type="time" class="input w-28" />
        </template>
        <button class="btn btn-sm" :disabled="i === 0" @click="cfg.schedule.presets.splice(i - 1, 0, cfg.schedule.presets.splice(i, 1)[0])">↑</button>
        <button class="btn btn-sm btn-danger" @click="cfg.schedule.presets.splice(i, 1)">삭제</button>
        <p v-if="errors[`schedule.presets.${i}`]" class="field-error w-full">{{ errors[`schedule.presets.${i}`] }}</p>
      </div>
      <button class="btn btn-sm" @click="addPreset">+ 프리셋 추가</button>
    </section>
  </div>
  <p v-else class="text-slate-400">불러오는 중…</p>
</template>
