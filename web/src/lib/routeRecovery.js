// 이전 빌드의 메뉴 파일을 요청한 경우 최신 HTML을 받아 같은 메뉴로 이동한다.
export function installRouteRecovery(router, browser = window, notify = () => {}) {
  let target = browser.location.hash.slice(1) || '/dashboard'
  let reloading = false
  router.beforeEach((to) => { target = to.fullPath })

  function recover(error, path = target) {
    if (!/Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i.test(error?.message || String(error))) return false
    if (reloading) return true
    const url = new URL(browser.location.href)
    const lastReload = Number(url.searchParams.get('_ui_reload'))
    if (lastReload && Date.now() - lastReload < 60000) return false
    url.searchParams.set('_ui_reload', String(Date.now()))
    url.hash = path
    reloading = true
    browser.location.replace(url.href)
    return true
  }

  browser.addEventListener('vite:preloadError', (event) => {
    if (recover(event.payload)) event.preventDefault()
  })
  router.onError((error, to) => {
    if (!recover(error, to?.fullPath || target)) {
      console.error('메뉴 로딩 실패', error)
      notify('화면을 불러오지 못했습니다. 연결을 확인하고 새로고침하세요.')
    }
  })
}
