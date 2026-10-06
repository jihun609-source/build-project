// AutoSet 확장프로그램 서비스 워커
// - 수집 content script가 보낸 릴스 정보를 서버로 전송 (HTTPS 페이지의 mixed content 차단 회피)
// - 실패분은 chrome.storage 큐에 두었다가 다음 전송·팝업 열기·브라우저 시작 때 재전송
// - 업로드는 사용자가 팝업/웹 UI에서 누를 때 한 건만 처리 (자동 반복·타이머 없음)

const DEFAULTS = { serverUrl: 'http://localhost:8000', token: '', collectEnabled: true }

async function settings() {
  const s = await chrome.storage.local.get(['serverUrl', 'token', 'collectEnabled'])
  return { ...DEFAULTS, ...s }
}

async function apiFetch(path, init = {}) {
  const s = await settings()
  const headers = { ...(init.headers || {}), 'X-Token': s.token }
  if (init.body && typeof init.body === 'string') headers['Content-Type'] = 'application/json'
  const res = await fetch(s.serverUrl.replace(/\/$/, '') + path, { ...init, headers })
  return res
}

// ------------------------------------------------------------ 수집 큐
const today = () => new Date().toLocaleDateString('sv-SE')
let flushing = false

async function enqueue(item) {
  const { queue = [], sent = {} } = await chrome.storage.local.get(['queue', 'sent'])
  const key = item.url
  if (sent[key] || queue.some(q => q.url === key)) return
  queue.push(item)
  await chrome.storage.local.set({ queue })
  flush()
}

async function flush() {
  if (flushing) return
  flushing = true
  try {
    const { queue = [], sent = {}, counts = {} } = await chrome.storage.local.get(['queue', 'sent', 'counts'])
    if (!queue.length) return
    const batch = queue.slice(0, 50)
    const res = await apiFetch('/feed', { method: 'POST', body: JSON.stringify({ items: batch }) })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    const d = today()
    counts[d] = (counts[d] || 0) + data.results.filter(r => r.created).length
    for (const b of batch) sent[b.url] = Date.now()
    // 오래된 전송 기록 정리 (3일)
    for (const [k, t] of Object.entries(sent)) if (Date.now() - t > 3 * 86400000) delete sent[k]
    await chrome.storage.local.set({ queue: queue.slice(batch.length), sent, counts, lastError: null, lastSentAt: Date.now() })
    if (queue.length > batch.length) setTimeout(flush, 500)
  } catch (e) {
    await chrome.storage.local.set({ lastError: `전송 실패: ${e.message}` })
  } finally {
    flushing = false
  }
}

chrome.runtime.onStartup.addListener(flush)
chrome.runtime.onInstalled.addListener(() => { flush(); registerBridge() })

// ------------------------------------------------------------ 웹 UI 브리지 (다음 항목 업로드 버튼)
async function registerBridge() {
  const s = await settings()
  let origin
  try { origin = new URL(s.serverUrl).origin } catch { return }
  const pattern = `${origin}/*`
  try { await chrome.scripting.unregisterContentScripts({ ids: ['autoset-bridge'] }) } catch { /* 없음 */ }
  const ok = await chrome.permissions.contains({ origins: [pattern] })
  if (!ok) return
  try {
    await chrome.scripting.registerContentScripts([{ id: 'autoset-bridge', matches: [pattern], js: ['bridge.js'], runAt: 'document_start' }])
  } catch (e) { console.warn('bridge 등록 실패', e) }
}

// ------------------------------------------------------------ 업로드 (C안)
let uploading = false

async function uploadNext() {
  if (uploading) return { ok: false, error: '이미 업로드 중입니다' }
  const tabs = await chrome.tabs.query({ url: 'https://studio.youtube.com/*' })
  if (!tabs.length) return { ok: false, error: 'YouTube 스튜디오 탭을 열어 두세요 (로그인 상태)' }
  const tab = tabs[0]
  const res = await apiFetch('/upload/next')
  if (res.status === 204) return { ok: false, error: '업로드할 항목이 없습니다' }
  if (!res.ok) return { ok: false, error: `서버 오류 ${res.status}: ${await res.text()}` }
  const item = await res.json()
  uploading = true
  run(tab, item).finally(() => { uploading = false })
  return { ok: true, item_id: item.id, title: item.title }
}

