// /ws 실시간 이벤트. 끊기면 지수 백오프로 재연결하고, 끊긴 동안은 폴링 이벤트를 흘려
// 각 화면이 REST로 다시 읽게 한다.
import { reactive } from 'vue'

export const wsState = reactive({ connected: false, lastMessage: null })
const handlers = new Map()   // type → Set(fn)
let sock = null
let retry = 0
let pollTimer = null
let pingTimer = null

export function on(type, fn) {
  if (!handlers.has(type)) handlers.set(type, new Set())
  handlers.get(type).add(fn)
  return () => handlers.get(type)?.delete(fn)
}

function dispatch(type, data) {
  handlers.get(type)?.forEach(fn => { try { fn(data) } catch (e) { console.error(e) } })
  handlers.get('*')?.forEach(fn => { try { fn(type, data) } catch (e) { console.error(e) } })
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(() => dispatch('poll', {}), 5000)
}

function stopPolling() {
  clearInterval(pollTimer)
  pollTimer = null
}

export function connect() {
  if (sock && (sock.readyState === 0 || sock.readyState === 1)) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  sock = new WebSocket(`${proto}://${location.host}/ws`)
  sock.onopen = () => {
    wsState.connected = true
    retry = 0
    stopPolling()
    dispatch('poll', {})           // 끊긴 동안 놓친 변경 반영
    clearInterval(pingTimer)
    pingTimer = setInterval(() => sock?.readyState === 1 && sock.send('ping'), 25000)
  }
  sock.onmessage = (ev) => {
    let msg
    try { msg = JSON.parse(ev.data) } catch { return }
    wsState.lastMessage = Date.now()
    dispatch(msg.type, msg.data)
  }
  sock.onclose = (ev) => {
    wsState.connected = false
    clearInterval(pingTimer)
    startPolling()
    if (ev.code === 4401) return    // 토큰 없음: 로그인 후 다시 연결
    const delay = Math.min(30000, 1000 * 2 ** retry++)
    setTimeout(connect, delay)
  }
  sock.onerror = () => sock?.close()
}

export function disconnect() {
  sock?.close()
}
