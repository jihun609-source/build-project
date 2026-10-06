<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, qs } from '../lib/api'
import { on } from '../lib/ws'
import { STATUS_LABEL, statusColor } from '../lib/store'
import { fmt } from '../lib/time'
import ItemDetail from '../components/ItemDetail.vue'

const route = useRoute()
const router = useRouter()
const items = ref([])
const counts = ref({})
const tab = ref('all')
const q = ref('')

const TABS = [
  ['all', '전체', null],
  ['download', '다운로드', 'queued,downloading,failed_download'],
  ['held', '보류', 'held'],
  ['edit', '편집', 'downloaded,editing,failed_edit'],
  ['caption', '자막', 'edited,captioning,failed_caption'],
  ['review', '검토 대기', null],
  ['upload', '업로드 대기', 'captioned,uploading,failed_upload'],
  ['scheduled', '예약 대기', null],
  ['failed', '실패만', null],
  ['uploaded', '업로드됨', 'uploaded'],
  ['skipped', '건너뜀', 'skipped'],
]
const selectedId = computed(() => (route.params.id ? +route.params.id : null))

function params() {
  const t = TABS.find(x => x[0] === tab.value)
  return { status: t?.[2], failed: tab.value === 'failed', review: tab.value === 'review', scheduled: tab.value === 'scheduled', q: q.value }
}
async function load() {
  const r = await api.get('/items' + qs({ ...params(), limit: 200 }))
  items.value = r.items
  counts.value = r.counts
}
function tabCount(t) {
  if (t[0] === 'all') return Object.values(counts.value).reduce((a, b) => a + b, 0)
  if (t[0] === 'failed') return Object.entries(counts.value).filter(([k]) => k.startsWith('failed')).reduce((a, [, v]) => a + v, 0)
  if (t[0] === 'review') return counts.value.captioned_review || 0
  if (!t[2]) return ''
  return t[2].split(',').reduce((a, s) => a + (counts.value[s] || 0), 0)
}

let timer = null
const offs = []
onMounted(() => {
  if (route.query.failed) tab.value = 'failed'
  if (route.query.review) tab.value = 'review'
  load()
  offs.push(on('item', (it) => {
    const i = items.value.findIndex(x => x.id === it.id)
    if (i >= 0) items.value[i] = { ...items.value[i], ...it }
    clearTimeout(timer)
    timer = setTimeout(load, 1500)
  }))
  offs.push(on('poll', load))
})
onBeforeUnmount(() => offs.forEach(f => f()))
watch(tab, load)

function open(id) { router.push(`/pipeline/${id}`) }
function close() { router.push('/pipeline') }
function current(it) {
  return it.stage_progress.find(s => ['running', 'failed', 'review'].includes(s.state)) ||
    it.stage_progress.find(s => s.state === 'pending') || it.stage_progress[3]
}
const STAGE = { download: '다운로드', edit: '편집', caption: '자막', upload: '업로드' }
</script>

<template>
  <div class="grid gap-4" :class="selectedId ? 'xl:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]' : ''">
    <div :class="['space-y-3 min-w-0', selectedId && 'hidden xl:block']">
      <div class="flex gap-1 overflow-x-auto border-b">
        <button v-for="t in TABS" :key="t[0]" :class="['tab', tab === t[0] && 'tab-active']" @click="tab = t[0]">
          {{ t[1] }} <span class="text-xs opacity-60">{{ tabCount(t) }}</span></button>
      </div>
      <div class="flex gap-2">
        <input v-model="q" class="input max-w-xs" placeholder="제목·캡션·작성자 검색" @keyup.enter="load" />
        <button class="btn" @click="load">검색</button>
      </div>
      <div class="card overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="bg-slate-50 text-xs text-slate-500">
            <tr>
              <th class="p-2 text-left">항목</th><th class="p-2 text-left">단계</th><th class="p-2 hidden md:table-cell">프리셋</th>
              <th class="p-2 text-left">게시</th><th class="p-2 hidden lg:table-cell">업로드</th><th class="p-2 hidden lg:table-cell">AI</th>
            </tr>
          </thead>
          <tbody class="divide-y">
            <tr v-for="it in items" :key="it.id" :class="['hover:bg-slate-50 cursor-pointer', selectedId === it.id && 'bg-indigo-50']"
              @click="open(it.id)">
              <td class="p-2">
                <div class="flex gap-2 items-center min-w-[12rem]">
                  <img v-if="it.thumb" :src="it.thumb"
                    class="w-9 h-14 object-cover rounded bg-slate-200 shrink-0" @error="e => e.target.style.visibility = 'hidden'" />
                  <div class="min-w-0">
                    <div class="font-medium truncate max-w-[16rem]">#{{ it.id }} {{ it.title || it.feed_caption?.slice(0, 30) || '' }}</div>
                    <div class="text-xs text-slate-400">@{{ it.feed_author }}</div>
                    <div v-if="it.error" class="text-[11px] text-red-600 truncate max-w-[16rem]" :title="it.error">{{ it.error }}</div>
                  </div>
                </div>
              </td>
              <td class="p-2 min-w-[8rem]">
                <span :class="['badge', statusColor(it.status)]">{{ STATUS_LABEL[it.status] }}</span>
                <div class="h-1.5 bg-slate-200 rounded mt-1 overflow-hidden">
                  <div :class="['h-full', current(it).state === 'failed' ? 'bg-red-500' : 'bg-indigo-500']"
                    :style="{ width: (it.status === 'uploaded' ? 100 : current(it).pct) + '%' }" />
                </div>
                <div class="text-[10px] text-slate-400">{{ STAGE[current(it).stage] }} {{ current(it).detail || '' }}</div>
              </td>
              <td class="p-2 text-center text-xs hidden md:table-cell">{{ it.edit_preset }}</td>
              <td class="p-2 text-xs whitespace-nowrap">
                <span v-if="it.publish_mode === 'scheduled'" class="text-indigo-700">⏱ {{ fmt(it.publish_at) }}</span>
                <span v-else class="text-slate-500">즉시</span>
              </td>
              <td class="p-2 text-center text-xs hidden lg:table-cell">
                <a v-if="it.video_id" :href="`https://youtu.be/${it.video_id}`" target="_blank" class="text-red-600" @click.stop>▶</a>
                {{ it.upload_mode || '' }}
              </td>
              <td class="p-2 text-center text-xs hidden lg:table-cell">{{ it.ai_provider_used || '' }}</td>
            </tr>
            <tr v-if="!items.length"><td colspan="6" class="p-6 text-center text-slate-400">항목이 없습니다</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <div v-if="selectedId" class="min-w-0">
      <div class="card p-4 xl:sticky xl:top-14 xl:max-h-[calc(100vh-5rem)] xl:overflow-auto">
        <ItemDetail :id="selectedId" @close="close" @deleted="close(); load()" />
      </div>
    </div>
  </div>
</template>
