<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import Icon from './components/Icon.vue'
import { api, errorMessage } from './api'

const mascotUrl = '/images/minty-catgirl.png'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const markdown = value => DOMPurify.sanitize(md.render(value || ''))
const status = ref(null)
const connected = ref(false)
const initialLoading = ref(true)
const loadError = ref('')
const sessions = ref([])
const selectedId = ref(null)
const messages = ref([])
const events = ref([])
const run = ref(null)
const files = ref([])
const tasks = ref([])
const stats = ref({ sessions: 0, runs: 0, completedRuns: 0, totalTokens: 0, daily: [] })
const statsError = ref('')
const sideError = ref('')
const loadingSession = ref(false)
const sending = ref(false)
const stopping = ref(false)
const prompt = ref('')
const view = ref('workspace')
const sideTab = ref('tasks')
const settingsOpen = ref(false)
const sidebarOpen = ref(false)
const contextOpen = ref(false)
const fileOpen = ref(false)
const openedFile = ref({ path: '', content: '' })
const fileLoading = ref(false)
const uploading = ref(false)
const uploadInput = ref(null)
const composer = ref(null)
const chatScroller = ref(null)
const chartElement = ref(null)
const chartMetric = ref('runs')
const openedEvents = ref(new Set())
let pollTimer, chart, chartObserver, echarts
let selectionVersion = 0
let destroyed = false

