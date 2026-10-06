<script setup>
// 업로드 시각 선택기: 즉시 / 예약 프리셋 / 직접 입력(서버 로컬 시간대, 과거 불가)
import { computed, ref, watch } from 'vue'
import { store } from '../lib/store'
import { localInput, resolveSchedule } from '../lib/time'

const model = defineModel({ type: Object, default: null })
defineProps({ small: Boolean })

const presets = computed(() => store.schedulePresets.length ? store.schedulePresets : [{ label: '즉시', type: 'immediate' }])
const sel = ref(0)
const custom = ref('')
const minValue = ref(localInput())

function currentIndex() {
  if (!model.value) return 0
  if (model.value.type === 'custom') return -1
  const i = presets.value.findIndex(p => p.label === model.value.label)
  return i < 0 ? 0 : i
}
watch(model, () => {
  sel.value = currentIndex()
  if (model.value?.type === 'custom') custom.value = model.value.value
}, { immediate: true })

function onSelect(e) {
  const v = +e.target.value
  sel.value = v
  if (v === -1) {
    minValue.value = localInput()
    if (!custom.value) custom.value = localInput(new Date(Date.now() + 3600000))
    model.value = { type: 'custom', label: '직접 입력', value: custom.value }
  } else {
    model.value = presets.value[v]
  }
}
function onCustom() {
  model.value = { type: 'custom', label: '직접 입력', value: custom.value }
}
const disabled = (p) => resolveSchedule(p) === null
</script>

<template>
  <div class="flex items-center gap-1 min-w-0">
    <select :class="['input', small ? 'py-1 text-xs' : '']" :value="sel" @change="onSelect" title="업로드 시각">
      <option v-for="(p, i) in presets" :key="i" :value="i" :disabled="disabled(p)">
        {{ p.label }}{{ disabled(p) ? ' (지남)' : '' }}</option>
      <option :value="-1">직접 입력…</option>
    </select>
    <input v-if="sel === -1" v-model="custom" type="datetime-local" :min="minValue"
      :class="['input', small ? 'py-1 text-xs' : '']" @change="onCustom" />
  </div>
</template>
