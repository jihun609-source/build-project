<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, qs } from '../lib/api'
import { store, toast, toastError } from '../lib/store'
import { clone, same, useDirty } from '../lib/dirty'
import CodeEditor from '../components/CodeEditor.vue'
import HistoryPanel from '../components/HistoryPanel.vue'
import Modal from '../components/Modal.vue'
import ApiKeyBox from '../components/ApiKeyBox.vue'

// ---------------------------------------------------------------- 설정 (config.ai)
const cfg = ref(null)
const cfgOrig = ref(null)
const cfgErrors = ref({})
const models = ref([])
const pullName = ref('gemma4:12b')
const { dirty: cfgDirty } = useDirty('ai-config', () => cfg.value && !same(cfg.value.ai, cfgOrig.value?.ai))

async function loadConfig() {
  const r = await api.get('/config')
  cfg.value = r.config
  cfgOrig.value = clone(r.config)
}
async function loadModels() {
  try { models.value = (await api.get('/system/ollama/models')).models } catch (e) { models.value = [] }
}
async function saveConfig() {
  try {
    const r = await api.put('/config', { config: cfg.value })
    cfg.value = r.config
    cfgOrig.value = clone(r.config)
    cfgErrors.value = {}
    toast('AI 설정을 저장했습니다', 'success')
  } catch (e) { cfgErrors.value = e.fields || {}; toastError(e) }
}
const ai = computed(() => cfg.value?.ai)
const ALL = ['ollama', 'gemini', 'anthropic']
const unused = computed(() => ALL.filter(p => !ai.value.providers.includes(p)))
function move(i, d) {
  const a = ai.value.providers
  const j = i + d
  if (j < 0 || j >= a.length) return
  ;[a[i], a[j]] = [a[j], a[i]]
}
let dragIdx = null
function onDrop(i) {
  if (dragIdx === null || dragIdx === i) return
  const a = ai.value.providers
  const [x] = a.splice(dragIdx, 1)
  a.splice(i, 0, x)
  dragIdx = null
}
async function pull() {
  try {
    await api.post('/system/ollama/pull', { model: pullName.value })
    toast(`${pullName.value} 받기 시작`, 'success')
  } catch (e) { toastError(e) }
}
const pullState = computed(() => store.status?.ollama_pull)
const listText = (arr) => (arr || []).join(', ')
const setList = (obj, key, text) => { obj[key] = text.split(',').map(s => s.trim()).filter(Boolean) }

// ---------------------------------------------------------------- 프롬프트
const profiles = ref([])
const variables = ref({})
const defaultProfile = ref('')
const profile = ref('')
const files = ref({})
const filesOrig = ref({})
const version = ref('')
const fileTab = ref('system.md')
const editor = ref(null)
const promptErrors = ref({})
const historyOpen = ref(false)
const cloneModal = reactive({ open: false, name: '' })
const FILES = ['system.md', 'user.md', 'schema.json', 'examples.md']
const changedFiles = computed(() => FILES.filter(f => files.value[f] !== filesOrig.value[f]))
useDirty('prompts', () => changedFiles.value.length > 0)

async function loadProfiles() {
  const r = await api.get('/prompts')
  profiles.value = r.profiles
  variables.value = r.variables
  defaultProfile.value = r.default
  if (!profile.value) profile.value = r.default
}
async function loadProfile(p) {
  if (changedFiles.value.length && !confirm('저장하지 않은 프롬프트 변경이 있습니다. 버릴까요?')) return
  const r = await api.get(`/prompts/${encodeURIComponent(p)}`)
  profile.value = p
  files.value = r.files
  filesOrig.value = clone(r.files)
  version.value = r.version
  promptErrors.value = {}
}
async function savePrompts() {
  try {
    for (const f of changedFiles.value) {
      const r = await api.put(`/prompts/${encodeURIComponent(profile.value)}/${f}`, { content: files.value[f] })
      filesOrig.value[f] = files.value[f]
      version.value = r.version
    }
    promptErrors.value = {}
    toast('프롬프트를 저장했습니다 (다음 AI 호출부터 적용)', 'success')
  } catch (e) { promptErrors.value = e.fields || {}; toastError(e) }
}
async function cloneProfile() {
  try {
    await api.post(`/prompts/${encodeURIComponent(profile.value)}/clone`, { name: cloneModal.name })
    cloneModal.open = false
    await loadProfiles()
    filesOrig.value = clone(files.value)
    await loadProfile(cloneModal.name)
  } catch (e) { toastError(e) }
}
async function makeDefault() {
  try {
    await api.post(`/prompts/${encodeURIComponent(profile.value)}/default`)
    defaultProfile.value = profile.value
    await loadConfig()
    toast('기본 프로필로 지정했습니다', 'success')
  } catch (e) { toastError(e) }
}
function insertVar(name) { editor.value?.insert(`{{${name}}}`) }

