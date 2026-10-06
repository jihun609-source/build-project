// 관리 UI 페이지에 들어가는 브리지: UI의 "다음 항목 업로드" 버튼 요청을 확장프로그램에 전달
window.addEventListener('message', (ev) => {
  if (ev.source !== window || ev.data?.source !== 'autoset-ui' || ev.data.type !== 'upload-next') return
  if (!chrome.runtime?.id) {
    window.postMessage({ source: 'autoset-extension', type: 'error', message: '확장프로그램이 새로고침되었습니다. 이 페이지를 새로고침하세요.' }, location.origin)
    return
  }
  chrome.runtime.sendMessage({ type: 'upload-next' }, (r) => {
    const reply = r?.ok
      ? { source: 'autoset-extension', type: 'upload-started', item_id: r.item_id, title: r.title }
      : { source: 'autoset-extension', type: 'error', message: r?.error || chrome.runtime.lastError?.message || '확장프로그램 오류' }
    window.postMessage(reply, location.origin)
  })
})
window.postMessage({ source: 'autoset-extension', type: 'ready' }, location.origin)
