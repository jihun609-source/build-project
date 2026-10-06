<script setup>
// 버전 목록 → 비교(diff) → 되돌리기
import { ref, watch } from 'vue'
import { api } from '../lib/api'
import { toast, toastError } from '../lib/store'
import DiffView from './DiffView.vue'
import Modal from './Modal.vue'

const props = defineProps({
  listUrl: String,            // GET → { versions: [...] }
  versionUrl: Function,       // version → GET url → { text, current }
  restoreUrl: Function,       // version → POST url
  title: { type: String, default: '버전 이력' },
})
const emit = defineEmits(['restored'])
const open = defineModel({ type: Boolean, default: false })
const versions = ref([])
const selected = ref(null)
const diff = ref(null)

async function load() {
  try { versions.value = (await api.get(props.listUrl)).versions } catch (e) { toastError(e) }
}
watch(open, (v) => { if (v) { selected.value = null; diff.value = null; load() } })

async function pick(v) {
  selected.value = v
  try { diff.value = await api.get(props.versionUrl(v.version)) } catch (e) { toastError(e) }
}
async function restore() {
  if (!selected.value) return
  if (!confirm(`${selected.value.saved_at} 버전으로 되돌릴까요? (현재 값은 새 백업으로 남습니다)`)) return
  try {
    await api.post(props.restoreUrl(selected.value.version))
    toast('되돌렸습니다', 'success')
    emit('restored')
    open.value = false
  } catch (e) { toastError(e) }
}
</script>

<template>
  <Modal v-model="open" :title="title" wide>
    <div class="grid md:grid-cols-[220px_1fr] gap-3">
      <div class="border rounded max-h-[60vh] overflow-auto divide-y text-sm">
        <p v-if="!versions.length" class="p-3 text-slate-400">저장된 이전 버전이 없습니다</p>
        <button v-for="v in versions" :key="v.version" @click="pick(v)"
          :class="['w-full text-left px-3 py-2 hover:bg-slate-50', selected?.version === v.version && 'bg-indigo-50']">
          <div class="font-mono text-xs">{{ v.saved_at.replace('T', ' ') }}</div>
          <div class="text-[11px] text-slate-400">{{ v.key }} · {{ (v.size / 1024).toFixed(1) }}KB</div>
        </button>
      </div>
      <div>
        <DiffView v-if="diff" :old-text="diff.text" :new-text="diff.current" />
        <p v-else class="text-sm text-slate-400">왼쪽에서 버전을 고르면 현재와 비교합니다</p>
      </div>
    </div>
    <template #footer>
      <button class="btn" @click="open = false">닫기</button>
      <button class="btn btn-primary" :disabled="!selected" @click="restore">이 버전으로 되돌리기</button>
    </template>
  </Modal>
</template>
