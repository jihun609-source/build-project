<script setup>
import { ref } from 'vue'
import { api, auth } from '../lib/api'

const token = ref(new URLSearchParams(location.search).get('token') || '')
const error = ref('')
const busy = ref(false)

async function login() {
  busy.value = true
  error.value = ''
  try {
    await api.post('/auth/login', { token: token.value.trim() })
    auth.ok = true
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="min-h-screen grid place-items-center bg-slate-900 p-4">
    <form class="card w-full max-w-sm p-6 space-y-4" @submit.prevent="login">
      <div>
        <h1 class="text-xl font-bold">AutoSet</h1>
        <p class="text-sm text-slate-500 mt-1">서버의 <code>config.yaml</code> → <code>ui.token</code> 값을 입력하세요.</p>
      </div>
      <input v-model="token" class="input" type="password" placeholder="접속 토큰" autofocus />
      <p v-if="error" class="field-error">{{ error }}</p>
      <button class="btn btn-primary w-full" :disabled="busy || !token">로그인</button>
    </form>
  </div>
</template>
