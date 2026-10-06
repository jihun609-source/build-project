// 시간 표시·입력은 서버 로컬 시간대(config.timezone) 기준.
import { store } from './store'

const tz = () => store.status?.server?.timezone || 'Asia/Seoul'

function parts(date) {
  const p = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz(), year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    hourCycle: 'h23', weekday: 'short',
  }).formatToParts(date)
  const o = Object.fromEntries(p.map(x => [x.type, x.value]))
  return o
}

const WD = { Mon: '월', Tue: '화', Wed: '수', Thu: '목', Fri: '금', Sat: '토', Sun: '일' }

export function fmt(iso, withDay = true) {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d)) return iso
  const p = parts(d)
  const base = `${+p.month}/${+p.day}`
  return `${base}${withDay ? ` (${WD[p.weekday] || p.weekday})` : ''} ${p.hour}:${p.minute}`
}

export function fmtFull(iso) {
  if (!iso) return ''
  const p = parts(new Date(iso))
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`
}

export function ago(iso) {
  if (!iso) return ''
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return '방금'
  if (s < 3600) return `${Math.floor(s / 60)}분 전`
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`
  return `${Math.floor(s / 86400)}일 전`
}

// 서버 로컬 "YYYY-MM-DDTHH:mm" (datetime-local 입력값)
export function localInput(date = new Date()) {
  const p = parts(date)
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`
}

export function addDaysLocal(dateStr, days) {
  const [y, m, d] = dateStr.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d + days))
  return dt.toISOString().slice(0, 10)
}

// 예약 프리셋 → fetch 요청 body 조각. 과거면 null
export function resolveSchedule(preset) {
  if (!preset) return { publish_at: null, use_next_slot: false }
  switch (preset.type) {
    case 'immediate': return { publish_at: null, use_next_slot: false }
    case 'next_slot': return { publish_at: null, use_next_slot: true }
    case 'offset': return { publish_at: new Date(Date.now() + preset.minutes * 60000).toISOString(), use_next_slot: false }
    case 'at': {
      const today = localInput().slice(0, 10)
      const target = `${addDaysLocal(today, preset.day_offset || 0)}T${preset.time}`
      if (target <= localInput()) return null
      return { publish_at: target, use_next_slot: false }
    }
    case 'custom': {
      if (!preset.value || preset.value <= localInput()) return null
      return { publish_at: preset.value, use_next_slot: false }
    }
  }
  return { publish_at: null, use_next_slot: false }
}

export function scheduleLabel(preset) {
  if (!preset) return '즉시'
  if (preset.type === 'custom') return preset.value ? preset.value.replace('T', ' ').slice(5) : '직접 입력'
  return preset.label
}
