<template>
  <div class="market-review-page">
    <!-- Header -->
    <div class="page-header">
      <div class="header-left">
        <h2>大盘复盘</h2>
        <span class="subtitle">市场走势与板块分析</span>
      </div>
      <div class="header-actions">
        <input type="date" v-model="selectedDate" class="date-input" />
      </div>
    </div>

    <!-- Market Overview -->
    <div class="grid grid-4">
      <div class="stat-card" v-for="idx in marketIndices" :key="idx.name">
        <div class="idx-name">{{ idx.name }}</div>
        <div class="idx-value">{{ formatNumber(idx.value, 2) }}</div>
        <div :class="['idx-change', getChangeClass(idx.change)]">
          {{ formatPercent(idx.change) }}
        </div>
      </div>
    </div>

    <!-- Charts Row -->
    <div class="grid grid-2">
      <!-- Index Trend Chart -->
      <div class="card">
        <div class="card-title">指数走势 (近30日)</div>
        <div ref="indexChartRef" class="chart-container"></div>
      </div>

      <!-- Volume Chart -->
      <div class="card">
        <div class="card-title">成交额走势 (近30日)</div>
        <div ref="volumeChartRef" class="chart-container"></div>
      </div>
    </div>

    <!-- Sector Heatmap -->
    <div class="card">
      <div class="card-title">板块涨跌热力图</div>
      <div class="sector-heatmap">
        <div
          v-for="sector in sectorHeatmap"
          :key="sector.name"
          class="sector-cell"
          :style="getSectorStyle(sector.value)"
        >
          <div class="sector-name">{{ sector.name }}</div>
          <div class="sector-value">{{ formatPercent(sector.value) }}</div>
        </div>
      </div>
    </div>

    <!-- Sector Rankings -->
    <div class="grid grid-2">
      <!-- Top Sectors -->
      <div class="card">
        <div class="card-title tag-danger-text">领涨板块</div>
        <table class="table">
          <thead>
            <tr>
              <th>排名</th>
              <th>板块</th>
              <th>涨跌幅</th>
              <th>领涨股</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(sector, i) in topSectors" :key="sector.sector_name">
              <td>{{ i + 1 }}</td>
              <td>{{ sector.sector_name }}</td>
              <td class="num-up">{{ formatPercent(sector.change_pct) }}</td>
              <td>{{ sector.leader }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Bottom Sectors -->
      <div class="card">
        <div class="card-title tag-success-text">领跌板块</div>
        <table class="table">
          <thead>
            <tr>
              <th>排名</th>
              <th>板块</th>
              <th>涨跌幅</th>
              <th>领涨股</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(sector, i) in bottomSectors" :key="sector.sector_name">
              <td>{{ i + 1 }}</td>
              <td>{{ sector.sector_name }}</td>
              <td class="num-down">{{ formatPercent(sector.change_pct) }}</td>
              <td>{{ sector.leader }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Market Stats -->
    <div class="card">
      <div class="card-title">市场统计</div>
      <div class="market-stats">
        <div class="stat-item">
          <span class="label">上涨家数</span>
          <span class="value num-up">{{ marketData.up_count || '--' }}</span>
        </div>
        <div class="stat-item">
          <span class="label">下跌家数</span>
          <span class="value num-down">{{ marketData.down_count || '--' }}</span>
        </div>
        <div class="stat-item">
          <span class="label">涨停</span>
          <span class="value num-up">{{ marketData.limit_up_count || '--' }}</span>
        </div>
        <div class="stat-item">
          <span class="label">跌停</span>
          <span class="value num-down">{{ marketData.limit_down_count || '--' }}</span>
        </div>
        <div class="stat-item">
          <span class="label">两市成交额</span>
          <span class="value">{{ formatAmount(marketData.total_amount) }}</span>
        </div>
        <div class="stat-item">
          <span class="label">北向资金</span>
          <span :class="['value', getChangeClass(marketData.north_flow)]">
            {{ marketData.north_flow ? formatAmount(marketData.north_flow) : '--' }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, onUnmounted } from 'vue'
import dayjs from 'dayjs'
import * as echarts from 'echarts'
import { marketApi, sectorApi } from '../api'
import {
  formatNumber,
  formatPercent,
  formatAmount,
  getChangeClass
} from '../utils/format'

// State
const selectedDate = ref(dayjs().format('YYYY-MM-DD'))
const marketData = ref({})
const historyData = ref({ chart: {} })
const sectorData = ref({ top: [], bottom: [] })
const sectorHeatmap = ref([])

// Chart refs
const indexChartRef = ref(null)
const volumeChartRef = ref(null)
let indexChart = null
let volumeChart = null

// Computed
const marketIndices = computed(() => [
  { name: '上证指数', value: marketData.value.sh_index, change: marketData.value.sh_change_pct },
  { name: '深证成指', value: marketData.value.sz_index, change: marketData.value.sz_change_pct },
  { name: '创业板指', value: marketData.value.cyb_index, change: marketData.value.cyb_change_pct },
  { name: '科创50', value: marketData.value.kc50_index, change: marketData.value.kc50_change_pct }
])

const topSectors = computed(() => sectorData.value.top?.slice(0, 5) || [])
const bottomSectors = computed(() => sectorData.value.bottom?.slice(0, 5) || [])

// Methods
function getSectorStyle(value) {
  if (!value) return { background: '#f1f5f9' }

  const absValue = Math.abs(value)
  const intensity = Math.min(absValue / 5, 1) // Max at 5%

  if (value > 0) {
    return {
      background: `rgba(239, 68, 68, ${0.1 + intensity * 0.4})`,
      color: value > 2 ? 'white' : '#991b1b'
    }
  } else {
    return {
      background: `rgba(34, 197, 94, ${0.1 + intensity * 0.4})`,
      color: value < -2 ? 'white' : '#166534'
    }
  }
}

function initCharts() {
  if (indexChartRef.value) {
    indexChart = echarts.init(indexChartRef.value)
  }
  if (volumeChartRef.value) {
    volumeChart = echarts.init(volumeChartRef.value)
  }
}

function updateCharts() {
  const chart = historyData.value.chart || {}

  if (indexChart && chart.dates) {
    indexChart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['上证指数', '深证成指', '创业板指'] },
      grid: { left: 60, right: 20, top: 40, bottom: 30 },
      xAxis: {
        type: 'category',
        data: chart.dates,
        axisLabel: { formatter: v => v.slice(5) }
      },
      yAxis: { type: 'value', scale: true },
      series: [
        { name: '上证指数', type: 'line', data: chart.sh_index, smooth: true },
        { name: '深证成指', type: 'line', data: chart.sz_index, smooth: true },
        { name: '创业板指', type: 'line', data: chart.cyb_index, smooth: true }
      ]
    })
  }

  if (volumeChart && chart.dates) {
    volumeChart.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 60, right: 20, top: 40, bottom: 30 },
      xAxis: {
        type: 'category',
        data: chart.dates,
        axisLabel: { formatter: v => v.slice(5) }
      },
      yAxis: {
        type: 'value',
        axisLabel: { formatter: v => `${(v / 10000).toFixed(0)}万亿` }
      },
      series: [
        {
          name: '成交额',
          type: 'bar',
          data: chart.total_amount,
          itemStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: '#3b82f6' },
              { offset: 1, color: '#93c5fd' }
            ])
          }
        }
      ]
    })
  }
}

