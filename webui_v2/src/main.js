import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'

// Views
import StockPool from './views/StockPool.vue'
import MarketReview from './views/MarketReview.vue'
import Dashboard from './views/Dashboard.vue'
import History from './views/History.vue'

// Router
const routes = [
  { path: '/', redirect: '/stock-pool' },
  { path: '/stock-pool', name: 'StockPool', component: StockPool },
  { path: '/market-review', name: 'MarketReview', component: MarketReview },
  { path: '/dashboard', name: 'Dashboard', component: Dashboard },
  { path: '/history', name: 'History', component: History },
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// Create app
const app = createApp(App)
app.use(router)
app.mount('#app')
