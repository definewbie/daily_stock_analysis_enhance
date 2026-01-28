// 格式化数字
export function formatNumber(num, decimals = 2) {
  if (num === null || num === undefined) return '--'
  return Number(num).toFixed(decimals)
}

// 格式化百分比
export function formatPercent(num) {
  if (num === null || num === undefined) return '--'
  const prefix = num > 0 ? '+' : ''
  return `${prefix}${formatNumber(num)}%`
}

// 格式化金额（亿）
export function formatAmount(num) {
  if (num === null || num === undefined) return '--'
  return `${formatNumber(num)}亿`
}

// 获取涨跌样式类
export function getChangeClass(num) {
  if (num === null || num === undefined) return 'num-flat'
  if (num > 0) return 'num-up'
  if (num < 0) return 'num-down'
  return 'num-flat'
}

// 获取市场状态标签
export function getMarketStateLabel(state) {
  const labels = {
    bull: { text: '牛市', class: 'tag-danger' },
    bear: { text: '熊市', class: 'tag-success' },
    neutral: { text: '震荡', class: 'tag-warning' }
  }
  return labels[state] || { text: '未知', class: 'tag-info' }
}

// 获取选股状态标签
export function getPoolStatusLabel(status) {
  const labels = {
    pending: { text: '待选', class: 'tag-info' },
    selected: { text: '已选', class: 'tag-success' },
    dismissed: { text: '排除', class: 'tag-warning' }
  }
  return labels[status] || { text: status, class: 'tag-info' }
}

// 格式化策略名称
export function formatStrategy(strategy) {
  const names = {
    policy: '政策利好',
    hot_sector: '热门板块',
    north_flow: '北向资金',
    reversal: '板块反转'
  }
  return names[strategy] || strategy
}
