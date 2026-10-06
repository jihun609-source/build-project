<script setup>
// 4칸 스텝 바: 다운로드 → 편집 → 자막 → 업로드
const props = defineProps({ stages: { type: Array, default: () => [] }, compact: Boolean })
const NAMES = { download: '다운로드', edit: '편집', caption: '자막', upload: '업로드' }
const DETAIL = { 추출: '추출', 인식: '음성 인식', 생성: 'AI 생성', 합성: '합성' }

function cls(s) {
  switch (s.state) {
    case 'done': return 'bg-emerald-500 text-white'
    case 'running': return 'bg-indigo-500 text-white running-stripes'
    case 'failed': return 'bg-red-500 text-white'
    case 'review': return 'bg-amber-400 text-amber-950'
    case 'held': return 'bg-sky-200 text-sky-800'
    case 'skipped': return 'bg-slate-300 text-slate-600'
    default: return 'bg-slate-200 text-slate-500'
  }
}
function label(s) {
  const n = NAMES[s.stage]
  if (s.state === 'done') return props.compact ? '✓' : `✓ ${n}`
  if (s.state === 'failed') return `✕ ${n}`
  if (s.state === 'review') return '검토'
  if (s.state === 'held') return '보류'
  if (s.state === 'running') {
    if (s.stage === 'caption' && s.detail) return DETAIL[s.detail] || s.detail
    return `${Math.round(s.pct)}%`
  }
  return n
}
function tip(s) {
  let t = `${NAMES[s.stage]}: ${s.state}`
  if (s.detail) t += ` · ${s.detail}`
  if (s.state === 'running') t += ` · ${Math.round(s.pct)}%`
  if (s.error) t += `\n${s.error}`
  return t
}
</script>

<template>
  <div>
    <div class="grid grid-cols-4 gap-0.5 text-[11px] font-medium">
      <div v-for="s in stages" :key="s.stage" :title="tip(s)"
        :class="['relative h-6 rounded-sm overflow-hidden grid place-items-center', cls(s)]">
        <div v-if="s.state === 'running'" class="absolute inset-y-0 left-0 bg-indigo-800/40" :style="{ width: s.pct + '%' }" />
        <span class="relative truncate px-1">{{ label(s) }}</span>
      </div>
    </div>
    <p v-for="s in stages.filter(x => x.state === 'failed' && x.error)" :key="s.stage"
      class="mt-1 text-[11px] text-red-600 truncate" :title="s.error">{{ s.error }}</p>
  </div>
</template>