async function fetchData() {
  try {
    const [todayResult, historyResult, sectorsResult, heatmapResult] = await Promise.all([
      marketApi.getToday(selectedDate.value).catch(() => ({})),
      marketApi.getHistory(30),
      sectorApi.getToday(selectedDate.value),
      sectorApi.getHeatmap(selectedDate.value)
    ])

    marketData.value = todayResult
    historyData.value = historyResult
    sectorData.value = sectorsResult
    sectorHeatmap.value = heatmapResult.items || []

    updateCharts()
  } catch (error) {
    console.error('Failed to fetch market data:', error)
  }
}

// Watch
watch(selectedDate, () => fetchData())

// Lifecycle
onMounted(() => {
  initCharts()
  fetchData()

  window.addEventListener('resize', () => {
    indexChart?.resize()
    volumeChart?.resize()
  })
})

onUnmounted(() => {
  indexChart?.dispose()
  volumeChart?.dispose()
})
</script>

<style scoped>
.market-review-page {
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

.header-actions {
  display: flex;
  gap: 12px;
}

.date-input {
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

/* Index Cards */
.stat-card {
  background: var(--card);
  padding: 16px 20px;
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}

.idx-name {
  font-size: 0.875rem;
  color: var(--text-light);
}

.idx-value {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 4px 0;
}

.idx-change {
  font-size: 0.875rem;
  font-weight: 600;
}

/* Chart */
.chart-container {
  height: 280px;
}

/* Heatmap */
.sector-heatmap {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
}

.sector-cell {
  padding: 12px 8px;
  border-radius: var(--radius);
  text-align: center;
  transition: transform 0.2s;
}

.sector-cell:hover {
  transform: scale(1.05);
}

.sector-name {
  font-size: 0.75rem;
  margin-bottom: 4px;
}

.sector-value {
  font-size: 0.875rem;
  font-weight: 600;
}

/* Market Stats */
.market-stats {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 16px;
}

.stat-item {
  text-align: center;
}

.stat-item .label {
  display: block;
  font-size: 0.75rem;
  color: var(--text-light);
  margin-bottom: 4px;
}

.stat-item .value {
  font-size: 1.25rem;
  font-weight: 700;
}

.tag-danger-text {
  color: var(--danger);
}

.tag-success-text {
  color: var(--success);
}
</style>
