const $ = (id) => document.getElementById(id)

async function refresh() {
  const s = await chrome.storage.local.get(['autoNext', 'serverUrl', 'token', 'collectEnabled', 'queue', 'counts', 'domWarning', 'lastError', 'diag', 'lastSentAt'])
  const d = s.diag
  const t = (ms) => (ms ? new Date(ms).toLocaleTimeString() : '-')
  $('diag').textContent = d
    ? [
        `수집 스크립트 v${d.version} · ${t(d.at)}`,
        `탭: ${d.tab || '-'}`,
        `감지한 영상: ${d.videos}개 · 기록: ${d.captures}건`,
        `마지막: ${d.last ? `${t(d.last.at)} ${d.last.code || ''} @${d.last.author || '?'} ${d.last.note || ''}` : '-'}`,
        `마지막 서버 전송: ${t(s.lastSentAt)}`,
        d.error ? `오류: ${d.error}` : '오류: 없음',
      ].join('\n')
    : ['인스타그램 탭에서 수집 스크립트가 아직 실행되지 않았습니다.', '인스타그램 탭을 새로고침하세요.'].join('\n')
  if (d?.error) $('diagBox').open = true
  $('server').value = s.serverUrl || 'http://localhost:8000'
  $('token').value = s.token || ''
  $('enabled').checked = s.collectEnabled !== false
  $('autoNext').checked = !!s.autoNext
  $('today').textContent = (s.counts || {})[new Date().toLocaleDateString('sv-SE')] || 0
  $('queue').textContent = (s.queue || []).length
  const w = s.domWarning
  $('warning').hidden = !w
  if (w) $('warning').textContent = `⚠ 인스타그램 화면 구조를 인식하지 못했습니다: ${w.message}`
  $('error').hidden = !s.lastError
  $('error').textContent = s.lastError || ''
  chrome.runtime.sendMessage({ type: 'health' }, (r) => {
    const c = $('conn')
    if (!r?.ok) { c.textContent = '● 서버 연결 안 됨'; c.style.color = '#f87171'; return }
    if (!r.auth) { c.textContent = '● 토큰 오류'; c.style.color = '#fbbf24'; return }
    c.textContent = `● 연결됨 · 업로드 ${r.status.mode === 'extension' ? '확장' : 'API'}`
    c.style.color = '#34d399'
  })
}

$('autoNext').addEventListener('change', async (e) => {
  await chrome.storage.local.set({ autoNext: e.target.checked })
})

$('enabled').addEventListener('change', async (e) => {
  await chrome.storage.local.set({ collectEnabled: e.target.checked })
})

$('save').addEventListener('click', async () => {
  let url = $('server').value.trim().replace(/\/$/, '')
  if (!/^https?:\/\//.test(url)) url = 'http://' + url
  let origin
  try { origin = new URL(url).origin } catch { return alert('서버 주소가 올바르지 않습니다') }
  // 서버 주소 권한 요청 (관리 UI 브리지 스크립트용)
  try { await chrome.permissions.request({ origins: [`${origin}/*`] }) } catch { /* 거절해도 수집은 동작 */ }
  await chrome.storage.local.set({ serverUrl: url, token: $('token').value.trim(), lastError: null })
  chrome.runtime.sendMessage({ type: 'register-bridge' })
  chrome.runtime.sendMessage({ type: 'flush' }, refresh)
})

$('open').addEventListener('click', async () => {
  const { serverUrl } = await chrome.storage.local.get('serverUrl')
  chrome.tabs.create({ url: (serverUrl || 'http://localhost:8000').replace(/\/$/, '') + '/ui/' })
})

$('upload').addEventListener('click', () => {
  $('uploadMsg').hidden = true
  $('upload').disabled = true
  chrome.runtime.sendMessage({ type: 'upload-next' }, (r) => {
    $('upload').disabled = false
    $('uploadMsg').hidden = false
    $('uploadMsg').className = r?.ok ? 'ok' : 'err'
    $('uploadMsg').textContent = r?.ok ? `#${r.item_id} 업로드 시작: ${r.title}` : (r?.error || '실패')
  })
})

chrome.runtime.sendMessage({ type: 'flush' }, refresh)
refresh()