async function run(tab, item) {
  const report = (path, body) => apiFetch(`/upload/${item.id}/${path}`, { method: 'POST', body: JSON.stringify(body) }).catch(() => {})
  try {
    let selectors = null
    try {
      const r = await apiFetch('/upload/selectors.json')
      if (r.ok) selectors = await r.json()
    } catch { /* 번들 파일 사용 */ }
    if (!selectors) selectors = await (await fetch(chrome.runtime.getURL('upload/selectors.json'))).json()

    await report('progress', { pct: 0, text: '파일 받는 중' })
    const fileRes = await apiFetch(item.file_url)
    if (!fileRes.ok) throw new Error(`완성본 파일을 받지 못했습니다 (HTTP ${fileRes.status})`)
    const buf = new Uint8Array(await fileRes.arrayBuffer())

    await chrome.tabs.update(tab.id, { active: true })
    const port = chrome.tabs.connect(tab.id, { name: 'autoset-upload' })
    const done = new Promise((resolve) => {
      port.onMessage.addListener((m) => {
        if (m.type === 'progress') report('progress', { pct: m.pct, text: m.text })
        if (m.type === 'result') resolve(m)
      })
      port.onDisconnect.addListener(() => resolve({ type: 'result', error: '스튜디오 탭 연결이 끊겼습니다 (탭을 닫거나 새로고침함)' }))
    })
    // 파일은 1MB 조각(base64)으로 나눠 전달
    const CHUNK = 1024 * 1024
    port.postMessage({ type: 'begin', item, selectors, size: buf.length })
    for (let i = 0; i < buf.length; i += CHUNK) {
      const part = buf.subarray(i, i + CHUNK)
      let bin = ''
      for (let j = 0; j < part.length; j += 0x8000) bin += String.fromCharCode.apply(null, part.subarray(j, j + 0x8000))
      port.postMessage({ type: 'chunk', data: btoa(bin) })
    }
    port.postMessage({ type: 'end' })
    const result = await done
    await report('result', { video_id: result.video_id || null, error: result.error || null,
      switched_to_immediate: !!result.switched_to_immediate })
    try { port.disconnect() } catch { /* 이미 끊김 */ }
  } catch (e) {
    await report('result', { error: e.message })
  }
}

// ------------------------------------------------------------ 메시지
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    switch (msg.type) {
      case 'feed': {
        const s = await settings()
        if (s.collectEnabled) await enqueue(msg.item)
        sendResponse({ ok: true })
        break
      }
      case 'diag':
        await chrome.storage.local.set({ diag: { ...msg.diag, tab: sender.tab?.url } })
        sendResponse({ ok: true })
        break
      case 'dom-warning':
        await chrome.storage.local.set({ domWarning: { at: Date.now(), message: msg.message } })
        sendResponse({ ok: true })
        break
      case 'dom-ok':
        await chrome.storage.local.set({ domWarning: null })
        sendResponse({ ok: true })
        break
      case 'flush':
        await flush()
        sendResponse({ ok: true })
        break
      case 'upload-next':
        sendResponse(await uploadNext())
        break
      case 'register-bridge':
        await registerBridge()
        sendResponse({ ok: true })
        break
      case 'health': {
        try {
          const h = await apiFetch('/health')
          const a = await apiFetch('/upload/status')
          sendResponse({ ok: h.ok, auth: a.ok, status: a.ok ? await a.json() : null, code: a.status })
        } catch (e) { sendResponse({ ok: false, error: e.message }) }
        break
      }
      default:
        sendResponse({ ok: false })
    }
  })()
  return true
})
