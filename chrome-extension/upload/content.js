// YouTube 스튜디오 업로드 (C안). 요청받은 한 건만 처리하고 멈춘다.
// 순서: 파일 수신 → 업로드 대화상자 → 파일 주입 → 제목·설명·태그 → 아동용 아님 → 공개 설정 → 완료
// 예상 못 한 대화상자는 닫거나 넘기지 않고 즉시 중단해 오류로 보고한다.
(() => {
  const STEP_TIMEOUT = 60000
  let S = null            // selectors.json
  let port = null

  const sleep = (ms) => new Promise(r => setTimeout(r, ms))
  const visible = (el) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'

  function q(list, root = document) {
    for (const sel of [].concat(list || [])) {
      const all = root.querySelectorAll(sel)
      for (const el of all) if (visible(el) || el.type === 'file') return el
    }
    return null
  }

  class Abort extends Error {}

  function checkDialogs() {
    const known = (S.known_dialogs || []).join(',')
    for (const sel of S.any_dialog || []) {
      for (const d of document.querySelectorAll(sel)) {
        if (!visible(d)) continue
        if (known && (d.matches(known) || d.closest(known) || d.querySelector(known))) continue
        const text = (d.innerText || '').trim().slice(0, 120).replace(/\s+/g, ' ')
        throw new Abort(`예상하지 못한 대화상자가 떠서 중단했습니다: "${text}"`)
      }
    }
  }

  async function waitFor(list, what, timeout = STEP_TIMEOUT, cond = () => true) {
    const t0 = Date.now()
    while (Date.now() - t0 < timeout) {
      checkDialogs()
      const el = q(list)
      if (el && cond(el)) return el
      await sleep(300)
    }
    throw new Abort(`${what} 요소를 ${Math.round(timeout / 1000)}초 안에 찾지 못했습니다 (selectors.json 확인)`)
  }

  const enabled = (el) => !el.hasAttribute('disabled') && el.getAttribute('aria-disabled') !== 'true'

  async function click(list, what) {
    const el = await waitFor(list, what, STEP_TIMEOUT, enabled)
    el.scrollIntoView({ block: 'center' })
    el.click()
    await sleep(400)
    return el
  }

  async function setEditable(list, text, what) {
    const el = await waitFor(list, what)
    el.focus()
    document.execCommand('selectAll', false, null)
    document.execCommand('delete', false, null)
    if (text) document.execCommand('insertText', false, text)
    el.dispatchEvent(new Event('input', { bubbles: true }))
    await sleep(300)
  }

  async function typeInput(el, text) {
    el.focus()
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    setter.call(el, text)
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.dispatchEvent(new Event('change', { bubbles: true }))
    await sleep(200)
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }))
    el.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }))
    await sleep(400)
  }

  const progress = (pct, text) => port?.postMessage({ type: 'progress', pct, text })

  function readProgress() {
    const el = q(S.progress_label)
    const text = (el?.innerText || '').trim()
    const m = text.match(/(\d{1,3})\s*%/)
    return { text, pct: m ? +m[1] : null }
  }

  function readVideoId() {
    const a = q(S.video_link)
    const href = a?.href || a?.innerText || ''
    const m = href.match(/(?:youtu\.be\/|shorts\/|v=)([A-Za-z0-9_-]{11})/)
    return m ? m[1] : null
  }

  function banner(text, error) {
    let b = document.getElementById('autoset-banner')
    if (!b) {
      b = document.createElement('div')
      b.id = 'autoset-banner'
      b.style.cssText = 'position:fixed;z-index:999999;top:8px;left:50%;transform:translateX(-50%);padding:8px 14px;border-radius:6px;font:13px "Malgun Gothic",sans-serif;color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.3)'
      document.body.appendChild(b)
    }
    b.style.background = error ? '#dc2626' : '#4f46e5'
    b.textContent = `AutoSet: ${text}`
    if (!error) setTimeout(() => b.remove(), 8000)
  }

  async function upload(item, file) {
    let switched = false
    banner(`#${item.id} 업로드 시작`)
    progress(1, '업로드 대화상자 여는 중')
    if (!q(S.upload_dialog)) {
      await click(S.create_button, '만들기 버튼')
      await click(S.upload_menu_item, '동영상 업로드 메뉴')
    }
    await waitFor(S.upload_dialog, '업로드 대화상자')
    const input = await waitFor(S.file_input, '파일 선택')
    const dt = new DataTransfer()
    dt.items.add(file)
    input.files = dt.files
    input.dispatchEvent(new Event('change', { bubbles: true }))
    progress(3, '파일 전달됨')

    await sleep(2500)
    await setEditable(S.title_box, item.title, '제목 입력칸')
    await setEditable(S.description_box, item.description, '설명 입력칸')
    await click(S.not_made_for_kids, '아동용 아님 선택')
    if (item.tags?.length) {
      const more = q(S.show_more)
      if (more) { more.click(); await sleep(600) }
      const tagInput = await waitFor(S.tags_input, '태그 입력칸')
      await typeInput(tagInput, item.tags.join(',') + ',')
    }
    progress(10, '세부정보 입력 완료')

    for (let i = 0; i < 3; i++) await click(S.next_button, `다음 버튼 ${i + 1}`)

    if (item.publish_mode === 'scheduled') {
      await click(S.schedule_expand, '예약 펼치기')
      await click(S.schedule_date_trigger, '예약 날짜')
      const dateInput = await waitFor(S.schedule_date_input, '예약 날짜 입력칸')
      await typeInput(dateInput, item.publish_date_text)
      const timeInput = await waitFor(S.schedule_time_input, '예약 시각 입력칸')
      await typeInput(timeInput, item.publish_time_text)
      await sleep(800)
      const dialogText = (q(S.upload_dialog)?.innerText || '')
      if ((S.past_time_error_texts || []).some(t => dialogText.includes(t)) && /오류|error|invalid|잘못/i.test(dialogText)) {
        // 스튜디오가 과거 시각을 거부 → 즉시 게시로 전환
        switched = true
        await click(S.visibility[item.privacy] || S.visibility.public, '공개 범위')
      }
    } else {
      await click(S.visibility[item.privacy] || S.visibility.public, '공개 범위')
    }

    // 업로드 진행 대기 (최대 30분)
    const t0 = Date.now()
    let videoId = null
    while (Date.now() - t0 < 30 * 60000) {
      checkDialogs()
      videoId = videoId || readVideoId()
      const p = readProgress()
      if (p.pct != null) progress(Math.min(95, 10 + p.pct * 0.85), p.text)
      else if (p.text) progress(null, p.text)
      if ((S.upload_complete_texts || []).some(t => p.text.includes(t)) && p.pct == null) break
      if (p.pct === 100) break
      await sleep(1500)
    }
    videoId = videoId || readVideoId()
    await click(S.done_button, '완료(게시/예약) 버튼')
    // 게시 완료 대화상자
    const t1 = Date.now()
    while (Date.now() - t1 < STEP_TIMEOUT) {
      const shared = q(S.published_dialog)
      if (shared) {
        videoId = videoId || readVideoId()
        const close = q(S.published_close)
        if (close) close.click()
        break
      }
      checkDialogs()
      await sleep(500)
    }
    if (!videoId) throw new Abort('업로드는 끝났지만 영상 링크(video id)를 찾지 못했습니다')
    progress(100, '완료')
    banner(`#${item.id} 업로드 완료 (${videoId})`)
    return { video_id: videoId, switched_to_immediate: switched }
  }

  chrome.runtime.onConnect.addListener((p) => {
    if (p.name !== 'autoset-upload') return
    port = p
    let item = null
    let parts = []
    p.onMessage.addListener(async (m) => {
      if (m.type === 'begin') { item = m.item; S = m.selectors; parts = [] }
      if (m.type === 'chunk') {
        const bin = atob(m.data)
        const arr = new Uint8Array(bin.length)
        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i)
        parts.push(arr)
      }
      if (m.type === 'end') {
        const file = new File(parts, `autoset_${item.id}.mp4`, { type: 'video/mp4' })
        parts = []
        try {
          const r = await upload(item, file)
          p.postMessage({ type: 'result', ...r })
        } catch (e) {
          banner(e.message, true)
          p.postMessage({ type: 'result', error: e.message })
        }
      }
    })
  })
})()
