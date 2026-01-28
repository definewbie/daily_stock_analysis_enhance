<template>
  <div class="app-container">
    <!-- 顶部导航 -->
    <header class="app-header">
      <div class="header-left">
        <h1 class="logo">A股智能分析系统</h1>
      </div>
      <nav class="nav-tabs">
        <router-link to="/stock-pool" class="nav-tab" active-class="active">
          <span class="icon">📊</span> 选股池
        </router-link>
        <router-link to="/market-review" class="nav-tab" active-class="active">
          <span class="icon">📈</span> 大盘复盘
        </router-link>
        <router-link to="/dashboard" class="nav-tab" active-class="active">
          <span class="icon">🎯</span> 决策仪表盘
        </router-link>
        <router-link to="/history" class="nav-tab" active-class="active">
          <span class="icon">📅</span> 历史记录
        </router-link>
      </nav>
      <div class="header-right">
        <span class="date-display">{{ currentDate }}</span>
      </div>
    </header>

    <!-- 主内容区 -->
    <main class="app-main">
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import dayjs from 'dayjs'

const currentDate = computed(() => dayjs().format('YYYY-MM-DD'))
</script>

<style>
/* CSS Reset */
*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

:root {
  --primary: #2563eb;
  --primary-hover: #1d4ed8;
  --success: #10b981;
  --warning: #f59e0b;
  --danger: #ef4444;
  --bg: #f8fafc;
  --card: #ffffff;
  --text: #1e293b;
  --text-light: #64748b;
  --border: #e2e8f0;
  --shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  --radius: 8px;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background-color: var(--bg);
  color: var(--text);
  line-height: 1.5;
}

.app-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* Header */
.app-header {
  background: var(--card);
  border-bottom: 1px solid var(--border);
  padding: 0 24px;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-shadow: var(--shadow);
  position: sticky;
  top: 0;
  z-index: 100;
}

.logo {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--primary);
}

.nav-tabs {
  display: flex;
  gap: 4px;
}

.nav-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: var(--radius);
  text-decoration: none;
  color: var(--text-light);
  font-weight: 500;
  transition: all 0.2s;
}

.nav-tab:hover {
  background: var(--bg);
  color: var(--text);
}

.nav-tab.active {
  background: var(--primary);
  color: white;
}

.nav-tab .icon {
  font-size: 1rem;
}

.date-display {
  color: var(--text-light);
  font-size: 0.875rem;
}

/* Main */
.app-main {
  flex: 1;
  padding: 24px;
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
}

/* Transitions */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* Utility Classes */
.card {
  background: var(--card);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 20px;
}

.card-title {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: var(--radius);
  font-weight: 500;
  cursor: pointer;
  border: none;
  transition: all 0.2s;
}

.btn-primary {
  background: var(--primary);
  color: white;
}

.btn-primary:hover {
  background: var(--primary-hover);
}

.btn-success {
  background: var(--success);
  color: white;
}

.btn-outline {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text);
}

.btn-outline:hover {
  background: var(--bg);
}

/* Table */
.table {
  width: 100%;
  border-collapse: collapse;
}

.table th,
.table td {
  padding: 12px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}

.table th {
  font-weight: 600;
  color: var(--text-light);
  font-size: 0.875rem;
}

.table tr:hover {
  background: var(--bg);
}

/* Tags */
.tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.75rem;
  font-weight: 500;
}

.tag-success {
  background: #dcfce7;
  color: #166534;
}

.tag-warning {
  background: #fef3c7;
  color: #92400e;
}

.tag-danger {
  background: #fee2e2;
  color: #991b1b;
}

.tag-info {
  background: #dbeafe;
  color: #1e40af;
}

/* Number colors */
.num-up {
  color: var(--danger);
}

.num-down {
  color: var(--success);
}

.num-flat {
  color: var(--text-light);
}

/* Grid */
.grid {
  display: grid;
  gap: 20px;
}

.grid-2 {
  grid-template-columns: repeat(2, 1fr);
}

.grid-3 {
  grid-template-columns: repeat(3, 1fr);
}

.grid-4 {
  grid-template-columns: repeat(4, 1fr);
}

/* Loading */
.loading {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 40px;
  color: var(--text-light);
}

/* Empty */
.empty {
  text-align: center;
  padding: 40px;
  color: var(--text-light);
}
</style>
