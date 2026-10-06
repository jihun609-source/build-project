import assert from 'node:assert/strict'
import { test } from 'node:test'
import { installRouteRecovery } from '../src/lib/routeRecovery.js'

function setup(href = 'http://localhost:8010/ui/?keep=1#/upload') {
  const handlers = {}
  const replacements = []
  const url = new URL(href)
  const browser = {
    location: { href, hash: url.hash, replace: (next) => replacements.push(next) },
    addEventListener: (event, callback) => { handlers[event] = callback },
  }
  const router = {
    beforeEach: (callback) => { handlers.beforeEach = callback },
    onError: (callback) => { handlers.onError = callback },
  }
  installRouteRecovery(router, browser)
  return { handlers, replacements }
}

test('failed menu import refreshes the requested menu and preserves query parameters', () => {
  const { handlers, replacements } = setup()
  handlers.onError(new TypeError('Failed to fetch dynamically imported module'), { fullPath: '/feed?q=cat' })
  const url = new URL(replacements[0])
  assert.equal(url.hash, '#/feed?q=cat')
  assert.equal(url.searchParams.get('keep'), '1')
  assert.ok(Number(url.searchParams.get('_ui_reload')))
})

test('Vite preload failure uses the menu being opened and only reloads once', () => {
  const { handlers, replacements } = setup()
  handlers.beforeEach({ fullPath: '/edit' })
  let prevented = false
  handlers['vite:preloadError']({ payload: new Error('Unable to preload CSS'), preventDefault: () => { prevented = true } })
  handlers.onError(new TypeError('Importing a module script failed'), { fullPath: '/edit' })
  assert.equal(new URL(replacements[0]).hash, '#/edit')
  assert.equal(replacements.length, 1)
  assert.ok(prevented)
})

test('a failed retry does not create a refresh loop', () => {
  const { handlers, replacements } = setup(`http://localhost:8010/ui/?_ui_reload=${Date.now()}#/system`)
  let prevented = false
  handlers['vite:preloadError']({ payload: new Error('Failed to fetch dynamically imported module'), preventDefault: () => { prevented = true } })
  assert.equal(replacements.length, 0)
  assert.equal(prevented, false)
})

test('other errors are not treated as a stale menu file', () => {
  const { handlers, replacements } = setup()
  handlers['vite:preloadError']({ payload: new SyntaxError('Unexpected token'), preventDefault: () => assert.fail('error hidden') })
  assert.equal(replacements.length, 0)
})
