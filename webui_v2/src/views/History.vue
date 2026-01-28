<template>
  <div class="history-page">
    <!-- Header -->
    <div class="page-header">
      <div class="header-left">
        <h2>历史记录</h2>
        <span class="subtitle">查看历史选股与分析记录</span>
      </div>
      <div class="header-actions">
        <select v-model="daysRange" class="select-input">
          <option value="7">近7天</option>
          <option value="14">近14天</option>
          <option value="30">近30天</option>
        </select>
      </div>
    </div>

    <!-- Date List -->
    <div class="card">
      <div class="card-title">可用日期</div>
      <div class="date-pills">
        <button
          v-for="d in availableDates"
          :key="d"
          :class="['date-pill', { active: selectedDate === d }]"
          @click="selectDate(d)"
        >
          {{ formatDateLabel(d) }}
        </button>
      </div>
    </div>

    <!-- Selected Date Detail -->
    <div v-if="selectedDate" class="grid grid-2">
      <!-- Stock Pool History -->
      <div class="card">
        <div class="card-title">
          选股记录
          <span class="date-badge">{{ selectedDate }}</span>
        </div>

        <div v-if="loading" class="loading">加载中...</div>

        <div v-else-if="poolHistory.length === 0" class="empty">
          当日无选股记录
        </div>

        <div v-else class="history-list">
          <div v-for="stock in poolHistory" :key="stock.stock_code" class="history-item">
            <div class="item-left">
              <span class="code">{{ stock.stock_code }}</span>
              <span class="name">{{ stock.stock_name || '--' }}</span>
            </div>
            <div class="item-center">
              <span class="tag tag-info">{{ formatStrategy(stock.strategy) }}</span>
              <span class="sector">{{ stock.sector_name }}</span>
            </div>
            <div class="item-right">
              <span class="score">{{ formatNumber(stock.total_score, 0) }}分</span>
              <span :class="['tag', getPoolStatusLabel(stock.status).class]">
                {{ getPoolStatusLabel(stock.status).text }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- Market History -->
      <div class="card">
        <div class="card-title">
          大盘数据
          <span class="date-badge">{{ selectedDate }}</span>
        </div>

        <div v-if="!marketHistory" class="empty">
          当日无市场数据
        </div>

        <div v-else class="market-detail">
          <div class="detail-grid">
            <div class="detail-item">
              <div class="label">上证指数</div>
              <div class="value">{{ formatNumber(marketHistory.sh_index) }}</div>
              <div :class="['change', getChangeClass(marketHistory.sh_change_pct)]">
                {{ formatPercent(marketHistory.sh_change_pct) }}
              </div>
            </div>
            <div class="detail-item">
              <div class="label">深证成指</div>
              <div class="value">{{ formatNumber(marketHistory.sz_index) }}</div>
              <div :class="['change', getChangeClass(marketHistory.sz_change_pct)]">
                {{ formatPercent(marketHistory.sz_change_pct) }}
              </div>
            </div>
            <div class="detail-item">
              <div class="label">创业板指</div>
              <div class="value">{{ formatNumber(marketHistory.cyb_index) }}</div>
              <div :class="['change', getChangeClass(marketHistory.cyb_change_pct)]">
                {{ formatPercent(marketHistory.cyb_change_pct) }}
              </div>
            </div>
            <div class="detail-item">
              <div class="label">成交额</div>
              <div class="value">{{ formatAmount(marketHistory.total_amount) }}</div>
            </div>
            <div class="detail-item">
              <div class="label">涨跌比</div>
              <div class="value">
                <span class="num-up">{{ marketHistory.up_count }}</span>
                :
                <span class="num-down">{{ marketHistory.down_count }}</span>
              </div>
            </div>
            <div class="detail-item">
              <div class="label">北向资金</div>
              <div :class="['value', getChangeClass(marketHistory.north_flow)]">
                {{ marketHistory.north_flow ? formatAmount(marketHistory.north_flow) : '--' }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- History Timeline -->
    <div class="card">
      <div class="card-title">选股历史概览</div>

      <div class="timeline">
        <div v-for="record in historyRecords" :key="record.date" class="timeline-item">
          <div class="timeline-date">
            {{ formatDateLabel(record.date) }}
          </div>
          <div class="timeline-content">
            <div class="timeline-stats">
              <span>候选 <strong>{{ record.count }}</strong> 只</span>
              <span>已选 <strong class="num-up">{{ record.selected_count }}</strong> 只</span>
            </div>
            <div class="timeline-stocks">
              <span v-for="stock in record.items" :key="stock.stock_code" class="stock-chip">
                {{ stock.stock_code }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import dayjs from 'dayjs'
import { historyApi, stockPoolApi, marketApi } from '../api'
import {
  formatNumber,
  formatPercent,
  formatAmount,
  getChangeClass,
  getPoolStatusLabel,
  formatStrategy
} from '../utils/format'

// State
const daysRange = ref('7')
const availableDates = ref([])
const selectedDate = ref('')
const loading = ref(false)
const poolHistory = ref([])
const marketHistory = ref(null)
const historyRecords = ref([])

// Methods
function formatDateLabel(dateStr) {
  const d = dayjs(dateStr)
  const today = dayjs()

  if (d.isSame(today, 'day')) return '今天'
  if (d.isSame(today.subtract(1, 'day'), 'day')) return '昨天'

  return d.format('MM-DD')
}

async function fetchDates() {
  try {
    const result = await historyApi.getAvailableDates(parseInt(daysRange.value))
    availableDates.value = result.dates || []

    if (availableDates.value.length > 0 && !selectedDate.value) {
      selectedDate.value = availableDates.value[0]
    }
  } catch (error) {
    console.error('Failed to fetch dates:', error)
  }
}

async function fetchHistory() {
  try {
    const result = await historyApi.getStockPoolHistory(parseInt(daysRange.value))
    historyRecords.value = result.items || []
  } catch (error) {
    console.error('Failed to fetch history:', error)
  }
}

async function fetchDateDetail(date) {
  loading.value = true
  try {
    const [poolResult, marketResult] = await Promise.all([
      stockPoolApi.getList(date, 20),
      marketApi.getToday(date).catch(() => null)
    ])

    poolHistory.value = poolResult.items || []
    marketHistory.value = marketResult
  } catch (error) {
    console.error('Failed to fetch date detail:', error)
    poolHistory.value = []
    marketHistory.value = null
  } finally {
    loading.value = false
  }
}

function selectDate(date) {
  selectedDate.value = date
  fetchDateDetail(date)
}

// Watch
watch(daysRange, () => {
  fetchDates()
  fetchHistory()
})

watch(selectedDate, (newDate) => {
  if (newDate) {
    fetchDateDetail(newDate)
  }
})

// Init
onMounted(() => {
  fetchDates()
  fetchHistory()
})
</script>

<style scoped>
.history-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.page-header h2 {
  font-size: 1.5rem;
  font-weight: 700;
}

.subtitle {
  color: var(--text-light);
  font-size: 0.875rem;
  margin-left: 12px;
}

.select-input {
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: white;
}

/* Date Pills */
.date-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.date-pill {
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: white;
  cursor: pointer;
  transition: all 0.2s;
  font-size: 0.875rem;
}

.date-pill:hover {
  background: var(--bg);
}

.date-pill.active {
  background: var(--primary);
  color: white;
  border-color: var(--primary);
}

.date-badge {
  font-size: 0.75rem;
  color: var(--text-light);
  font-weight: normal;
  margin-left: 8px;
}

/* History List */
.history-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.history-item {
  display: flex;
  align-items: center;
  padding: 12px;
  background: var(--bg);
  border-radius: var(--radius);
}

.item-left {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 140px;
}

.item-left .code {
  font-family: monospace;
  font-weight: 600;
  color: var(--primary);
}

.item-left .name {
  font-size: 0.875rem;
}

.item-center {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
}

.item-center .sector {
  font-size: 0.75rem;
  color: var(--text-light);
}

.item-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.item-right .score {
  font-weight: 600;
  color: var(--primary);
}

/* Market Detail */
.detail-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

.detail-item {
  text-align: center;
  padding: 12px;
  background: var(--bg);
  border-radius: var(--radius);
}

.detail-item .label {
  font-size: 0.75rem;
  color: var(--text-light);
  margin-bottom: 4px;
}

.detail-item .value {
  font-size: 1.25rem;
  font-weight: 600;
}

.detail-item .change {
  font-size: 0.875rem;
}

/* Timeline */
.timeline {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.timeline-item {
  display: flex;
  gap: 16px;
  padding: 16px;
  background: var(--bg);
  border-radius: var(--radius);
}

.timeline-date {
  min-width: 60px;
  font-weight: 600;
  color: var(--primary);
}

.timeline-content {
  flex: 1;
}

.timeline-stats {
  display: flex;
  gap: 16px;
  margin-bottom: 8px;
  font-size: 0.875rem;
}

.timeline-stats strong {
  font-weight: 700;
}

.timeline-stocks {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.stock-chip {
  font-family: monospace;
  font-size: 0.75rem;
  padding: 2px 8px;
  background: white;
  border-radius: 4px;
  color: var(--text-light);
}
</style>