const activeSession = computed(() => sessions.value.find(s => s.id === selectedId.value))
const active = computed(() => ['queued', 'running', 'pending', 'stopping'].includes(String(run.value?.status).toLowerCase()))
const liveText = computed(() => events.value.filter(e => ['text', 'assistant_delta', 'text_delta'].includes(e.type)).map(e => e.content).join(''))
const toolEvents = computed(() => events.value.filter(e => !['text', 'assistant_delta', 'text_delta'].includes(e.type)))
const completedTasks = computed(() => tasks.value.filter(t => ['completed', 'done'].includes(String(t.status).toLowerCase())).length)
const taskProgress = computed(() => tasks.value.length ? Math.round(completedTasks.value / tasks.value.length * 100) : 0)
const tokenCount = computed(() => (run.value?.inputTokens || 0) + (run.value?.outputTokens || 0))
const statusLabel = computed(() => !connected.value ? '等待连接' : status.value?.configured ? '已就绪' : '待配置模型')
const dateLabel = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date())
const normalizeState = value => String(value || '').toLowerCase()
const formatCount = n => Number(n || 0).toLocaleString('zh-CN')
const formatTime = date => date ? new Date(date).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''
const sessionDate = date => date ? new Date(date).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) : ''
const stateLabel = value => ({ queued: '排队中', pending: '等待中', running: '进行中', stopping: '正在停止', completed: '已完成', succeeded: '已完成', failed: '出错了', cancelled: '已停止', stopped: '已停止', in_progress: '进行中' })[normalizeState(value)] || value || '等待中'
const bytes = size => size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`
function eventDetails(event) {
  try { return JSON.stringify(JSON.parse(event.content), null, 2) } catch { return event.content }
}
function eventTitle(event) {
  if (event.type === 'error') return '执行遇到问题'
  if (event.type === 'status') return '运行状态更新'
  if (event.type === 'tool_result') return '工具返回结果'
  try { const data = JSON.parse(event.content); return data.name || data.tool || '调用工具' } catch { return event.type === 'tool_start' ? '正在调用工具' : '运行记录' }
}
function toggleEvent(id) {
  const next = new Set(openedEvents.value)
  next.has(id) ? next.delete(id) : next.add(id)
  openedEvents.value = next
}
async function scrollToBottom() {
  await nextTick()
  if (chatScroller.value) chatScroller.value.scrollTop = chatScroller.value.scrollHeight
}
async function refreshStats() {
  try { stats.value = (await api.get('/stats')).data; statsError.value = ''; await nextTick(); drawChart() }
  catch (e) { statsError.value = errorMessage(e) }
}
async function initialize() {
  initialLoading.value = true
  loadError.value = ''
  const results = await Promise.allSettled([api.get('/status'), api.get('/sessions')])
  if (results[0].status === 'fulfilled') { status.value = results[0].value.data; connected.value = true }
  else { connected.value = false; loadError.value = errorMessage(results[0].reason) }
  if (results[1].status === 'fulfilled') sessions.value = results[1].value.data
  else loadError.value ||= errorMessage(results[1].reason)
  initialLoading.value = false
  await refreshStats()
}
function newSession() {
  if (sending.value || uploading.value) return
  selectionVersion++
  clearTimeout(pollTimer)
  selectedId.value = null
  messages.value = []; events.value = []; run.value = null; files.value = []; tasks.value = []
  prompt.value = ''; sideError.value = ''; loadError.value = ''; view.value = 'workspace'; sidebarOpen.value = false
  nextTick(() => composer.value?.focus())
}
async function refreshSide(id = selectedId.value, version = selectionVersion) {
  if (!id) return
  const result = await Promise.allSettled([api.get(`/sessions/${id}/files`), api.get(`/sessions/${id}/tasks`)])
  if (id !== selectedId.value || version !== selectionVersion) return
  sideError.value = ''
  if (result[0].status === 'fulfilled') files.value = result[0].value.data
  else sideError.value = errorMessage(result[0].reason)
  if (result[1].status === 'fulfilled') tasks.value = result[1].value.data
  else sideError.value ||= errorMessage(result[1].reason)
}
async function selectSession(session) {
  if (sending.value || uploading.value) return
  const version = ++selectionVersion
  clearTimeout(pollTimer)
  selectedId.value = session.id; messages.value = []; events.value = []; run.value = null; files.value = []; tasks.value = []
  loadError.value = ''; sideError.value = ''; loadingSession.value = true; view.value = 'workspace'; sidebarOpen.value = false
  try {
    const [details, latest] = await Promise.all([api.get(`/sessions/${session.id}`), api.get(`/sessions/${session.id}/runs/latest`)])
    if (version !== selectionVersion) return
    messages.value = details.data.messages || []
    run.value = latest.data || null
    await refreshSide(session.id, version)
    if (run.value?.id) await pollRun(run.value.id, version)
    await scrollToBottom()
  } catch (e) { if (version === selectionVersion) loadError.value = errorMessage(e) }
  finally { if (version === selectionVersion) loadingSession.value = false }
}
async function pollRun(runId, version) {
  clearTimeout(pollTimer)
  if (destroyed || version !== selectionVersion) return
  const id = selectedId.value
  try {
    let after = events.value.length ? events.value[events.value.length - 1].id : 0
    const [newEvents, latest] = await Promise.all([api.get(`/runs/${runId}/events`, { params: { after } }), api.get(`/sessions/${id}/runs/latest`)])
    if (version !== selectionVersion || destroyed) return
    const known = new Set(events.value.map(e => e.id))
    events.value.push(...newEvents.data.filter(e => !known.has(e.id)))
    run.value = latest.data
    // Drain paginated events, including the tail after a run finishes.
    let batch = newEvents.data
    while (batch.length === 200) {
      after = batch[batch.length - 1].id
      const next = await api.get(`/runs/${runId}/events`, { params: { after } })
      if (version !== selectionVersion || destroyed) return
      batch = next.data
      const existing = new Set(events.value.map(e => e.id))
      events.value.push(...batch.filter(e => !existing.has(e.id)))
      if (batch.length && batch[batch.length - 1].id === after) break
    }
    loadError.value = ''
    if (newEvents.data.length) await scrollToBottom()
    if (active.value) {
      await refreshSide(id, version)
      if (version === selectionVersion) pollTimer = setTimeout(() => pollRun(runId, version), 1200)
    } else {
      const details = await api.get(`/sessions/${id}`)
      if (version !== selectionVersion) return
      messages.value = details.data.messages || []
      const entry = sessions.value.find(session => session.id === id)
      if (entry) Object.assign(entry, { title: details.data.title, updatedAt: details.data.updatedAt })
      await refreshSide(id, version)
      await refreshStats()
      await scrollToBottom()
    }
  } catch (e) {
    if (version !== selectionVersion || destroyed) return
    loadError.value = errorMessage(e)
    if (active.value) pollTimer = setTimeout(() => pollRun(runId, version), 3500)
  }
}
async function send() {
  const text = prompt.value.trim()
  if (!text || sending.value || active.value || uploading.value) return
  if (!connected.value || !status.value?.configured) { settingsOpen.value = true; return }
  sending.value = true; loadError.value = ''; view.value = 'workspace'
  try {
    if (!selectedId.value) {
      const created = (await api.post('/sessions', { title: text.slice(0, 28) })).data
      sessions.value.unshift(created); selectedId.value = created.id
    }
    const createdRun = (await api.post(`/sessions/${selectedId.value}/runs`, { prompt: text })).data
    const version = ++selectionVersion
    clearTimeout(pollTimer)
    prompt.value = ''; events.value = []; openedEvents.value = new Set(); run.value = createdRun
    messages.value.push({ id: `local-${Date.now()}`, role: 'user', content: text, createdAt: new Date().toISOString() })
    await scrollToBottom()
    pollRun(createdRun.id, version)
  } catch (e) { loadError.value = errorMessage(e); ElMessage.error(loadError.value) }
  finally { sending.value = false }
}
async function stop() {
  if (!run.value?.id || stopping.value) return
  const runId = run.value.id
  const version = selectionVersion
  stopping.value = true
  try {
    const result = await api.post(`/runs/${runId}/stop`)
    if (version === selectionVersion && run.value?.id === runId) {
      run.value = result.data
      await pollRun(runId, version)
    }
    ElMessage.info('任务已停止。')
  }
  catch (e) { ElMessage.error(errorMessage(e)) }
  finally { stopping.value = false }
}
function onComposerKey(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); send() }
}
async function openFile(file) {
  fileOpen.value = true; fileLoading.value = true; openedFile.value = { path: file.path, content: '' }
  try { openedFile.value = (await api.get(`/sessions/${selectedId.value}/files/content`, { params: { path: file.path } })).data }
  catch (e) { openedFile.value.content = errorMessage(e) }
  finally { fileLoading.value = false }
}
async function copyFile() {
  try { await navigator.clipboard.writeText(openedFile.value.content); ElMessage.success('已复制文件内容') }
  catch { ElMessage.error('浏览器未允许复制，请手动选择文本。') }
}
function downloadFile() {
  const url = URL.createObjectURL(new Blob([openedFile.value.content], { type: 'text/plain;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = openedFile.value.path.split('/').pop() || 'file.txt'; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
async function uploadFiles(event) {
  const selected = Array.from(event.target.files || [])
  event.target.value = ''
  if (!selected.length || active.value || uploading.value) return
  const invalid = selected.find(file => file.size > 1024 * 1024)
  if (invalid) { ElMessage.error(`${invalid.name} 超过了 1 MB，请选择较小的代码或文本文件。`); return }
  uploading.value = true
  try {
    if (!selectedId.value) {
      const created = (await api.post('/sessions', { title: '新的灵感' })).data
      sessions.value.unshift(created); selectedId.value = created.id
    }
    for (const file of selected) {
      const data = new FormData(); data.append('file', file)
      await api.post(`/sessions/${selectedId.value}/files`, data)
    }
    ElMessage.success(`已放入 ${selected.length} 个文件，可以让薄荷阅读它们了。`)
    sideTab.value = 'files'
  } catch (e) { ElMessage.error(errorMessage(e)) }
  finally { await refreshSide(); uploading.value = false }
}
async function changeView(next) {
  view.value = next; sidebarOpen.value = false; contextOpen.value = false
  if (next === 'analytics') { await refreshStats(); await nextTick(); drawChart() }
}
async function drawChart() {
  if (!chartElement.value || view.value !== 'analytics') return
  if (!echarts) echarts = await import('./chart.js')
  if (!chartElement.value || view.value !== 'analytics') return
  if (!chart || chart.getDom() !== chartElement.value) {
    chart?.dispose(); chartObserver?.disconnect()
    chart = echarts.init(chartElement.value)
    chartObserver = new ResizeObserver(() => chart?.resize())
    chartObserver.observe(chartElement.value)
  }
  const daily = stats.value.daily || []
  chart.setOption({
    tooltip: { trigger: 'axis', backgroundColor: '#fff', borderColor: '#e7ede7', textStyle: { color: '#314638', fontSize: 14, fontFamily: '"Inter", "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif' } },
    grid: { left: 16, right: 22, top: 28, bottom: 20, containLabel: true },
    xAxis: { type: 'category', boundaryGap: false, data: daily.map(d => d.date?.slice(5)), axisLine: { lineStyle: { color: '#e7ede7' } }, axisTick: { show: false }, axisLabel: { color: '#526557', fontSize: 14, margin: 16 } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eff2ed', type: 'dashed' } }, axisLabel: { color: '#526557', fontSize: 14 } },
    series: [{ type: 'line', smooth: 0.4, symbolSize: 7, data: daily.map(d => d[chartMetric.value] || 0), itemStyle: { color: '#70a98a', borderWidth: 3, borderColor: '#fff' }, lineStyle: { width: 3, color: '#70a98a' }, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: '#d7ebdc' }, { offset: 1, color: '#f9fcf8' }]) } }],
  }, true)
}
onMounted(initialize)
onBeforeUnmount(() => { destroyed = true; clearTimeout(pollTimer); chartObserver?.disconnect(); chart?.dispose() })
</script>

<template>
  <div class="app-shell">
    <div v-if="sidebarOpen" class="sidebar-scrim" @click="sidebarOpen = false"></div>
    <aside class="sidebar" :class="{ open: sidebarOpen }">
      <a class="brand" href="#" @click.prevent="newSession">
        <div class="brand-mark"><img :src="mascotUrl" alt="薄荷猫娘" width="50" height="50" /></div>
        <div><strong>Minty Code<span class="brand-dot">.</span></strong><small>让灵感，慢慢发芽</small></div>
      </a>
      <button class="new-chat" @click="newSession"><Icon name="plus" :size="18" /><span>开启新的灵感</span><kbd>＋</kbd></button>
      <div class="nav-label">我的空间 <span>WORKSPACE</span></div>
      <nav class="main-nav" aria-label="主导航">
        <button :class="{ selected: view === 'workspace' }" @click="changeView('workspace')"><Icon name="grid" /><span>编程工作台</span><span class="nav-dot" v-if="view === 'workspace'"></span></button>
        <button :class="{ selected: view === 'analytics' }" @click="changeView('analytics')"><Icon name="chart" /><span>使用概览</span></button>
      </nav>
      <div class="history-heading"><span>最近的对话</span><span class="history-count">{{ sessions.length }}</span></div>
      <div class="session-list">
        <div v-if="initialLoading" class="sidebar-empty"><span class="loading-dot"></span>正在寻找你的灵感…</div>
        <div v-else-if="!sessions.length" class="sidebar-empty">这里还很安静<br />第一段灵感，等你开始。</div>
        <button v-for="session in sessions" :key="session.id" class="session-item" :class="{ selected: selectedId === session.id && view === 'workspace' }" @click="selectSession(session)">
          <Icon name="chat" :size="16" /><span>{{ session.title || '新的对话' }}</span><small>{{ sessionDate(session.updatedAt || session.createdAt) }}</small>
        </button>
      </div>
      <div class="sidebar-bottom">
        <button class="settings-button" @click="settingsOpen = true"><Icon name="settings" :size="18" /><span>设置与连接</span><span :class="['connection-dot', { online: connected && status?.configured }]"></span></button>
        <div class="personal-profile"><div class="profile-avatar">S<span></span></div><div><strong>我的个人工作室</strong><small>Personal workspace</small></div><span class="local-pill">本地</span></div>
      </div>
    </aside>

    <main class="main-area">
      <header class="topbar">
        <div class="breadcrumb"><button class="icon-button mobile-menu" aria-label="打开导航" @click="sidebarOpen = true"><Icon name="menu" /></button><span class="breadcrumb-home">我的空间</span><span class="breadcrumb-slash">/</span><strong>{{ view === 'analytics' ? '使用概览' : '编程工作台' }}</strong><span class="topbar-badge">PERSONAL</span></div>
        <div class="topbar-right"><span class="date-label">{{ dateLabel }}</span><button class="status-pill" @click="settingsOpen = true"><span :class="['connection-dot', { online: connected && status?.configured }]"></span>{{ statusLabel }}</button></div>
      </header>

      <div v-if="view === 'analytics'" class="analytics-view">
        <div class="page-eyebrow"><span></span> A LITTLE PROGRESS, EVERY DAY</div>
        <h1>每一份灵感，都有迹可循<span class="heading-spark">✳</span></h1>
        <p class="page-description">看看你和薄荷一起完成了多少小事。</p>
        <el-alert v-if="statsError" :title="statsError" type="error" :closable="false" show-icon class="spaced-alert" />
        <div class="stat-grid">
          <div v-for="item in [{label:'对话空间', value:stats.sessions, icon:'chat', color:'mint', unit:'个'}, {label:'发起任务', value:stats.runs, icon:'bolt', color:'yellow', unit:'次'}, {label:'已完成任务', value:stats.completedRuns, icon:'check', color:'pink', unit:'次'}, {label:'累计 Token', value:stats.totalTokens, icon:'layers', color:'lavender', unit:'tokens'}]" :key="item.label" class="stat-card"><div :class="['stat-icon', item.color]"><Icon :name="item.icon" :size="22" /></div><span>{{ item.label }}</span><div><strong>{{ formatCount(item.value) }}</strong><small>{{ item.unit }}</small></div></div>
        </div>
        <section class="chart-card"><div class="section-title-row"><div><h2>灵感生长曲线 <span>ACTIVITY</span></h2><p>最近的任务与模型使用情况</p></div><el-radio-group v-model="chartMetric" size="small" @change="drawChart"><el-radio-button value="runs">任务次数</el-radio-button><el-radio-button value="tokens">Token 用量</el-radio-button></el-radio-group></div><div ref="chartElement" class="activity-chart"></div><div v-if="!(stats.daily || []).some(d => d.runs || d.tokens)" class="chart-empty"><Icon name="leaf" :size="26" /><span>完成第一个任务，让曲线长出新芽。</span></div></section>
        <div class="analytics-footnote"><Icon name="shield" :size="16" />统计来自本地保存的真实运行记录，Token 用量由模型服务返回。</div>
      </div>

      <div v-else class="workspace-layout">
        <section class="conversation-area">
          <div class="conversation-header"><div><span class="mini-icon"><Icon name="chat" :size="16" /></span><strong>{{ activeSession?.title || '一片新的灵感' }}</strong><span class="session-badge">{{ selectedId ? '已保存' : '新对话' }}</span></div><div class="conversation-actions"><button class="icon-button context-toggle" title="任务与文件" aria-label="打开任务与文件" @click="contextOpen = true"><Icon name="folder" :size="18" /></button><button class="icon-button" title="设置与连接" aria-label="设置与连接" @click="settingsOpen = true"><Icon name="settings" :size="18" /></button></div></div>
          <div ref="chatScroller" class="chat-scroll">
            <div v-if="loadingSession" class="loading-state"><span class="loading-dot"></span>正在打开对话…</div>
            <div v-else-if="!messages.length && !active" class="welcome">
              <div class="welcome-illustration"><img class="welcome-mascot" :src="mascotUrl" alt="薄荷猫娘，你的编程小搭档" width="180" height="180" fetchpriority="high" /></div>
              <h1>你好呀，今天想创造点什么<span>？</span></h1>
              <p>我是薄荷，你的编程小搭档。<br />从一个小点子开始，我们一起把它变成现实。</p>
            </div>
            <div v-else class="message-list">
              <div class="conversation-start"><span></span>灵感从这里开始<span></span></div>
              <article v-for="message in messages.filter(m => ['user','assistant'].includes(m.role))" :key="message.id" :class="['message', message.role]"><div :class="['message-avatar', message.role]"><img v-if="message.role === 'assistant'" :src="mascotUrl" alt="" width="44" height="44" /><span v-else>我</span></div><div class="message-body"><div class="message-meta"><strong>{{ message.role === 'assistant' ? '薄荷' : '你' }}</strong><span>{{ formatTime(message.createdAt) }}</span><span v-if="message.role === 'assistant'" class="assistant-tag">MINTY</span></div><div v-if="message.role === 'assistant'" class="markdown-content" v-html="markdown(message.content)"></div><div v-else class="user-content">{{ message.content }}</div></div></article>
              <article v-if="active" class="message assistant"><div class="message-avatar assistant"><img :src="mascotUrl" alt="" width="44" height="44" /></div><div class="message-body"><div class="message-meta"><strong>薄荷</strong><span class="assistant-tag">MINTY</span><span class="thinking-label"><span class="loading-dot"></span>{{ run?.status === 'stopping' ? '正在收好手头的工作' : '正在认真处理' }}</span></div><div v-if="liveText" class="markdown-content" v-html="markdown(liveText)"></div><div v-else class="thinking-bubbles"><i></i><i></i><i></i></div></div></article>
              <div v-if="toolEvents.length" class="execution-card"><div class="execution-heading"><Icon name="terminal" :size="16" /><strong>执行足迹</strong><span>{{ toolEvents.length }} 条记录</span><span v-if="active" class="live-badge">LIVE</span></div><div class="execution-items"><div v-for="event in toolEvents" :key="event.id" :class="['execution-item', { error: event.type === 'error' }]"><button @click="toggleEvent(event.id)"><Icon :name="event.type === 'error' ? 'close' : event.type === 'tool_start' ? 'terminal' : 'check'" :size="14" /><span>{{ eventTitle(event) }}</span><small>{{ formatTime(event.createdAt) }}</small><Icon name="chevron" :size="13" :class="{ rotated: openedEvents.has(event.id) }" /></button><pre v-if="openedEvents.has(event.id)">{{ eventDetails(event) }}</pre></div></div></div>
              <div v-if="run && !active" :class="['run-summary', normalizeState(run.status)]"><Icon :name="normalizeState(run.status) === 'failed' ? 'close' : 'check'" :size="14" /><span>{{ stateLabel(run.status) }}</span><span v-if="tokenCount">· {{ formatCount(tokenCount) }} tokens</span><span v-if="run.error" class="run-error">{{ run.error }}</span></div>
            </div>
          </div>
          <div class="composer-wrap">
            <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon class="composer-alert"><template #default><button class="text-button" @click="selectedId ? selectSession(activeSession || { id: selectedId }) : initialize()">重新连接</button></template></el-alert>
            <div v-else-if="!initialLoading && connected && !status?.configured" class="config-banner"><Icon name="bolt" :size="16" /><span>再一步就好：配置模型 API Key，即可开始对话。</span><button @click="settingsOpen = true">查看配置 <Icon name="chevron" :size="12" /></button></div>
            <div class="composer" :class="{ busy: active }"><textarea ref="composer" v-model="prompt" :disabled="active || sending" aria-label="输入编程任务" placeholder="把你的想法告诉薄荷，剩下的我们一起完成…" rows="3" maxlength="20000" @keydown="onComposerKey"></textarea><div class="composer-bottom"><div class="composer-context"><Icon name="folder" :size="15" /><span>独立会话工作区</span><span class="context-separator"></span><span class="model-name">{{ status?.model || '模型未连接' }}</span></div><button v-if="active" class="stop-button" :disabled="stopping" @click="stop"><Icon name="stop" :size="13" />{{ stopping ? '停止中' : '停止任务' }}</button><button v-else class="send-button" :disabled="!prompt.trim() || sending || initialLoading || uploading" aria-label="发送任务" @click="send"><span v-if="sending" class="loading-dot"></span><Icon v-else name="arrow" :size="20" /></button></div></div>
            <div class="composer-footnote"><span><kbd>Enter</kbd> 发送 · <kbd>Shift + Enter</kbd> 换行</span><span><Icon name="shield" :size="12" />仅供个人使用，请检查重要修改</span></div>
          </div>
        </section>

        <div v-if="contextOpen" class="context-scrim" @click="contextOpen = false"></div><aside class="context-panel" :class="{ open: contextOpen }">
          <div class="context-heading"><div><Icon name="layers" :size="18" /><strong>灵感工作区</strong></div><button v-if="contextOpen" class="icon-button" aria-label="关闭工作区" @click="contextOpen = false"><Icon name="close" :size="16" /></button><button class="icon-button" aria-label="刷新任务和文件" title="刷新任务和文件" :disabled="!selectedId" @click="refreshSide()"><Icon name="refresh" :size="15" /></button></div>
          <div class="context-tabs"><button :class="{ selected: sideTab === 'tasks' }" @click="sideTab = 'tasks'">任务清单<span>{{ tasks.length }}</span></button><button :class="{ selected: sideTab === 'files' }" @click="sideTab = 'files'">工作文件<span>{{ files.length }}</span></button></div>
          <div v-if="sideTab === 'files'" class="upload-toolbar"><input ref="uploadInput" type="file" multiple hidden @change="uploadFiles" /><button class="upload-button" :disabled="active || uploading || sending" @click="uploadInput?.click()"><span v-if="uploading" class="loading-dot"></span><Icon v-else name="plus" :size="14" />{{ uploading ? '正在放入文件…' : '添加代码文件' }}</button><small>UTF-8 文本 · 每个文件 ≤ 1 MB</small></div><div v-if="sideError" class="side-error">{{ sideError }}</div>
          <div class="context-content">
            <template v-if="sideTab === 'tasks'"><div v-if="tasks.length" class="task-progress"><div><span>一点点，靠近完成</span><strong>{{ taskProgress }}%</strong></div><el-progress :percentage="taskProgress" :show-text="false" :stroke-width="5" color="#85b397" /></div><div v-if="!tasks.length" class="context-empty"><div class="empty-illustration"><Icon name="check" :size="27" /><i></i><i></i></div><strong>给灵感留一点位置</strong><p>薄荷拆解任务后，<br />会在这里记下每一个小步骤。</p></div><div v-for="task in tasks" :key="task.id" class="task-item"><span :class="['task-state', normalizeState(task.status)]"><Icon :name="normalizeState(task.status) === 'completed' ? 'check' : 'circle'" :size="15" /></span><div><strong>{{ task.subject || task.title }}</strong><p v-if="task.description">{{ task.description }}</p><small>{{ stateLabel(task.status) }}</small></div></div></template>
            <template v-else><div v-if="!files.length" class="context-empty"><div class="empty-illustration file-empty"><Icon name="folder" :size="28" /></div><strong>作品还在酝酿中</strong><p>添加已有代码，或让薄荷创建文件，<br />它们会被好好收在这里。</p></div><button v-for="file in files" :key="file.path" class="file-item" @click="openFile(file)"><span class="file-icon"><Icon name="file" :size="19" /></span><span><strong>{{ file.path.split('/').pop() }}</strong><small :title="file.path">{{ file.path }}</small></span><small>{{ bytes(file.size || 0) }}</small></button></template>
          </div>
          <div class="workspace-info"><div class="info-eyebrow"><span></span> 当前工作区</div><div><Icon name="folder" :size="15" /><span>{{ selectedId ? `会话 ${String(selectedId).slice(0, 8)}` : '尚未创建工作区' }}</span></div><p>当前会话的任务与文件独立保存。</p></div>
        </aside>
      </div>
    </main>

    <el-dialog v-model="settingsOpen" title="设置与连接" width="540px" class="mint-dialog" align-center><div class="settings-intro"><div class="settings-mascot"><img :src="mascotUrl" alt="" width="64" height="64" /></div><div><h3>让薄荷准备好出发</h3><p>模型密钥只保存在后端，不会发送到网页。</p></div></div><div class="settings-rows"><div><span>后端连接</span><strong :class="connected ? 'text-mint' : 'text-error'">{{ connected ? '连接正常' : '尚未连接' }}</strong></div><div><span>模型配置</span><strong>{{ status?.configured ? '已配置' : '尚未配置' }}</strong></div><div><span>当前模型</span><code>{{ status?.model || '—' }}</code></div><div><span>命令执行</span><strong>{{ status?.shellEnabled ? '已启用' : '已关闭（仅文件工具）' }}</strong></div><div><span>服务状态</span><strong>{{ statusLabel }}</strong></div></div><div class="settings-guide"><strong>首次使用</strong><ol><li>按项目 README 配置 PostgreSQL 与后端环境变量。</li><li>设置 <code>ANTHROPIC_API_KEY</code>、<code>MODEL_ID</code>；需要时设置兼容服务的地址。</li><li>启动后端，再点击下方按钮检查连接。</li></ol><p>为了保护密钥，配置需要在本机后端完成。此页面不会展示或保存 API Key。</p></div><template #footer><el-button @click="settingsOpen = false">稍后再说</el-button><el-button type="primary" :loading="initialLoading" @click="initialize">重新检查连接</el-button></template></el-dialog>
    <el-dialog v-model="fileOpen" :title="openedFile.path" width="min(900px, 94vw)" class="mint-dialog file-dialog"><div v-if="fileLoading" class="loading-state">正在打开文件…</div><pre v-else class="file-preview">{{ openedFile.content }}</pre><template #footer><el-button @click="fileOpen = false">关闭</el-button><el-button :disabled="fileLoading" @click="downloadFile">下载文件</el-button><el-button type="primary" :disabled="fileLoading" @click="copyFile"><Icon name="copy" :size="14" />复制内容</el-button></template></el-dialog>
  </div>
</template>
