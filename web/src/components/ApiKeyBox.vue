<script setup>
// API 키 등록·변경·삭제·연결 테스트 (키는 서버 .env 에 저장, 화면에는 가려진 값만 표시)
import { onMounted, ref } from 'vue'
import { api } from '../lib/api'
import { loadStatus, toast, toastError } from '../lib/store'

const props = defineProps({ provider: { type: String, required: true }, link: String })
const info = ref(null)
const value = ref('')
const editing = ref(false)
const busy = ref(false)
const test = ref(null)

async function load() {
  try { info.value = (await api.get('/secrets')).keys[props.provider] } catch (e) { toastError(e) }
}
onMounted(load)

async function save() {
  busy.value = true
  try {
    info.value = (await api.put(`/secrets/keys/${props.provider}`, { value: value.value })).keys[props.provider]
    value.value = ''
    editing.value = false
    test.value = null
    toast('키를 저장했습니다 (재시작 없이 바로 적용)', 'success')
    loadStatus()
  } catch (e) { toastError(e) } finally { busy.value = false }
}
async function remove() {
  if (!confirm(`${info.value.env} 를 삭제할까요?`)) return
  try {
    info.value = (await api.del(`/secrets/keys/${props.provider}`)).keys[props.provider]
    test.value = null
    loadStatus()
  } catch (e) { toastError(e) }
}
async function runTest() {
  busy.value = true
  test.value = null
  try { test.value = await api.post(`/secrets/keys/${props.provider}/test`) } catch (e) { toastError(e) } finally { busy.value = false }
}
</script>

<template>
  <div v-if="info" class="space-y-1.5">
    <div class="flex items-center gap-2 text-xs">
      <span class="text-slate-500">API 키</span>
      <b v-if="info.set" class="text-emerald-600 font-mono">등록됨 {{ info.masked }}</b>
      <b v-else class="text-red-600">없음</b>
      <span class="flex-1" />
      <button v-if="info.set && !editing" class="btn btn-sm" :disabled="busy" @click="runTest">연결 테스트</button>
      <button v-if="!editing" class="btn btn-sm" @click="editing = true">{{ info.set ? '변경' : '등록' }}</button>
      <button v-if="info.set && !editing" class="btn btn-sm btn-danger" @click="remove">삭제</button>
    </div>
    <div v-if="editing" class="flex gap-1">
      <input v-model="value" type="password" autocomplete="off" class="input font-mono" :placeholder="`${info.env} 붙여넣기`"
        @keyup.enter="value && save()" />
      <button class="btn btn-sm btn-primary" :disabled="busy || !value" @click="save">저장</button>
      <button class="btn btn-sm" @click="editing = false; value = ''">취소</button>
    </div>
    <p v-if="editing && link" class="text-[11px] text-slate-500">발급: <a :href="link" target="_blank" class="text-indigo-600 underline">{{ link }}</a></p>
    <p v-if="test" :class="['text-[11px] break-all', test.ok ? 'text-emerald-600' : 'text-red-600']">
      {{ test.ok ? `✓ 동작함 (${test.model})` : `✕ ${test.error}` }}</p>
  </div>
</template>
