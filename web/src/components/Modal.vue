<script setup>
const open = defineModel({ type: Boolean, default: false })
defineProps({ title: String, wide: Boolean })
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-50 bg-black/40 flex items-end md:items-center justify-center p-0 md:p-4"
      @click.self="open = false">
      <div :class="['card w-full max-h-[92vh] flex flex-col rounded-b-none md:rounded-lg', wide ? 'md:max-w-5xl' : 'md:max-w-lg']">
        <div class="flex items-center px-4 py-3 border-b">
          <h3 class="font-semibold flex-1">{{ title }}</h3>
          <button class="text-slate-400 hover:text-slate-700 text-xl leading-none" @click="open = false">×</button>
        </div>
        <div class="p-4 overflow-auto"><slot /></div>
        <div v-if="$slots.footer" class="px-4 py-3 border-t flex justify-end gap-2"><slot name="footer" /></div>
      </div>
    </div>
  </Teleport>
</template>
