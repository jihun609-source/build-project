// 인스타그램 릴스 수집: 사용자가 직접 스크롤하는 동안 화면 중앙에 온 릴스 정보만 기록한다.
// 자동 스크롤·페이지 순회·백그라운드 탭 수집·영상 다운로드는 하지 않는다.
(() => {
  const REEL_PATH = /^\/(?:[^/]+\/)?reels?\/([A-Za-z0-9_-]+)/
  const RESERVED = new Set(['explore', 'reels', 'reel', 'p', 'stories', 'direct', 'accounts', 'about', 'legal', 'tv', 'web'])
  const sentThisSession = new Set()
  const observed = new WeakSet()
  let failures = 0
  let current = null
  let settleTimer = null

  // 확장프로그램을 새로고침하면 이 탭의 옛 스크립트는 연결이 끊긴다 (Extension context invalidated).
  // 에러를 내지 않고 조용히 멈춘다. 다시 수집하려면 인스타그램 탭을 새로고침.
  let dead = false
  function send(msg) {
    if (dead) return
    try {
      if (!chrome.runtime?.id) throw new Error('invalidated')
      chrome.runtime.sendMessage(msg, () => void chrome.runtime.lastError)
    } catch {
      dead = true
      try { io.disconnect(); mo.disconnect() } catch { /* 아직 생성 전 */ }
      console.info('[AutoSet] 확장프로그램이 새로고침되었습니다. 이 탭을 새로고침하면 다시 기록합니다.')
    }
  }

  // 팝업 진단용 상태 (몇 초마다가 아니라 변화가 있을 때만 전송)
  const diag = { version: chrome.runtime.getManifest?.().version, page: '', videos: 0, captures: 0, last: null, error: null }
  function report(patch) {
    Object.assign(diag, patch, { page: location.pathname, at: Date.now() })
    send({ type: 'diag', diag })
  }
  window.addEventListener('error', (e) => {
    if (String(e.filename || '').includes('collect/content.js')) report({ error: `${e.message} (줄 ${e.lineno})` })
  })

  const onReelPage = () => REEL_PATH.test(location.pathname) || location.pathname.startsWith('/reels')

  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting && e.intersectionRatio >= 0.6) {
        current = e.target
        clearTimeout(settleTimer)
        // 스크롤이 멈추고 주소가 바뀐 뒤 읽는다
        settleTimer = setTimeout(() => {
          try { capture(e.target) } catch (err) { report({ error: `capture: ${err.message}` }) }
        }, 900)
      }
    }
  }, { threshold: [0.6] })

  function watchVideos() {
    if (!onReelPage()) return
    let added = 0
    document.querySelectorAll('video').forEach((v) => {
      if (!observed.has(v)) { observed.add(v); io.observe(v); added++ }
    })
    if (added) report({ videos: diag.videos + added })
  }

  function container(video) {
    // 영상 하나를 감싸는, 작성자 링크를 포함한 가장 가까운 블록
    let el = video
    for (let i = 0; i < 14 && el.parentElement; i++) {
      el = el.parentElement
      if (el.querySelector('a[href*="/reel/"], a[href*="/reels/"]') && findAuthor(el)) return el
      if (el.getBoundingClientRect().height > window.innerHeight * 1.6) break
    }
    return video.closest('article') || video.parentElement?.parentElement?.parentElement || document.body
  }

  function findAuthor(root) {
    for (const a of root.querySelectorAll('a[href^="/"]')) {
      const m = a.getAttribute('href').match(/^\/([A-Za-z0-9._]{1,30})\/?(?:reels\/)?$/)
      if (m && !RESERVED.has(m[1]) && (a.textContent || '').trim()) return m[1]
    }
    return null
  }

  function findCode(root) {
    const m = location.pathname.match(REEL_PATH)
    if (m && document.querySelectorAll('video').length <= 1) return m[1]
    // 피드형 화면: 컨테이너 안의 릴스 링크 우선, 없으면 주소창
    for (const a of root.querySelectorAll('a[href*="/reel/"], a[href*="/reels/"]')) {
      const mm = a.getAttribute('href').match(REEL_PATH)
      if (mm && mm[1] !== 'audio') return mm[1]
    }
    return m ? m[1] : null
  }

  function findCaption(root, author) {
    let best = ''
    root.querySelectorAll('h1, span[dir="auto"], div[dir="auto"]').forEach((s) => {
      const t = (s.innerText || '').trim()
      if (t && t !== author && t.length > best.length && t.length < 2500 && !/^\d+(\.\d+)?[천만KMk]?$/.test(t)) best = t
    })
    return best || null
  }

  // 재생 중인 영상의 현재 프레임 → 작은 JPEG data URL (실패하면 null)
  function grabFrame(video) {
    try {
      if (!video.videoWidth || video.readyState < 2) return null
      const w = 270
      const h = Math.round(video.videoHeight * w / video.videoWidth)
      const c = document.createElement('canvas')
      c.width = w
      c.height = h
      c.getContext('2d').drawImage(video, 0, 0, w, h)
      return c.toDataURL('image/jpeg', 0.75)
    } catch {
      return null   // 교차 출처 영상이면 캔버스가 막힘 → 서버가 따로 받아온다
    }
  }

  function coverImage(root, video) {
    if (video.poster) return video.poster
    // 영상과 같은 크기로 깔린 표지 이미지
    const r = video.getBoundingClientRect()
    for (const img of root.querySelectorAll('img')) {
      const b = img.getBoundingClientRect()
      if (img.src?.startsWith('http') && b.width > r.width * 0.6 && b.height > r.height * 0.6) return img.src
    }
    return null
  }

  function capture(video) {
    if (video !== current || !onReelPage()) return
    const root = container(video)
    const code = findCode(root)
    const author = findAuthor(root)
    if (!code) {
      report({ last: { at: Date.now(), code: null, note: '릴스 주소를 찾지 못함' } })
      if (++failures >= 3) send({ type: 'dom-warning', message: '릴스 주소를 찾지 못함' })
      return
    }
    if (!author && ++failures >= 3) send({ type: 'dom-warning', message: '작성자를 찾지 못함' })
    if (author) { failures = 0; send({ type: 'dom-ok' }) }
    const url = `https://www.instagram.com/reel/${code}/`
    if (sentThisSession.has(url)) return
    sentThisSession.add(url)
    report({ captures: diag.captures + 1, last: { at: Date.now(), code, author }, error: null })
    const og = document.querySelector('meta[property="og:image"]')?.content
    send({
      type: 'feed',
      item: {
        url,
        author,
        caption: findCaption(root, author),
        thumbnail_url: coverImage(root, video) || (location.pathname.includes(code) ? og : null) || null,
        thumbnail_data: grabFrame(video),
        seen_at: new Date().toISOString(),
      },
    })
  }

  report({})
  // ---------------------------------------------------------------- 자동 다음 릴스 (팝업에서 켜고 끔, 기본 꺼짐)
  // 인스타 릴스는 반복 재생(loop)이라 ended 이벤트가 오지 않는다. 재생 위치가 끝부분에서 처음으로
  // 돌아가는 순간을 "한 번 다 봤음"으로 본다. 탭이 화면에 보일 때만 동작한다.
  let autoNext = false
  let advancing = false
  const lastTime = new WeakMap()
  try {
    chrome.storage.local.get('autoNext', (s) => { autoNext = !!s?.autoNext })
    chrome.storage.onChanged.addListener((ch) => { if (ch.autoNext) autoNext = !!ch.autoNext.newValue })
  } catch { /* 확장프로그램 새로고침됨 */ }

  function scrollParent(el) {
    for (let p = el.parentElement; p; p = p.parentElement) {
      const oy = getComputedStyle(p).overflowY
      if ((oy === 'auto' || oy === 'scroll') && p.scrollHeight > p.clientHeight + 10) return p
    }
    return document.scrollingElement
  }

  function goNext(video) {
    if (advancing) return
    advancing = true
    const vids = [...document.querySelectorAll('video')]
    const r0 = video.getBoundingClientRect()
    // 화면상 현재 영상보다 아래에 있는 가장 가까운 영상
    const next = vids.map(v => [v, v.getBoundingClientRect().top]).filter(([v, t]) => v !== video && t > r0.top + 20)
      .sort((a, b) => a[1] - b[1])[0]?.[0]
    if (next) {
      next.scrollIntoView({ behavior: 'smooth', block: 'center' })
    } else {
      const sp = scrollParent(video)
      sp.scrollBy({ top: sp === document.scrollingElement ? window.innerHeight : sp.clientHeight, behavior: 'smooth' })
    }
    setTimeout(() => { advancing = false }, 1500)
  }

  document.addEventListener('timeupdate', (e) => {
    const v = e.target
    if (!(v instanceof HTMLVideoElement) || dead) return
    const prev = lastTime.get(v) ?? 0
    lastTime.set(v, v.currentTime)
    if (!autoNext || v !== current || document.visibilityState !== 'visible' || !onReelPage()) return
    const d = v.duration
    if (!d || !isFinite(d) || d < 2) return
    // 끝부분(마지막 1초 이내)에서 앞부분(1초 이내)으로 되감김 = 한 바퀴 재생 완료
    if (prev > d - 1 && v.currentTime < 1) goNext(v)
  }, true)
  document.addEventListener('ended', (e) => {
    if (autoNext && e.target === current && document.visibilityState === 'visible') goNext(e.target)
  }, true)

  const mo = new MutationObserver(() => { if (!dead) watchVideos() })
  mo.observe(document.documentElement, { childList: true, subtree: true })
  watchVideos()
})()
