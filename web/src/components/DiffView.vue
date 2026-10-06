<script setup>
import { computed } from 'vue'
import { diffLines } from 'diff'

const props = defineProps({
  oldText: { type: String, default: '' }, newText: { type: String, default: '' },
  oldLabel: { type: String, default: '선택한 버전' }, newLabel: { type: String, default: '현재' },
})
const parts = computed(() => diffLines(props.oldText || '', props.newText || ''))
function cls(p) {
  if (p.added) return 'bg-emerald-100 text-emerald-900 block'
  if (p.removed) return 'bg-red-100 text-red-900 line-through block'
  return 'text-slate-600'
}
</script>

<template>
  <div>
    <div class="flex gap-3 text-xs mb-2">
      <span class="badge bg-red-100 text-red-700">− {{ oldLabel }}</span>
      <span class="badge bg-emerald-100 text-emerald-700">+ {{ newLabel }}</span>
    </div>
    <pre class="text-xs font-mono border rounded bg-slate-50 overflow-auto max-h-[60vh] p-2 whitespace-pre-wrap"><span
      v-for="(p, i) in parts" :key="i" :class="cls(p)">{{ p.value }}</span></pre>
  </div>
</template>
