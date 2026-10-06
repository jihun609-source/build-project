// REST 호출 래퍼. 인증은 로그인 시 서버가 심는 httponly 쿠키로 처리한다.
import { reactive } from 'vue'

export const auth = reactive({ ok: null })

export class ApiError extends Error {
  constructor(status, detail, fields) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
    this.status = status
    this.fields = fields || {}
  }
}

async function request(method, path, body, opts = {}) {
  const init = { method, credentials: 'same-origin', headers: {} }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  const res = await fetch(path, init)
  if (res.status === 401 && !path.startsWith('/auth/login')) {
    auth.ok = false
    throw new ApiError(401, '로그인이 필요합니다')
  }
  if (res.status === 204) return null
  const ct = res.headers.get('content-type') || ''
  const data = ct.includes('application/json') ? await res.json() : await res.text()
  if (!res.ok) {
    let detail = data?.detail ?? data
    if (Array.isArray(detail)) detail = detail.map(d => `${(d.loc || []).join('.')}: ${d.msg}`).join(', ')
    throw new ApiError(res.status, detail, data?.fields)
  }
  if (opts.raw) return res
  return data
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b = {}) => request('POST', p, b),
  put: (p, b) => request('PUT', p, b),
  patch: (p, b) => request('PATCH', p, b),
  del: (p) => request('DELETE', p),
  upload: (p, file, field = 'file') => {
    const fd = new FormData()
    fd.append(field, file)
    return request('POST', p, fd)
  },
}

export function qs(params) {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '' && v !== false) s.set(k, v)
  const t = s.toString()
  return t ? `?${t}` : ''
}
