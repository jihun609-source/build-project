import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import './style.css'
import { installRouteRecovery } from './lib/routeRecovery'
import { toast } from './lib/store'

const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/dashboard', component: () => import('./views/Dashboard.vue'), meta: { title: '대시보드' } },
  { path: '/feed', component: () => import('./views/Feed.vue'), meta: { title: '피드' } },
  { path: '/pipeline/:id?', component: () => import('./views/Pipeline.vue'), meta: { title: '파이프라인' } },
  { path: '/captions', component: () => import('./views/Captions.vue'), meta: { title: '자막' } },
  { path: '/edit', component: () => import('./views/EditSettings.vue'), meta: { title: '편집 설정' } },
  { path: '/ai', component: () => import('./views/AiPrompts.vue'), meta: { title: 'AI·프롬프트' } },
  { path: '/upload', component: () => import('./views/UploadSettings.vue'), meta: { title: '업로드 설정' } },
  { path: '/system', component: () => import('./views/System.vue'), meta: { title: '시스템' } },
]

const router = createRouter({ history: createWebHashHistory(), routes })
installRouteRecovery(router, window, (message) => toast(message, 'error', 8000))
router.afterEach((to) => { document.title = `${to.meta.title || ''} · AutoSet` })

createApp(App).use(router).mount('#app')
