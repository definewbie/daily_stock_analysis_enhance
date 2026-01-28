<template>
  <div class="dashboard-page">
    <!-- Header -->
    <div class="page-header">
      <div class="header-left">
        <h2>决策仪表盘</h2>
        <span class="subtitle">综合分析与投资建议</span>
      </div>
      <div class="header-actions">
        <input type="date" v-model="selectedDate" class="date-input" />
      </div>
    </div>

    <!-- Market State Banner -->
    <div :class="['market-banner', `state-${dashboardData.market_state?.state}`]">
      <div class="banner-content">
        <div class="state-icon">
          {{ dashboardData.market_state?.state === 'bull' ? '🐂' : dashboardData.market_state?.state === 'bear' ? '🐻' : '⚖️' }}
        </div>
        <div class="state-info">
          <div class="state-label">当前市场状态</div>
          <div class="state-value">{{ dashboardData.market_state?.label || '震荡' }}</div>
        </div>
        <div class="state-tips">
          <span v-if="dashboardData.market_state?.state === 'bull'">
            市场情绪积极，可适当提高仓位
          </span>
          <span v-else-if="dashboardData.market_state?.state === 'bear'">
            市场走弱，建议控制仓位，注意风险
          </span>
          <span v-else>
            市场震荡整理，关注结构性机会
          </span>
        </div>
      </div>
    </div>

    <!-- Overview Cards -->
    <div class="grid grid-3">
      <!-- Market Card -->
      <div class="card">
        <div class="card-title">市场概况</div>
        <div class="overview-list">
          <div class="overview-item">
            <span class="label">上证指数</span>
            <span class="value">{{ formatNumber(dashboardData.market?.sh_index) }}</span>
            <span :class="['change', getChangeClass(dashboardData.market?.sh_change_pct)]">
              {{ formatPercent(dashboardData.market?.sh_change_pct) }}
            </span>
          </div>
          <div class="overview-item">
            <span class="label">两市成交</span>
            <span class="value">{{ formatAmount(dashboardData.market?.total_amount) }}</span>
          </div>
          <div class="overview-item">
            <span class="label">涨跌比</span>
            <span class="value">
              <span class="num-up">{{ dashboardData.market?.up_count }}</span>
              :
              <span class="num-down">{{ dashboardData.market?.down_count }}</span>
            </span>
          </div>
          <div class="overview-item">
            <span class="label">北向资金</span>
            <span :class="['value', getChangeClass(dashboardData.market?.north_flow)]">
              {{ dashboardData.market?.north_flow ? formatAmount(dashboardData.market?.north_flow) : '--' }}
            </span>
          </div>
        </div>
      </div>

      <!-- Stock Pool Card -->
      <div class="card">
        <div class="card-title">选股池概况</div>
        <div class="pool-stats">
          <div class="pool-stat">
            <div class="stat-value">{{ dashboardData.stock_pool?.total || 0 }}</div>
            <div class="stat-label">候选股数</div>
          </div>
          <div class="pool-stat highlight">
            <div class="stat-value">{{ dashboardData.stock_pool?.selected || 0 }}</div>
            <div class="stat-label">已选中</div>
          </div>
        </div>
        <router-link to="/stock-pool" class="link-btn">
          查看选股池 →
        </router-link>
      </div>

      <!-- Sector Card -->
      <div class="card">
        <div class="card-title">板块动态</div>
        <div class="sector-mini">
          <div class="sector-group">
            <div class="group-title num-up">领涨</div>
            <div v-for="s in topSectors" :key="s.sector_name" class="sector-item">
              <span class="name">{{ s.sector_name }}</span>
              <span class="pct num-up">{{ formatPercent(s.change_pct) }}</span>
            </div>
          </div>
          <div class="sector-group">
            <div class="group-title num-down">领跌</div>
            <div v-for="s in bottomSectors" :key="s.sector_name" class="sector-item">
              <span class="name">{{ s.sector_name }}</span>
              <span class="pct num-down">{{ formatPercent(s.change_pct) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Selected Stocks Analysis -->
    <div class="card">
      <div class="card-title">
        <span>已选股票分析</span>
        <span class="text-light">点击股票查看详情</span>
      </div>

      <div v-if="selectedStocks.length === 0" class="empty">
        暂无已选股票，请先在选股池中选择
      </div>

      <div v-else class="selected-grid">
        <div
          v-for="stock in selectedStocks"
          :key="stock.stock_code"
          class="stock-card"
          @click="showStockDetail(stock)"
        >
          <div class="stock-header">
            <div class="stock-code">{{ stock.stock_code }}</div>
            <div class="stock-name">{{ stock.stock_name || '--' }}</div>
          </div>
          <div class="stock-body">
            <div class="stock-score">
              <div class="score-circle">{{ formatNumber(stock.total_score, 0) }}</div>
              <div class="score-label">综合评分</div>
            </div>
            <div class="stock-details">
              <div class="detail-row">
                <span>策略</span>
                <span class="tag tag-info">{{ formatStrategy(stock.strategy) }}</span>
              </div>
              <div class="detail-row">
                <span>板块</span>
                <span>{{ stock.sector_name || '--' }}</span>
              </div>
              <div class="detail-row">
                <span>涨跌幅</span>
                <span :class="getChangeClass(stock.stock_change_pct)">
                  {{ formatPercent(stock.stock_change_pct) }}
                </span>
              </div>
              <div class="detail-row">
                <span>流通市值</span>
                <span>{{ formatAmount(stock.circ_mv) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Top 3 Candidates -->
    <div class="card">
      <div class="card-title">Top 3 候选股</div>
      <div class="top3-list">
        <div v-for="(stock, i) in top3Stocks" :key="stock.stock_code" class="top3-item">
          <div class="rank">{{ i + 1 }}</div>
          <div class="info">
            <div class="name">
              <span class="code">{{ stock.stock_code }}</span>
              {{ stock.stock_name }}
            </div>
            <div class="meta">
              {{ formatStrategy(stock.strategy) }} | {{ stock.sector_name }}
            </div>
          </div>
          <div class="score">{{ formatNumber(stock.total_score, 0) }}分</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import dayjs from 'dayjs'
import { dashboardApi, stockPoolApi } from '../api'
import {
  formatNumber,
  formatPercent,
  formatAmount,
  getChangeClass,
  formatStrategy
} from '../utils/format'

// State
const selectedDate = ref(dayjs().format('YYYY-MM-DD'))
const dashboardData = ref({})
const selectedStocks = ref([])

// Computed
const topSectors = computed(() =>
  dashboardData.value.sectors?.top?.slice(0, 3) || []
)

const bottomSectors = computed(() =>
  dashboardData.value.sectors?.bottom?.slice(0, 3) || []
)

const top3Stocks = computed(() =>
  dashboardData.value.stock_pool?.top3 || []
)

// Methods
async function fetchData() {
  try {
    const [dashboard, selected] = await Promise.all([
      dashboardApi.get(selectedDate.value),
      stockPoolApi.getSelected(selectedDate.value)
    ])

    dashboardData.value = dashboard
    selectedStocks.value = selected.items || []
  } catch (error) {
    console.error('Failed to fetch dashboard:', error)
  }
}

function showStockDetail(stock) {
  alert(`查看 ${stock.stock_code} ${stock.stock_name} 详情\n\n此功能开发中...`)
}

// Watch
watch(selectedDate, () => fetchData())

// Init
onMounted(() => {
  fetchData()
})
</script>

<style scoped>
.dashboard-page {
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

.date-input {
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

/* Market Banner */
.market-banner {
  background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
  border-radius: var(--radius);
  padding: 24px;
  box-shadow: var(--shadow);
}

.market-banner.state-bull {
  background: linear-gradient(135deg, #fef2f2 0%, #fecaca 100%);
}

.market-banner.state-bear {
  background: linear-gradient(135deg, #f0fdf4 0%, #bbf7d0 100%);
}

.banner-content {
  display: flex;
  align-items: center;
  gap: 24px;
}

.state-icon {
  font-size: 3rem;
}

.state-info .state-label {
  font-size: 0.875rem;
  color: var(--text-light);
}

.state-info .state-value {
  font-size: 1.5rem;
  font-weight: 700;
}

.state-tips {
  margin-left: auto;
  color: var(--text-light);
  font-size: 0.875rem;
}

/* Overview */
.overview-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.overview-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.overview-item .label {
  color: var(--text-light);
  font-size: 0.875rem;
  min-width: 80px;
}

.overview-item .value {
  font-weight: 600;
}

.overview-item .change {
  font-size: 0.875rem;
}

/* Pool Stats */
.pool-stats {
  display: flex;
  gap: 24px;
  margin-bottom: 16px;
}

.pool-stat {
  text-align: center;
}

.pool-stat .stat-value {
  font-size: 2rem;
  font-weight: 700;
  color: var(--text-light);
}

.pool-stat.highlight .stat-value {
  color: var(--primary);
}

.pool-stat .stat-label {
  font-size: 0.75rem;
  color: var(--text-light);
}

.link-btn {
  display: inline-block;
  color: var(--primary);
  text-decoration: none;
  font-size: 0.875rem;
}

.link-btn:hover {
  text-decoration: underline;
}

/* Sector Mini */
.sector-mini {
  display: flex;
  gap: 20px;
}

.sector-group {
  flex: 1;
}

.group-title {
  font-size: 0.75rem;
  font-weight: 600;
  margin-bottom: 8px;
}

.sector-item {
  display: flex;
  justify-content: space-between;
  font-size: 0.875rem;
  padding: 4px 0;
}

.sector-item .name {
  color: var(--text);
}

.sector-item .pct {
  font-weight: 600;
}

/* Selected Grid */
.selected-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.stock-card {
  background: var(--bg);
  border-radius: var(--radius);
  padding: 16px;
  cursor: pointer;
  transition: all 0.2s;
}

.stock-card:hover {
  box-shadow: var(--shadow);
  transform: translateY(-2px);
}

.stock-header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
}

.stock-code {
  font-family: monospace;
  font-weight: 700;
  color: var(--primary);
}

.stock-name {
  font-weight: 500;
}

.stock-body {
  display: flex;
  gap: 16px;
}

.stock-score {
  text-align: center;
}

.score-circle {
  width: 60px;
  height: 60px;
  border-radius: 50%;
  background: var(--primary);
  color: white;
  font-size: 1.5rem;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.score-label {
  font-size: 0.625rem;
  color: var(--text-light);
  margin-top: 4px;
}

.stock-details {
  flex: 1;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  padding: 2px 0;
  color: var(--text-light);
}

.detail-row span:last-child {
  color: var(--text);
}

/* Top 3 */
.top3-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.top3-item {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  background: var(--bg);
  border-radius: var(--radius);
}

.top3-item .rank {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--primary);
  color: white;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.top3-item .info {
  flex: 1;
}

.top3-item .name {
  font-weight: 500;
}

.top3-item .name .code {
  font-family: monospace;
  color: var(--primary);
  margin-right: 8px;
}

.top3-item .meta {
  font-size: 0.75rem;
  color: var(--text-light);
}

.top3-item .score {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--primary);
}

.text-light {
  color: var(--text-light);
  font-size: 0.875rem;
  font-weight: normal;
  margin-left: auto;
}
</style>
