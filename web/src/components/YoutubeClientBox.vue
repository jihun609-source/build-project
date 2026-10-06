<script setup>
// YouTube OAuth 클라이언트(client_secret.json) 등록: 파일 업로드 또는 JSON 붙여넣기
import { onMounted, ref } from 'vue'
import { api } from '../lib/api'
import { loadStatus, toast, toastError } from '../lib/store'

const emit = defineEmits(['changed'])
const info = ref(null)
const paste = ref('')
const showPaste = ref(false)

async function load() {
  try { info.value = (await api.get('/secrets')).youtube_client } catch (e) { toastError(e) }
}
onMounted(load)

async function done(r, msg) {
  info.value = r.youtube_client
  paste.value = ''
  showPaste.value = false
  toast(msg, 'success')
  await loadStatus()
  emit('changed')
}
async function upload(e) {
  const f = e.target.files[0]
  e.target.value = ''
  if (!f) return
  try { await done(await api.upload('/secrets/youtube-client', f), 'OAuth 클라이언트 파일을 등록했습니다') } catch (err) { toastError(err) }
}
async function savePaste() {
  try { await done(await api.put('/secrets/youtube-client', { text: paste.value }), 'OAuth 클라이언트를 등록했습니다') } catch (e) { toastError(e) }
}
async function remove() {
  if (!confirm('OAuth 클라이언트 파일과 연결 토큰을 삭제할까요?')) return
  try { await done(await api.del('/secrets/youtube-client'), '삭제했습니다') } catch (e) { toastError(e) }
}
function copy(text) {
  navigator.clipboard?.writeText(text).then(() => toast('복사했습니다'), () => {})
}
</script>

<template>
  <div v-if="info" class="space-y-2 text-sm">
    <p class="text-xs">
      OAuth 클라이언트:
      <b v-if="info.present" class="text-emerald-600">등록됨</b><b v-else class="text-red-600">없음</b>
      <span v-if="info.client_id" class="font-mono text-slate-500"> · {{ info.type }} · {{ info.client_id }}</span>
    </p>
    <p v-if="info.error" class="field-error">{{ info.error }}</p>
    <p v-if="info.redirect_registered === false" class="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
      이 클라이언트에 아래 리디렉션 URI가 등록돼 있지 않습니다. Google Cloud Console → 사용자 인증 정보 → 이 클라이언트 →
      "승인된 리디렉션 URI"에 추가한 뒤 JSON을 다시 받아 등록하세요.</p>
    <div class="flex items-center gap-1 text-[11px]">
      <span class="text-slate-500">리디렉션 URI</span>
      <code class="bg-slate-100 px-1 rounded">{{ info.redirect_uri }}</code>
      <button class="btn btn-sm" @click="copy(info.redirect_uri)">복사</button>
    </div>
    <div class="flex flex-wrap gap-2">
      <label class="btn btn-sm cursor-pointer">client_secret.json 업로드<input type="file" accept=".json,application/json" class="hidden" @change="upload" /></label>
      <button class="btn btn-sm" @click="showPaste = !showPaste">JSON 붙여넣기</button>
      <button v-if="info.present" class="btn btn-sm btn-danger" @click="remove">삭제</button>
    </div>
    <div v-if="showPaste" class="space-y-1">
      <textarea v-model="paste" rows="4" class="input font-mono text-xs" placeholder='{"web": {"client_id": "...", "client_secret": "...", ...}}' />
      <button class="btn btn-sm btn-primary" :disabled="!paste.trim()" @click="savePaste">등록</button>
    </div>
    <details class="text-[11px] text-slate-500">
      <summary class="cursor-pointer">OAuth 클라이언트 만드는 방법</summary>
      <ol class="list-decimal ml-4 mt-1 space-y-0.5">
        <li><a href="https://console.cloud.google.com/" target="_blank" class="text-indigo-600 underline">Google Cloud Console</a>에서 프로젝트 만들기</li>
        <li>API 및 서비스 → 라이브러리 → <b>YouTube Data API v3</b> 사용 설정</li>
        <li>OAuth 동의 화면 설정 (테스트 사용자에 업로드할 채널의 구글 계정 추가)</li>
        <li>사용자 인증 정보 → 만들기 → OAuth 클라이언트 ID → 유형 <b>웹 애플리케이션</b></li>
        <li>승인된 리디렉션 URI에 위 주소를 추가하고 만들기 → JSON 다운로드 → 여기에 업로드</li>
      </ol>
    </details>
  </div>
</template>