// 테스트 실행
const items = ref([])
const test = reactive({ itemId: null, instruction: '', busy: false, result: null })
async function runTest() {
  test.busy = true
  test.result = null
  try {
    const overrides = Object.fromEntries(changedFiles.value.map(f => [f, files.value[f]]))
    test.result = await api.post('/prompts/test', { item_id: test.itemId, profile: profile.value, overrides,
      instruction: test.instruction || null })
  } catch (e) { toastError(e) } finally { test.busy = false }
}
async function applyTest() {
  if (!confirm(`#${test.itemId} 항목에 이 결과를 적용하고 자막 합성을 다시 실행할까요?`)) return
  try {
    await api.post('/prompts/test/apply', { item_id: test.itemId, result: test.result.result, provider: test.result.provider,
      profile: test.result.prompt?.profile, version: test.result.prompt?.version })
    toast('적용했습니다', 'success')
  } catch (e) { toastError(e) }
}

onMounted(async () => {
  try {
    await Promise.all([loadConfig(), loadProfiles()])
    await loadProfile(profile.value)
    loadModels()
    const r = await api.get('/items' + qs({ status: 'edited,captioned,captioned_review,uploaded,failed_caption,failed_upload', limit: 50 }))
    items.value = r.items
    test.itemId = items.value[0]?.id ?? null
  } catch (e) { toastError(e) }
})
const st = computed(() => store.status)
</script>

