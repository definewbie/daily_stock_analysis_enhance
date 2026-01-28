<template>
  <div class="stock-pool-page">
    <!-- Header -->
    <div class="page-header">
      <div class="header-left">
        <h2>选股池</h2>
        <span class="subtitle">基于多策略智能选股结果</span>
      </div>
      <div class="header-actions">
        <input type="date" v-model="selectedDate" class="date-input" />
        <button class="btn btn-primary" @click="runSelection" :disabled="loading">
          {{ loading ? '选股中...' : '执行选股' }}
        </button>
      </div>
    </div>

    <!-- Stats -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-value">{{ poolData.count || 0 }}</div>
        <div class="stat-label">候选股数</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ selectedCount }}</div>
        <div class="stat-label">已选中</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ marketState.label }}</div>
        <div class="stat-label">市场状态</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ marketState.threshold }}亿</div>
        <div class="stat-label">市值阈值</div>
      </div>
    </div>

    <!-- Stock List -->
    <div class="card">
      <div class="card-title">
        <span>候选股列表</span>
        <span class="text-light">按综合评分排序</span>
      </div>

      <div v-if="loading" class="loading">加载中...</div>

      <table v-else-if="stocks.length > 0" class="table">
        <thead>
          <tr>
            <th width="50">选择</th>
            <th>代码</th>
            <th>名称</th>
            <th>策略</th>
            <th>板块</th>
            <th>综合评分</th>
            <th>宏观分</th>
            <th>技术分</th>
            <th>涨跌幅</th>
            <th>流通市值</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="stock in stocks" :key="stock.stock_code">
            <td>
              <input
                type="checkbox"
                :checked="stock.status === 'selected'"
                @change="toggleStock(stock)"
              />
            </td>
            <td class="code">{{ stock.stock_code }}</td>
            <td>{{ stock.stock_name || '--' }}</td>
            <td>
              <span class="tag tag-info">{{ formatStrategy(stock.strategy) }}</span>
            </td>
            <td>{{ stock.sector_name || '--' }}</td>
            <td class="score">{{ formatNumber(stock.total_score) }}</td>
            <td>{{ formatNumber(stock.macro_score) }}</td>
            <td>{{ formatNumber(stock.tech_score) }}</td>
            <td :class="getChangeClass(stock.stock_change_pct)">
              {{ formatPercent(stock.stock_change_pct) }}
            </td>
            <td>{{ formatAmount(stock.circ_mv) }}</td>
            <td>
              <span :class="['tag', getPoolStatusLabel(stock.status).class]">
                {{ getPoolStatusLabel(stock.status).text }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-else class="empty">暂无选股数据，请先执行选股</div>
    </div>

    <!-- Selected Stocks -->
    <div v-if="selectedStocks.length > 0" class="card selected-stocks">
      <div class="card-title">
        <span>已选中股票</span>
        <button class="btn btn-outline" @click="goToDashboard">
          查看决策仪表盘 →
        </button>
      </div>
      <div class="selected-list">
        <div v-for="stock in selectedStocks" :key="stock.stock_code" class="selected-item">
          <span class="code">{{ stock.stock_code }}</span>
          <span class="name">{{ stock.stock_name }}</span>
          <span class="score">{{ formatNumber(stock.total_score) }}分</span>
          <button class="btn-remove" @click="removeStock(stock)">×</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { stockPoolApi, marketApi } from '../api'
import {
  formatNumber,
  formatPercent,
  formatAmount,
  getChangeClass,
  getPoolStatusLabel,
  formatStrategy
} from '../utils/format'

const router = useRouter()

// State
const selectedDate = ref(dayjs().format('YYYY-MM-DD'))
const loading = ref(false)
const poolData = ref({})
const stocks = ref([])
const marketState = ref({ state: 'neutral', label: '震荡', threshold: 50 })

// Computed
const selectedStocks = computed(() =>
  stocks.value.filter(s => s.status === 'selected')
)

const selectedCount = computed(() => selectedStocks.value.length)

// Methods
async function fetchData() {
  loading.value = true
  try {
    const [poolResult, stateResult] = await Promise.all([
      stockPoolApi.getList(selectedDate.value, 20),
      marketApi.getState()
    ])

    poolData.value = poolResult
    stocks.value = poolResult.items || []
    marketState.value = {
      state: stateResult.state,
      label: stateResult.state_label,
      threshold: stateResult.mv_threshold
    }
  } catch (error) {
    console.error('Failed to fetch data:', error)
  } finally {
    loading.value = false
  }
}

async function runSelection() {
  loading.value = true
  try {
    const result = await stockPoolApi.runSelection(selectedDate.value)
    stocks.value = result.items || []
    poolData.value = result
  } catch (error) {
    console.error('Selection failed:', error)
    alert('选股执行失败，请检查数据源')
  } finally {
    loading.value = false
  }
}

async function toggleStock(stock) {
  const newStatus = stock.status === 'selected' ? 'pending' : 'selected'
  try {
    await stockPoolApi.updateStatus(stock.stock_code, newStatus, selectedDate.value)
    stock.status = newStatus
  } catch (error) {
    console.error('Update failed:', error)
  }
}

async function removeStock(stock) {
  await toggleStock(stock)
}

function goToDashboard() {
  router.push('/dashboard')
}

// Watch date changes
watch(selectedDate, () => {
  fetchData()
})

// Init
onMounted(() => {
  fetchData()
})
</script>

<style scoped>
.stock-pool-page {
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
  align-items: center;
}

.date-input {
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 0.875rem;
}

/* Stats */
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}

.stat-card {
  background: var(--card);
  padding: 16px 20px;
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  text-align: center;
}

.stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--primary);
}

.stat-label {
  font-size: 0.75rem;
  color: var(--text-light);
  margin-top: 4px;
}

/* Table */
.code {
  font-family: monospace;
  font-weight: 600;
}

.score {
  font-weight: 700;
  color: var(--primary);
}

.text-light {
  color: var(--text-light);
  font-size: 0.875rem;
  font-weight: normal;
  margin-left: auto;
}

/* Selected Stocks */
.selected-stocks {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
}

.selected-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.selected-item {
  display: flex;
  align-items: center;
  gap: 8px;
  background: white;
  padding: 8px 12px;
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}

.selected-item .code {
  color: var(--primary);
}

.selected-item .name {
  color: var(--text);
}

.selected-item .score {
  font-size: 0.75rem;
  color: var(--text-light);
}

.btn-remove {
  background: none;
  border: none;
  color: var(--danger);
  cursor: pointer;
  font-size: 1.25rem;
  line-height: 1;
  padding: 0 4px;
}

.btn-remove:hover {
  color: #b91c1c;
}
</style>
