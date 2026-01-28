import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000
})

// Response interceptor
api.interceptors.response.use(
  response => response.data.data,
  error => {
    console.error('API Error:', error)
    return Promise.reject(error)
  }
)

// Stock Pool APIs
export const stockPoolApi = {
  getList: (date, limit = 10) =>
    api.get('/stock-pool', { params: { date, limit } }),

  getSelected: (date) =>
    api.get('/stock-pool/selected', { params: { date } }),

  updateStatus: (stockCode, status, date) =>
    api.put(`/stock-pool/${stockCode}/status`, { status, date }),

  runSelection: (date) =>
    api.post('/stock-pool/run', { date })
}

// Market APIs
export const marketApi = {
  getToday: (date) =>
    api.get('/market/today', { params: { date } }),

  getHistory: (days = 30) =>
    api.get('/market/history', { params: { days } }),

  getState: () =>
    api.get('/market/state')
}

// Sector APIs
export const sectorApi = {
  getToday: (date, type = 'all') =>
    api.get('/sectors/today', { params: { date, type } }),

  getHeatmap: (date) =>
    api.get('/sectors/heatmap', { params: { date } }),

  getHistory: (sectorName, days = 5) =>
    api.get(`/sectors/${encodeURIComponent(sectorName)}/history`, { params: { days } })
}

// Stock APIs
export const stockApi = {
  getInfo: (code) =>
    api.get(`/stock/${code}`),

  getAnalysis: (code) =>
    api.get(`/stock/${code}/analysis`)
}

// History APIs
export const historyApi = {
  getStockPoolHistory: (days = 7) =>
    api.get('/history/stock-pool', { params: { days } }),

  getAvailableDates: (days = 30) =>
    api.get('/history/dates', { params: { days } })
}

// Dashboard APIs
export const dashboardApi = {
  get: (date) =>
    api.get('/dashboard', { params: { date } })
}

// Report APIs
export const reportApi = {
  getMarketReview: (date) =>
    api.get('/report/market-review', { params: { date } })
}
