// 저장하지 않은 변경 추적: 상단 "저장 안 됨" 표시 + 페이지 이탈 확인
import { computed, onBeforeUnmount, onMounted, reactive, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'

export const dirtyRegistry = reactive(new Set())

export function useDirty(key, isDirty) {
  const dirty = computed(isDirty)
  watch(dirty, (v) => (v ? dirtyRegistry.add(key) : dirtyRegistry.delete(key)), { immediate: true })
  const unload = (e) => {
    if (dirty.value) { e.preventDefault(); e.returnValue = '' }
  }
  onMounted(() => window.addEventListener('beforeunload', unload))
  onBeforeUnmount(() => {
    window.removeEventListener('beforeunload', unload)
    dirtyRegistry.delete(key)
  })
  onBeforeRouteLeave(() => {
    if (dirty.value && !window.confirm('저장하지 않은 변경이 있습니다. 이 페이지를 떠날까요?')) return false
    dirtyRegistry.delete(key)
    return true
  })
  return { dirty }
}

export const clone = (o) => JSON.parse(JSON.stringify(o ?? null))
export const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
