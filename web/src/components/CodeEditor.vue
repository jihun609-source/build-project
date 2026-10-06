<script setup>
// CodeMirror 6 편집기 (markdown / json 하이라이트)
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState } from '@codemirror/state'
import { markdown } from '@codemirror/lang-markdown'
import { json } from '@codemirror/lang-json'

const model = defineModel({ type: String, default: '' })
const props = defineProps({ lang: { type: String, default: 'markdown' }, readonly: Boolean })
const el = ref(null)
let view = null

onMounted(() => {
  view = new EditorView({
    parent: el.value,
    state: EditorState.create({
      doc: model.value ?? '',
      extensions: [
        basicSetup,
        props.lang === 'json' ? json() : markdown(),
        EditorView.lineWrapping,
        EditorState.readOnly.of(props.readonly),
        EditorView.updateListener.of((u) => { if (u.docChanged) model.value = u.state.doc.toString() }),
      ],
    }),
  })
})
watch(model, (v) => {
  if (view && (v ?? '') !== view.state.doc.toString()) {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: v ?? '' } })
  }
})
onBeforeUnmount(() => view?.destroy())

function insert(text) {
  if (!view) return
  const { from, to } = view.state.selection.main
  view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length } })
  view.focus()
}
defineExpose({ insert })
</script>

<template>
  <div ref="el" class="border rounded-md overflow-hidden bg-white h-full min-h-[300px]" />
</template>