<template>
  <div v-if="cfg" class="space-y-5">
    <!-- ======================= 프로바이더·옵션 ======================= -->
    <div class="flex items-center gap-2">
      <h2 class="font-bold flex-1">AI 설정</h2>
      <span v-if="cfgDirty" class="badge bg-amber-400 text-amber-950">저장 안 됨</span>
      <button class="btn btn-sm" :disabled="!cfgDirty" @click="cfg = clone(cfgOrig)">되돌리기</button>
      <button class="btn btn-primary" :disabled="!cfgDirty" @click="saveConfig">설정 저장</button>
    </div>
    <div class="grid lg:grid-cols-3 gap-4">
      <section class="card p-4 space-y-3">
        <h3 class="font-semibold text-sm">프로바이더 순서 (위에서부터 시도, 드래그로 변경)</h3>
        <ul class="space-y-1">
          <li v-for="(p, i) in ai.providers" :key="p" draggable="true" @dragstart="dragIdx = i" @dragover.prevent @drop="onDrop(i)"
            class="flex items-center gap-2 border rounded px-2 py-1.5 bg-white cursor-move">
            <span class="text-slate-400">⠿</span><span class="font-medium flex-1">{{ i + 1 }}. {{ p }}</span>
            <button class="btn btn-sm" @click="move(i, -1)">↑</button>
            <button class="btn btn-sm" @click="move(i, 1)">↓</button>
            <button class="btn btn-sm btn-danger" :disabled="ai.providers.length === 1" @click="ai.providers.splice(i, 1)">×</button>
          </li>
        </ul>
        <div v-if="unused.length" class="flex gap-1">
          <button v-for="p in unused" :key="p" class="btn btn-sm" @click="ai.providers.push(p)">+ {{ p }}</button>
        </div>
        <p v-if="cfgErrors['ai.providers']" class="field-error">{{ cfgErrors['ai.providers'] }}</p>

        <h3 class="font-semibold text-sm pt-2">Gemini</h3>
        <ApiKeyBox provider="gemini" link="https://aistudio.google.com/apikey" />
        <p class="text-xs">오늘 {{ st?.gemini?.used }}/{{ ai.gemini.daily_limit }}회
          <span v-if="st?.gemini?.exhausted_until" class="text-amber-600"> · 한도 소진 ({{ st.gemini.exhausted_until }}까지)</span></p>
        <div class="grid grid-cols-2 gap-2">
          <label><span class="label">모델</span><input v-model="ai.gemini.model" class="input" /></label>
          <label><span class="label">일일 호출 상한</span><input v-model.number="ai.gemini.daily_limit" type="number" class="input" /></label>
        </div>
        <h3 class="font-semibold text-sm pt-2">Anthropic (선택)</h3>
        <ApiKeyBox provider="anthropic" link="https://console.anthropic.com/settings/keys" />
        <p class="text-xs">오늘 {{ st?.anthropic?.used }}회</p>
        <div class="grid grid-cols-2 gap-2">
          <label><span class="label">모델</span><input v-model="ai.anthropic.model" class="input" /></label>
          <label><span class="label">일일 호출 상한</span><input v-model.number="ai.anthropic.daily_limit" type="number" class="input" /></label>
        </div>
      </section>

      <section class="card p-4 space-y-3">
        <h3 class="font-semibold text-sm">Ollama</h3>
        <p class="text-xs">
          <span :class="st?.ollama?.connected ? 'text-emerald-600' : 'text-red-600'">● {{ st?.ollama?.connected ? '연결됨' : '연결 안 됨' }}</span>
          · {{ ai.ollama.host }}
          <span v-if="st?.ollama?.connected && !st?.ollama?.installed" class="text-amber-600"> · {{ ai.ollama.model }} 미설치 → 아래에서 모델 받기</span>
        </p>
        <label><span class="label">주소</span><input v-model="ai.ollama.host" class="input" /></label>
        <label><span class="label">사용 모델</span>
          <select v-model="ai.ollama.model" class="input">
            <option v-if="!models.some(m => m.name === ai.ollama.model)" :value="ai.ollama.model">{{ ai.ollama.model }} (미설치)</option>
            <option v-for="m in models" :key="m.name" :value="m.name">{{ m.name }} {{ m.parameter_size ? `· ${m.parameter_size}` : '' }}</option>
          </select></label>
        <div class="flex gap-2">
          <input v-model="pullName" class="input" placeholder="gemma4:12b" />
          <button class="btn" :disabled="pullState?.running" @click="pull">모델 받기</button>
          <button class="btn btn-sm" @click="loadModels">↻</button>
        </div>
        <div v-if="pullState" class="text-xs">
          <p>{{ pullState.model }}: {{ pullState.status }} {{ pullState.pct != null ? pullState.pct + '%' : '' }}</p>
          <div v-if="pullState.running" class="h-1.5 bg-slate-200 rounded overflow-hidden">
            <div class="h-full bg-indigo-500 transition-all" :style="{ width: (pullState.pct || 0) + '%' }" /></div>
        </div>
        <div class="grid grid-cols-3 gap-2">
          <label><span class="label">temperature</span><input v-model.number="ai.ollama.temperature" type="number" step="0.05" class="input" /></label>
          <label><span class="label">컨텍스트</span><input v-model.number="ai.ollama.num_ctx" type="number" step="1024" class="input" /></label>
          <label><span class="label">타임아웃(초)</span><input v-model.number="ai.ollama.timeout_sec" type="number" class="input" /></label>
        </div>

        <h3 class="font-semibold text-sm pt-2">음성 인식 (faster-whisper)</h3>
        <div class="grid grid-cols-3 gap-2">
          <label><span class="label">모델 크기</span>
            <select v-model="ai.stt.model_size" class="input">
              <option v-for="m in ['tiny', 'base', 'small', 'medium', 'large-v3', 'turbo']" :key="m">{{ m }}</option></select></label>
          <label><span class="label">언어 고정</span>
            <select :value="ai.stt.language || ''" class="input" @change="ai.stt.language = $event.target.value || null">
              <option value="">자동 감지</option><option value="ko">ko</option><option value="en">en</option><option value="ja">ja</option>
            </select></label>
          <label><span class="label">무음 기준 dB</span><input v-model.number="ai.silence_db" type="number" class="input" /></label>
        </div>
        <p class="text-[11px] text-slate-400">STT 장치: {{ st?.stt_device || '아직 로드 안 됨' }}</p>
      </section>

      <section class="card p-4 space-y-3">
        <h3 class="font-semibold text-sm">생성 옵션</h3>
        <label><span class="label">프레임 수</span><input v-model.number="ai.frame_count" type="number" min="1" max="12" class="input w-24" /></label>
        <label class="flex items-center gap-2 text-sm"><input v-model="ai.hold_low_confidence" type="checkbox" />
          저신뢰(confidence=low) 결과는 검토 대기로 보류</label>
        <label><span class="label">고정 태그 (쉼표)</span>
          <input :value="listText(ai.fixed_tags)" class="input" @change="setList(ai, 'fixed_tags', $event.target.value)" /></label>
        <label><span class="label" v-text="'톤 ({{tone}})'" /><textarea v-model="ai.style.tone" rows="2" class="input" /></label>
        <label><span class="label" v-text="'금지어 (쉼표, {{banned_words}})'" />
          <input :value="listText(ai.style.banned_words)" class="input" @change="setList(ai.style, 'banned_words', $event.target.value)" /></label>
      </section>
    </div>

    <!-- ======================= 프롬프트 편집기 ======================= -->
    <div class="flex flex-wrap items-center gap-2 pt-2">
      <h2 class="font-bold">프롬프트</h2>
      <select :value="profile" class="input w-40" @change="loadProfile($event.target.value)">
        <option v-for="p in profiles" :key="p" :value="p">{{ p }}{{ p === defaultProfile ? ' (기본)' : '' }}</option>
      </select>
      <span class="text-xs text-slate-500 font-mono">버전 {{ version }}</span>
      <button class="btn btn-sm" @click="cloneModal.open = true; cloneModal.name = ''">새 프로필 복제</button>
      <button class="btn btn-sm" :disabled="profile === defaultProfile" @click="makeDefault">기본 프로필 지정</button>
      <button class="btn btn-sm" @click="historyOpen = true">버전 이력</button>
      <span class="flex-1" />
      <span v-if="changedFiles.length" class="badge bg-amber-400 text-amber-950">저장 안 됨: {{ changedFiles.join(', ') }}</span>
      <button class="btn btn-sm" :disabled="!changedFiles.length" @click="files = clone(filesOrig)">되돌리기</button>
      <button class="btn btn-primary" :disabled="!changedFiles.length" @click="savePrompts">프롬프트 저장</button>
    </div>
    <div class="grid lg:grid-cols-[1fr_260px] gap-4">
      <div class="card flex flex-col min-h-[520px]">
        <div class="flex border-b px-2 overflow-x-auto">
          <button v-for="f in FILES" :key="f" :class="['tab font-mono', fileTab === f && 'tab-active']" @click="fileTab = f">
            {{ f }}{{ files[f] !== filesOrig[f] ? ' ●' : '' }}</button>
        </div>
        <div class="p-2 flex-1 flex flex-col">
          <CodeEditor v-for="f in FILES" v-show="fileTab === f" :key="profile + f" :ref="el => { if (fileTab === f) editor = el }"
            v-model="files[f]" :lang="f.endsWith('.json') ? 'json' : 'markdown'" class="flex-1" />
          <p v-if="promptErrors[fileTab]" class="field-error">{{ promptErrors[fileTab] }}</p>
          <p v-if="fileTab === 'schema.json'" class="text-[11px] text-slate-500 mt-1">이 스키마가 Ollama format·Gemini response_schema로 전달되고 결과 검증에도 쓰입니다.</p>
          <p v-if="fileTab === 'examples.md'" class="text-[11px] text-slate-500 mt-1">내용이 있으면 system 뒤에 "## 예시" 제목과 함께 붙습니다 (HTML 주석은 무시).</p>
        </div>
      </div>
      <div class="card p-3 space-y-2">
        <h3 class="font-semibold text-sm">변수 (클릭하면 삽입)</h3>
        <button v-for="(desc, name) in variables" :key="name" class="block w-full text-left hover:bg-slate-50 rounded px-1 py-0.5"
          @click="insertVar(name)">
          <code class="text-xs text-indigo-700" v-text="'{' + '{' + name + '}' + '}'" />
          <span class="block text-[11px] text-slate-500">{{ desc }}</span>
        </button>
      </div>
    </div>

    <!-- 테스트 실행 -->
    <section class="card p-4 space-y-3">
      <h3 class="font-semibold text-sm">테스트 실행 <span class="font-normal text-xs text-slate-500">- 편집 중인 프롬프트로 AI 생성만 실행 (항목에는 저장 안 함)</span></h3>
      <div class="flex flex-wrap gap-2">
        <select v-model="test.itemId" class="input w-64">
          <option v-if="!items.length" :value="null">편집이 끝난 항목이 없습니다</option>
          <option v-for="it in items" :key="it.id" :value="it.id">#{{ it.id }} {{ it.title || it.feed_author }}</option>
        </select>
        <input v-model="test.instruction" class="input flex-1 min-w-[12rem]" placeholder="{{extra_instruction}} (선택)" />
        <button class="btn btn-primary" :disabled="test.busy || !test.itemId" @click="runTest">{{ test.busy ? '생성 중…' : '테스트 실행' }}</button>
      </div>
      <div v-if="test.result" class="grid md:grid-cols-2 gap-3">
        <div>
          <p class="text-sm mb-1">
            <b :class="test.result.ok ? 'text-emerald-600' : 'text-red-600'">{{ test.result.ok ? '성공' : '실패' }}</b>
            · {{ test.result.provider || '-' }} · {{ (test.result.elapsed_ms / 1000).toFixed(1) }}초
            · 프롬프트 {{ test.result.prompt?.version }} · 음성 {{ test.result.has_speech ? '있음' : '없음' }}</p>
          <pre class="text-xs bg-slate-50 border rounded p-2 whitespace-pre-wrap max-h-80 overflow-auto">{{ test.result.ok ? JSON.stringify(test.result.result, null, 2) : test.result.error }}</pre>
          <button v-if="test.result.ok" class="btn btn-sm mt-2" @click="applyTest">이 결과 적용</button>
        </div>
        <div class="space-y-2">
          <div class="flex gap-1 overflow-x-auto"><img v-for="u in test.result.frames" :key="u" :src="u" class="h-24 rounded border" /></div>
          <details v-for="(c, i) in test.result.calls" :key="i" class="text-xs border rounded p-2">
            <summary class="cursor-pointer">{{ c.provider }} {{ c.model }} {{ c.skipped ? '(건너뜀)' : '' }}
              <span v-if="c.error" class="text-red-600">{{ c.error }}</span></summary>
            <pre class="whitespace-pre-wrap mt-1">{{ c.skipped || c.raw }}</pre>
          </details>
          <details class="text-xs"><summary class="cursor-pointer text-slate-500">보낸 프롬프트</summary>
            <pre class="whitespace-pre-wrap bg-slate-50 p-2 max-h-60 overflow-auto">{{ test.result.prompt?.system }}

---- user ----
{{ test.result.prompt?.user }}</pre></details>
        </div>
      </div>
    </section>

    <HistoryPanel v-model="historyOpen" :title="`프롬프트 '${profile}' 버전 이력`"
      :list-url="`/prompts/${encodeURIComponent(profile)}/history`"
      :version-url="(ver) => `/prompts/${encodeURIComponent(profile)}/history/${encodeURIComponent(ver)}`"
      :restore-url="(ver) => `/prompts/${encodeURIComponent(profile)}/restore/${encodeURIComponent(ver)}`"
      @restored="filesOrig = clone(files); loadProfile(profile)" />
    <Modal v-model="cloneModal.open" :title="`'${profile}' 복제`">
      <input v-model="cloneModal.name" class="input" placeholder="새 프로필 이름" @keyup.enter="cloneProfile" />
      <template #footer><button class="btn btn-primary" :disabled="!cloneModal.name" @click="cloneProfile">복제</button></template>
    </Modal>
  </div>
  <p v-else class="text-slate-400">불러오는 중…</p>
</template>
