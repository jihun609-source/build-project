<script setup>
import { store } from '../lib/store'
const model = defineModel({ type: String, default: '' })
defineProps({ small: Boolean })
</script>

<template>
  <select v-model="model" :class="['input', small ? 'py-1 text-xs' : '']" title="편집 프리셋">
    <option v-for="p in store.presets" :key="p.name" :value="p.name">{{ p.name }}{{ p.default ? ' (기본)' : '' }}</option>
  </select>
</template>
