const form = document.querySelector('#query-form');
const input = document.querySelector('#query-input');
const messages = document.querySelector('#messages');
const emptyState = document.querySelector('#empty-state');
const sendButton = document.querySelector('#send-button');
const micButton = document.querySelector('#mic-button');
const voiceStatus = document.querySelector('#voice-status');
const newChatButton = document.querySelector('#new-chat');
const historyList = document.querySelector('#conversation-history');
const reportEmail = document.querySelector('#report-email');
const autoEmail = document.querySelector('#auto-email');
const reportSettingsTrigger = document.querySelector('#report-settings-trigger');
const reportSettingsDialog = document.querySelector('#report-settings-dialog');
const reportSettingsSend = document.querySelector('#report-settings-send');
const reportSettingsStatus = document.querySelector('#report-settings-status');
const shell = document.querySelector('#assistant-shell');
const sidebarToggle = document.querySelector('#sidebar-toggle');
const sidebarCollapse = document.querySelector('#sidebar-collapse');
const sidebarBackdrop = document.querySelector('#sidebar-backdrop');
const storageKey = 'employeeInsights.conversations.v1';
const reportSettingsStorageKey = 'employeeInsights.reportSettings.v1';
const maxHistoryMessages = 8;
const maxConversations = 30;
const mobileSidebar = window.matchMedia('(max-width: 760px)');
let conversations = loadConversations();
let currentConversationId = null;
let activeRequest = null;
let recognition = null;
let recording = false;
let nextQuestionIsVoice = false;
let latestReportId = null;

function loadReportSettings() {
  try {
    const settings = JSON.parse(localStorage.getItem(reportSettingsStorageKey) || '{}');
    reportEmail.value = typeof settings.email === 'string' ? settings.email.slice(0, 254) : '';
    autoEmail.checked = settings.autoEmail === true;
  } catch {
    reportEmail.value = '';
    autoEmail.checked = false;
  }
}

function saveReportSettings() {
  try {
    localStorage.setItem(reportSettingsStorageKey, JSON.stringify({
      email: reportEmail.value.trim().slice(0, 254),
      autoEmail: autoEmail.checked,
    }));
  } catch {
    setVoiceStatus('Report preferences could not be saved on this device.');
  }
}

function loadConversations() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
    if (!Array.isArray(saved)) return [];
    return saved.filter((conversation) =>
      conversation && typeof conversation.id === 'string'
      && typeof conversation.title === 'string' && Array.isArray(conversation.messages)
    ).map((conversation) => ({
      id: conversation.id,
      title: conversation.title,
      updatedAt: Number(conversation.updatedAt) || Date.now(),
      messages: conversation.messages.filter((message) =>
        message && ['user', 'assistant'].includes(message.role) && typeof message.content === 'string'
      ).map((message) => ({
        role: message.role,
        content: message.content,
        ...(message.analysis && typeof message.analysis === 'object' ? { analysis: message.analysis } : {}),
        ...(typeof message.reportId === 'string' ? { reportId: message.reportId } : {}),
        ...(typeof message.reportUrl === 'string' && message.reportUrl.startsWith('/api/reports/')
          ? { reportUrl: message.reportUrl } : {}),
        ...(typeof message.emailStatus === 'string' ? { emailStatus: message.emailStatus } : {}),
      })),
    })).slice(0, maxConversations);
  } catch {
    return [];
  }
}

function saveConversations() {
  try {
    localStorage.setItem(storageKey, JSON.stringify(conversations.slice(0, maxConversations)));
  } catch {
    setVoiceStatus('This conversation could not be saved on this device.');
  }
}

function currentConversation() {
  return conversations.find((conversation) => conversation.id === currentConversationId);
}

function renderHistory() {
  historyList.replaceChildren();
  [...conversations].sort((left, right) => right.updatedAt - left.updatedAt).forEach((conversation) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'history-item';
    item.classList.toggle('is-active', conversation.id === currentConversationId);
    item.textContent = conversation.title;
    item.title = conversation.title;
    item.setAttribute('aria-current', conversation.id === currentConversationId ? 'page' : 'false');
    item.addEventListener('click', () => openConversation(conversation.id));
    historyList.append(item);
  });
}

function makeConversation(requirement) {
  const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const title = requirement.replace(/\s+/g, ' ').trim();
  const conversation = {
    id,
    title: title.length > 48 ? `${title.slice(0, 47)}…` : title,
    updatedAt: Date.now(),
    messages: [],
  };
  conversations.unshift(conversation);
  conversations = conversations.slice(0, maxConversations);
  currentConversationId = id;
  return conversation;
}

function createUserMessage(content) {
  const article = document.createElement('article');
  article.className = 'chat-message chat-user';
  const label = document.createElement('div');
  label.className = 'chat-message-label';
  label.textContent = 'You';
  const text = document.createElement('p');
  text.className = 'chat-user-text';
  text.textContent = content;
  article.append(label, text);
  return article;
}

function createAssistantMessage(content, options = {}) {
  const article = document.createElement('article');
  article.className = 'chat-message chat-assistant';
  const label = document.createElement('div');
  label.className = 'chat-message-label assistant-label';
  const mark = document.createElement('span');
  mark.className = 'assistant-mark';
  mark.setAttribute('aria-hidden', 'true');
  mark.textContent = 'E';
  const name = document.createElement('span');
  name.textContent = 'Employee Insights Assistant';
  label.append(mark, name);

  const answer = document.createElement('div');
  answer.className = options.error ? 'chat-answer chat-error' : 'chat-answer';
  const text = document.createElement('p');
  text.className = 'chat-answer-text';
  text.textContent = content;
  answer.append(text);
  article.append(label, answer);

  if (options.analysis) appendAnalysis(article, options.analysis);
  if (options.reportUrl && options.reportId) {
    appendReportActions(article, options.reportUrl, options.reportId, options.emailStatus);
  }
  if (options.speak) addStopAudioButton(article, text.textContent);
  return article;
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS('http://www.w3.org/2000/svg', name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
  return element;
}

function appendChart(parent, chart) {
  const supportedTypes = new Set(['bar', 'column', 'line', 'area', 'pie', 'donut', 'scatter', 'histogram', 'box', 'heatmap']);
  if (!chart || !supportedTypes.has(chart.type)) return;
  const labels = Array.isArray(chart.labels) ? chart.labels.slice(0, 12).map(String) : [];
  const values = Array.isArray(chart.values) ? chart.values.slice(0, 12).map(Number) : [];
  const palette = ['#176344', '#4d9472', '#80ad91', '#b0c9b7', '#d5e1d8', '#8a9b90'];
  const section = document.createElement('section');
  section.className = 'analysis-chart';
  const heading = document.createElement('h3');
  heading.textContent = chart.title || 'Results';
  section.append(heading);

  const details = document.createElement('p');
  details.className = 'chart-details';
  details.textContent = [
    chart.x_label && `${chart.x_label}${chart.x_unit ? ` (${chart.x_unit})` : ''}`,
    chart.y_label && `${chart.y_label}${chart.y_unit ? ` (${chart.y_unit})` : ''}`,
    chart.unit && `Units: ${chart.unit}`,
  ].filter(Boolean).join(' · ');
  if (details.textContent) section.append(details);

  const width = 720;
  const height = 300;
  const svg = svgElement('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': chart.title || 'Results chart' });
  const addText = (text, x, y, className = 'chart-axis-label', anchor = 'middle') => {
    const node = svgElement('text', { x, y, 'text-anchor': anchor, class: className });
    node.textContent = String(text);
    svg.append(node);
    return node;
  };

  if (chart.type === 'pie' || chart.type === 'donut') {
    const total = values.reduce((sum, value) => sum + value, 0);
    if (labels.length < 2 || labels.length !== values.length || values.some((value) => value < 0) || total <= 0) return;
    const centerX = 165;
    const centerY = 145;
    const outerRadius = 105;
    const innerRadius = chart.type === 'donut' ? 58 : 0;
    let angle = -Math.PI / 2;
    values.forEach((value, index) => {
      const sweep = value / total * Math.PI * 2;
      if (sweep <= 0) return;
      const point = (radius, theta) => [centerX + radius * Math.cos(theta), centerY + radius * Math.sin(theta)];
      const [startX, startY] = point(outerRadius, angle);
      const [endX, endY] = point(outerRadius, angle + sweep);
      const largeArc = sweep > Math.PI ? 1 : 0;
      let path;
      if (innerRadius) {
        const [innerEndX, innerEndY] = point(innerRadius, angle + sweep);
        const [innerStartX, innerStartY] = point(innerRadius, angle);
        path = `M ${startX} ${startY} A ${outerRadius} ${outerRadius} 0 ${largeArc} 1 ${endX} ${endY} L ${innerEndX} ${innerEndY} A ${innerRadius} ${innerRadius} 0 ${largeArc} 0 ${innerStartX} ${innerStartY} Z`;
      } else {
        path = `M ${centerX} ${centerY} L ${startX} ${startY} A ${outerRadius} ${outerRadius} 0 ${largeArc} 1 ${endX} ${endY} Z`;
      }
      svg.append(svgElement('path', { d: path, fill: palette[index % palette.length], class: 'chart-slice' }));
      angle += sweep;
    });
    if (innerRadius) addText(total.toLocaleString(undefined, { maximumFractionDigits: 1 }), centerX, centerY + 5, 'chart-center-value');
    labels.forEach((label, index) => {
      const y = 52 + index * 30;
      svg.append(svgElement('rect', { x: 340, y: y - 10, width: 11, height: 11, rx: 3, fill: palette[index % palette.length] }));
      addText(label.slice(0, 30), 360, y, 'chart-legend-label', 'start');
      addText(`${values[index].toLocaleString(undefined, { maximumFractionDigits: 2 })} (${(values[index] / total * 100).toFixed(0)}%)`, 700, y, 'chart-value', 'end');
    });
  } else if (chart.type === 'scatter') {
    const points = Array.isArray(chart.points) ? chart.points.filter((point) => Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y))).slice(0, 300) : [];
    if (points.length < 3) return;
    const plot = { left: 70, right: 690, top: 25, bottom: 235 };
    const xs = points.map((point) => Number(point.x));
    const ys = points.map((point) => Number(point.y));
    const xMin = Math.min(...xs); const xMax = Math.max(...xs);
    const yMin = Math.min(...ys); const yMax = Math.max(...ys);
    const xRange = xMax - xMin || 1; const yRange = yMax - yMin || 1;
    for (let tick = 0; tick <= 4; tick += 1) {
      const x = plot.left + (plot.right - plot.left) * tick / 4;
      const y = plot.bottom - (plot.bottom - plot.top) * tick / 4;
      svg.append(svgElement('line', { x1: x, y1: plot.top, x2: x, y2: plot.bottom, class: 'chart-gridline' }));
      svg.append(svgElement('line', { x1: plot.left, y1: y, x2: plot.right, y2: y, class: 'chart-gridline' }));
      addText((xMin + xRange * tick / 4).toLocaleString(undefined, { maximumFractionDigits: 2 }), x, 253);
      addText((yMin + yRange * tick / 4).toLocaleString(undefined, { maximumFractionDigits: 2 }), plot.left - 9, y + 4, 'chart-axis-label', 'end');
    }
    points.forEach((point) => svg.append(svgElement('circle', {
      cx: plot.left + (Number(point.x) - xMin) / xRange * (plot.right - plot.left),
      cy: plot.bottom - (Number(point.y) - yMin) / yRange * (plot.bottom - plot.top),
      r: 4, class: 'chart-point',
    })));
    addText(chart.x_label || 'X', 380, 285);
    addText(`${chart.y_label || 'Y'}${chart.y_unit ? ` (${chart.y_unit})` : ''}`, 10, 16, 'chart-axis-label', 'start');
  } else if (chart.type === 'box') {
    const groups = Array.isArray(chart.groups) ? chart.groups.slice(0, 8) : [];
    if (!groups.length) return;
    const numbers = groups.flatMap((group) => [group.min, group.max, ...(Array.isArray(group.outliers) ? group.outliers : [])]).map(Number).filter(Number.isFinite);
    if (!numbers.length) return;
    const minimum = Math.min(...numbers); const maximum = Math.max(...numbers);
    const range = maximum - minimum || 1;
    const left = 175; const right = 680; const rowHeight = 30;
    groups.forEach((group, index) => {
      const y = 40 + index * rowHeight;
      const x = (value) => left + (Number(value) - minimum) / range * (right - left);
      addText(String(group.label).slice(0, 22), left - 12, y + 4, 'chart-axis-label', 'end');
      svg.append(svgElement('line', { x1: x(group.min), y1: y, x2: x(group.max), y2: y, class: 'chart-whisker' }));
      svg.append(svgElement('rect', { x: x(group.q1), y: y - 8, width: Math.max(2, x(group.q3) - x(group.q1)), height: 16, class: 'chart-box' }));
      svg.append(svgElement('line', { x1: x(group.median), y1: y - 9, x2: x(group.median), y2: y + 9, class: 'chart-median' }));
      [group.min, group.max].forEach((value) => svg.append(svgElement('line', { x1: x(value), y1: y - 5, x2: x(value), y2: y + 5, class: 'chart-whisker' })));
      (Array.isArray(group.outliers) ? group.outliers : []).forEach((value) => {
        svg.append(svgElement('circle', { cx: x(value), cy: y, r: 3, class: 'chart-outlier' }));
      });
    });
    for (let tick = 0; tick <= 4; tick += 1) {
      const value = minimum + range * tick / 4;
      const tickX = left + (right - left) * tick / 4;
      svg.append(svgElement('line', { x1: tickX, y1: 20, x2: tickX, y2: 25, class: 'chart-gridline' }));
      addText(value.toLocaleString(undefined, { maximumFractionDigits: 2 }), tickX, 17, 'chart-axis-label');
    }
    addText(chart.x_label || chart.metric || 'Measure', (left + right) / 2, 290);
    const groupAxis = addText(chart.y_label || 'Group', 12, 145, 'chart-axis-label');
    groupAxis.setAttribute('transform', 'rotate(-90 12 145)');
  } else if (chart.type === 'heatmap') {
    const xLabels = Array.isArray(chart.x_labels) ? chart.x_labels.slice(0, 10) : [];
    const yLabels = Array.isArray(chart.y_labels) ? chart.y_labels.slice(0, 10) : [];
    const matrix = Array.isArray(chart.matrix) ? chart.matrix.slice(0, yLabels.length) : [];
    const flat = matrix.flat().map(Number).filter(Number.isFinite);
    if (xLabels.length < 2 || yLabels.length < 2 || !flat.length) return;
    const low = Math.min(...flat); const high = Math.max(...flat); const range = high - low || 1;
    const left = 145; const top = 35; const cellWidth = Math.min(52, 510 / xLabels.length); const cellHeight = Math.min(27, 180 / yLabels.length);
    yLabels.forEach((label, rowIndex) => {
      addText(String(label).slice(0, 20), left - 9, top + rowIndex * cellHeight + cellHeight * 0.68, 'chart-axis-label', 'end');
      xLabels.forEach((xLabel, columnIndex) => {
        const value = Number(matrix[rowIndex]?.[columnIndex] || 0);
        const intensity = Math.max(0, Math.min(1, (value - low) / range));
        const cellX = left + columnIndex * cellWidth;
        const cellY = top + rowIndex * cellHeight;
        svg.append(svgElement('rect', { x: cellX, y: cellY, width: cellWidth - 2, height: cellHeight - 2, rx: 2, fill: `rgb(${235 - intensity * 120}, ${244 - intensity * 90}, ${237 - intensity * 120})`, class: 'chart-heat-cell' }));
        if (xLabels.length <= 7 && yLabels.length <= 7) addText(value.toLocaleString(undefined, { maximumFractionDigits: 1 }), cellX + cellWidth / 2, cellY + cellHeight * 0.65, 'chart-heat-value');
      });
    });
    xLabels.forEach((label, index) => addText(String(label).slice(0, 12), left + index * cellWidth + cellWidth / 2, top + yLabels.length * cellHeight + 15));
    const scaleY = 270;
    const scaleLeft = left + 160;
    for (let index = 0; index < 8; index += 1) {
      const intensity = index / 7;
      svg.append(svgElement('rect', {
        x: scaleLeft + index * 18, y: scaleY - 8, width: 18, height: 9,
        fill: `rgb(${235 - intensity * 120}, ${244 - intensity * 90}, ${237 - intensity * 120})`,
      }));
    }
    addText(`${low.toLocaleString()} ${chart.unit || ''}`.trim(), scaleLeft - 8, scaleY + 14, 'chart-axis-label', 'end');
    addText(`${high.toLocaleString()} ${chart.unit || ''}`.trim(), scaleLeft + 152, scaleY + 14, 'chart-axis-label', 'start');
  } else {
    if (labels.length < (chart.type === 'histogram' ? 1 : 2) || labels.length !== values.length || values.some((value) => !Number.isFinite(value))) return;
    const plot = { left: 70, right: 700, top: 25, bottom: 228 };
    const minimum = Math.min(0, ...values); const maximum = Math.max(0, ...values);
    const range = maximum - minimum || 1;
    const plotHeight = plot.bottom - plot.top;
    for (let tick = 0; tick <= 4; tick += 1) {
      const y = plot.bottom - plotHeight * tick / 4;
      svg.append(svgElement('line', { x1: plot.left, y1: y, x2: plot.right, y2: y, class: 'chart-gridline' }));
      addText((minimum + range * tick / 4).toLocaleString(undefined, { maximumFractionDigits: 1 }), plot.left - 8, y + 4, 'chart-axis-label', 'end');
    }
    const points = values.map((value, index) => ({
      x: plot.left + (values.length === 1 ? (plot.right - plot.left) / 2 : (plot.right - plot.left) * index / (values.length - 1)),
      y: plot.bottom - (value - minimum) / range * plotHeight,
      value,
    }));
    if (chart.type === 'line' || chart.type === 'area') {
      const linePath = points.map((point, index) => `${index ? 'L' : 'M'} ${point.x} ${point.y}`).join(' ');
      if (chart.type === 'area') {
        const zeroY = plot.bottom - (0 - minimum) / range * plotHeight;
        const areaPath = `${linePath} L ${points.at(-1).x} ${zeroY} L ${points[0].x} ${zeroY} Z`;
        svg.append(svgElement('path', { d: areaPath, class: 'chart-area' }));
      }
      svg.append(svgElement('path', { d: linePath, class: 'chart-line' }));
      points.forEach((point) => svg.append(svgElement('circle', { cx: point.x, cy: point.y, r: 4, class: 'chart-point' })));
    } else {
      const zeroY = plot.bottom - (0 - minimum) / range * plotHeight;
      points.forEach((point, index) => {
        const slotWidth = (plot.right - plot.left) / labels.length;
        const barWidth = chart.type === 'histogram' ? slotWidth - 3 : Math.min(48, slotWidth * 0.62);
        svg.append(svgElement('rect', { x: point.x - barWidth / 2, y: Math.min(point.y, zeroY), width: barWidth, height: Math.max(1, Math.abs(zeroY - point.y)), class: 'chart-column' }));
      });
    }
    labels.forEach((label, index) => {
      const x = points[index].x;
      const node = addText(label.slice(0, labels.length > 8 ? 11 : 18), x, 250, 'chart-axis-label');
      node.setAttribute('aria-label', label);
    });
    addText(chart.x_label || chart.dimension || 'Category', 380, 285);
    addText(`${chart.y_label || chart.metric || 'Value'}${chart.unit ? ` (${chart.unit})` : ''}`, 70, 15, 'chart-axis-label', 'start');
  }
  section.append(svg);
  parent.append(section);
}

function appendAnalysis(parent, analysis) {
  const panel = document.createElement('div');
  panel.className = 'chat-analysis-panel';
  if (typeof analysis.chart_notice === 'string' && analysis.chart_notice) {
    const notice = document.createElement('p');
    notice.className = 'chart-availability-notice';
    notice.setAttribute('role', 'note');
    notice.textContent = analysis.chart_notice;
    panel.append(notice);
  }
  appendChart(panel, analysis.chart);

  (Array.isArray(analysis.tables) ? analysis.tables : []).forEach((tableData) => {
    if (!Array.isArray(tableData.columns) || !Array.isArray(tableData.rows) || !tableData.columns.length) return;
    const section = document.createElement('section');
    section.className = 'analysis-section';
    const heading = document.createElement('h3');
    heading.textContent = tableData.title || 'Results';
    const wrapper = document.createElement('div');
    wrapper.className = 'analysis-table-scroll';
    const table = document.createElement('table');
    table.className = 'analysis-table';
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    tableData.columns.slice(0, 8).forEach((column) => {
      const cell = document.createElement('th');
      cell.scope = 'col';
      cell.textContent = String(column);
      headerRow.append(cell);
    });
    thead.append(headerRow);
    const tbody = document.createElement('tbody');
    tableData.rows.slice(0, 100).forEach((row) => {
      if (!Array.isArray(row)) return;
      const rowElement = document.createElement('tr');
      tableData.columns.slice(0, 8).forEach((_, index) => {
        const cell = document.createElement('td');
        cell.textContent = row[index] === null || row[index] === undefined ? '—' : String(row[index]);
        rowElement.append(cell);
      });
      tbody.append(rowElement);
    });
    table.append(thead, tbody);
    wrapper.append(table);
    section.append(heading, wrapper);
    panel.append(section);
  });

  [
    ['Verified findings', analysis.insights],
    ['Limitations', analysis.limitations],
  ].forEach(([title, items]) => {
    if (!Array.isArray(items) || !items.length) return;
    const section = document.createElement('section');
    section.className = 'analysis-section';
    const heading = document.createElement('h3');
    heading.textContent = title;
    const list = document.createElement('ul');
    list.className = 'analysis-list';
    items.slice(0, 3).forEach((item) => {
      if (typeof item !== 'string') return;
      const entry = document.createElement('li');
      entry.textContent = item;
      list.append(entry);
    });
    if (list.childElementCount) section.append(heading, list);
    if (section.childElementCount) panel.append(section);
  });

  if (panel.childElementCount) parent.append(panel);
}

function appendReportActions(parent, reportUrl, reportId, emailStatus) {
  const actions = document.createElement('div');
  actions.className = 'report-actions';
  const download = document.createElement('a');
  download.className = 'report-action-button';
  download.href = reportUrl;
  download.download = 'employee-insights-business-report.pdf';
  download.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M5 17v3h14v-3"></path></svg><span>Download PDF</span>';
  actions.append(download);

  const send = document.createElement('button');
  send.type = 'button';
  send.className = 'report-action-button';
  send.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16v12H4zM4 7l8 6 8-6"></path></svg><span>Send Report</span>';
  const status = document.createElement('p');
  status.className = 'report-action-status';
  send.addEventListener('click', () => sendReport(reportId, status, send));
  actions.append(send, status);
  parent.append(actions);
  setReportEmailStatus(status, emailStatus);
}

async function sendReport(reportId, status, button) {
  const email = reportEmail.value.trim();
  if (!email || !reportEmail.checkValidity()) {
    status.textContent = 'Enter a valid email address in Report Settings.';
    status.dataset.state = 'error';
    reportEmail.focus();
    if (!reportSettingsDialog.open) reportSettingsDialog.showModal();
    return;
  }
  button.disabled = true;
  status.textContent = 'Sending report to the email server…';
  status.dataset.state = 'progress';
  try {
    const response = await fetch(`/api/reports/${encodeURIComponent(reportId)}/email`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Send failed');
    status.textContent = 'SMTP accepted the report for delivery.';
    status.dataset.state = 'success';
    saveReportSettings();
  } catch (error) {
    status.textContent = error.message || 'I couldn’t send the report just now. Please try again.';
    status.dataset.state = 'error';
  } finally {
    button.disabled = false;
  }
}

function setReportEmailStatus(status, emailStatus) {
  if (!emailStatus || emailStatus === 'not_requested') {
    status.textContent = 'Report ready to download.';
    return;
  }
  const messages = {
    sent: ['SMTP accepted the report for delivery.', 'success'],
    invalid_email: ['Report created. Enter a valid email address for automatic delivery.', 'error'],
    not_configured: ['Report created, but email delivery is not available right now.', 'error'],
    failed: ['Report created, but email delivery failed. You can try Send Report again.', 'error'],
  };
  const [message, state] = messages[emailStatus] || ['Report ready to download.', ''];
  status.textContent = message;
  if (state) status.dataset.state = state;
}

function addStopAudioButton(parent, content) {
  stopAudio();
  if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
    setVoiceStatus('Audio playback isn’t available in this browser.');
    return;
  }
  const actions = document.createElement('div');
  actions.className = 'answer-actions';
  const stopButton = document.createElement('button');
  stopButton.type = 'button';
  stopButton.className = 'stop-audio-button';
  stopButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg><span>Stop audio</span>';
  stopButton.addEventListener('click', stopAudio);
  actions.append(stopButton);
  parent.append(actions);

  try {
    const utterance = new SpeechSynthesisUtterance(content);
    utterance.lang = navigator.language || 'en-US';
    utterance.onend = () => { stopButton.hidden = true; };
    utterance.onerror = () => { stopButton.hidden = true; };
    window.speechSynthesis.speak(utterance);
  } catch {
    stopButton.hidden = true;
    setVoiceStatus('Audio playback isn’t available right now.');
  }
}

function stopAudio() {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  document.querySelectorAll('.stop-audio-button').forEach((button) => { button.hidden = true; });
}

function appendConversationMessage(conversation, role, content, metadata = {}) {
  conversation.messages.push({ role, content, ...metadata });
  conversation.updatedAt = Date.now();
  saveConversations();
  renderHistory();
}

function renderConversation(conversation) {
  messages.replaceChildren();
  emptyState.hidden = conversation.messages.length > 0;
  conversation.messages.forEach((message) => {
    messages.append(message.role === 'user'
      ? createUserMessage(message.content)
      : createAssistantMessage(message.content, message));
  });
  messages.scrollTop = messages.scrollHeight;
}

function cancelActiveRequest() {
  activeRequest?.abort();
  activeRequest = null;
  sendButton.disabled = false;
}

function openConversation(id) {
  cancelActiveRequest();
  stopAudio();
  const conversation = conversations.find((item) => item.id === id);
  if (!conversation) return;
  currentConversationId = id;
  renderConversation(conversation);
  renderHistory();
  closeMobileSidebar();
  input.focus();
}

function startNewChat() {
  cancelActiveRequest();
  stopAudio();
  currentConversationId = null;
  nextQuestionIsVoice = false;
  messages.replaceChildren();
  emptyState.hidden = false;
  input.value = '';
  resizeInput();
  setVoiceStatus('');
  renderHistory();
  closeMobileSidebar();
  input.focus();
}

function setVoiceStatus(message) {
  voiceStatus.textContent = message;
}

function resizeInput() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

function scrollToLatest() {
  messages.scrollTop = messages.scrollHeight;
}

async function runRequirement(requirement, voiceOrigin) {
  let conversation = currentConversation();
  if (!conversation) conversation = makeConversation(requirement);
  const history = conversation.messages.slice(-maxHistoryMessages)
    .map(({ role, content }) => ({ role, content }));
  currentConversationId = conversation.id;
  emptyState.hidden = true;
  messages.append(createUserMessage(requirement));
  appendConversationMessage(conversation, 'user', requirement);
  input.value = '';
  resizeInput();
  setVoiceStatus('Analyzing results and preparing your report…');
  if (autoEmail.checked) {
    setVoiceStatus('Analyzing results, preparing your report, and sending it…');
  }

  const assistant = document.createElement('article');
  assistant.className = 'chat-message chat-assistant';
  const label = document.createElement('div');
  label.className = 'chat-message-label assistant-label';
  const mark = document.createElement('span');
  mark.className = 'assistant-mark';
  mark.setAttribute('aria-hidden', 'true');
  mark.textContent = 'E';
  const name = document.createElement('span');
  name.textContent = 'Employee Insights Assistant';
  label.append(mark, name);
  const loading = document.createElement('p');
  loading.className = 'chat-thinking';
  loading.textContent = 'Analyzing your results…';
  assistant.append(label, loading);
  messages.append(assistant);
  scrollToLatest();

  const controller = new AbortController();
  activeRequest = controller;
  sendButton.disabled = true;
  try {
    const response = await fetch('/api/requirement', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        requirement,
        history,
        auto_email: autoEmail.checked,
        email: autoEmail.checked ? reportEmail.value.trim() : '',
      }),
      signal: controller.signal,
    });
    if (activeRequest !== controller || currentConversationId !== conversation.id) return;
    const payload = await response.json();
    if (activeRequest !== controller || currentConversationId !== conversation.id) return;
    loading.remove();

    let reply;
    let error = false;
    if (!response.ok) {
      reply = typeof payload.error === 'string' && payload.error.trim()
        ? payload.error
        : 'I couldn’t complete that just now. Please try again.';
      error = true;
    } else if (payload.clarification) {
      reply = payload.clarification;
    } else if (payload.explanation) {
      reply = payload.explanation;
    } else {
      reply = 'I found the information, but couldn’t provide a clear answer just now. Please try again.';
      error = true;
    }

    setVoiceStatus('');
    const answerMetadata = {
      ...(payload.analysis ? { analysis: payload.analysis } : {}),
      ...(payload.report_id ? { reportId: payload.report_id } : {}),
      ...(payload.report_url ? { reportUrl: payload.report_url } : {}),
      ...(payload.email_status ? { emailStatus: payload.email_status } : {}),
    };
    if (payload.report_id) {
      latestReportId = payload.report_id;
      reportSettingsSend.disabled = false;
      reportSettingsStatus.textContent = payload.email_status === 'sent'
        ? 'SMTP accepted the report for delivery.'
        : 'A report is ready to send.';
      delete reportSettingsStatus.dataset.state;
    }
    const answer = createAssistantMessage(reply, {
      error,
      ...answerMetadata,
      speak: voiceOrigin && !error,
    });
    assistant.replaceWith(answer);
    appendConversationMessage(conversation, 'assistant', reply, answerMetadata);
    scrollToLatest();
  } catch (error) {
    if (error.name === 'AbortError' || activeRequest !== controller) return;
    loading.remove();
    const reply = 'I couldn’t complete that just now. Please try again.';
    setVoiceStatus('');
    const answer = createAssistantMessage(reply, { error: true });
    assistant.replaceWith(answer);
    appendConversationMessage(conversation, 'assistant', reply);
    scrollToLatest();
  } finally {
    if (activeRequest === controller) {
      activeRequest = null;
      sendButton.disabled = false;
    }
  }
}

function closeMobileSidebar() {
  if (mobileSidebar.matches) {
    shell.classList.remove('mobile-sidebar-open');
    updateSidebarButton();
  }
}

function toggleSidebar() {
  if (mobileSidebar.matches) {
    shell.classList.toggle('mobile-sidebar-open');
  } else {
    shell.classList.toggle('sidebar-collapsed');
  }
  updateSidebarButton();
}

function updateSidebarButton() {
  if (mobileSidebar.matches) {
    shell.classList.remove('sidebar-collapsed');
    const isOpen = shell.classList.contains('mobile-sidebar-open');
    sidebarToggle.setAttribute('aria-label', isOpen ? 'Close sidebar' : 'Open sidebar');
    sidebarToggle.setAttribute('aria-expanded', String(isOpen));
    sidebarCollapse.setAttribute('aria-label', isOpen ? 'Close sidebar' : 'Open sidebar');
    sidebarCollapse.title = isOpen ? 'Close sidebar' : 'Open sidebar';
  } else {
    shell.classList.remove('mobile-sidebar-open');
    const isExpanded = !shell.classList.contains('sidebar-collapsed');
    sidebarToggle.setAttribute('aria-label', isExpanded ? 'Collapse sidebar' : 'Expand sidebar');
    sidebarToggle.setAttribute('aria-expanded', String(isExpanded));
    sidebarCollapse.setAttribute('aria-label', isExpanded ? 'Collapse sidebar' : 'Expand sidebar');
    sidebarCollapse.title = isExpanded ? 'Collapse sidebar' : 'Expand sidebar';
  }
}

function toggleRecording() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    setVoiceStatus('Voice input isn’t available in this browser. You can type your question instead.');
    return;
  }
  if (recording && recognition) {
    recognition.stop();
    return;
  }

  recognition = new Recognition();
  recognition.lang = navigator.language || 'en-US';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.onstart = () => {
    recording = true;
    micButton.classList.add('is-recording');
    micButton.setAttribute('aria-pressed', 'true');
    micButton.setAttribute('aria-label', 'Stop voice input');
    micButton.title = 'Stop voice input';
    setVoiceStatus('Listening…');
  };
  recognition.onresult = (event) => {
    const transcript = event.results?.[0]?.[0]?.transcript?.trim();
    if (!transcript) {
      setVoiceStatus('I didn’t catch that. Please try again.');
      return;
    }
    input.value = transcript;
    resizeInput();
    nextQuestionIsVoice = true;
    setVoiceStatus('Sending your question…');
    form.requestSubmit();
  };
  recognition.onerror = () => {
    setVoiceStatus('Voice input couldn’t start. You can type your question instead.');
  };
  recognition.onend = () => {
    recording = false;
    micButton.classList.remove('is-recording');
    micButton.setAttribute('aria-pressed', 'false');
    micButton.setAttribute('aria-label', 'Start voice input');
    micButton.title = 'Start voice input';
    if (voiceStatus.textContent === 'Listening…') setVoiceStatus('');
  };
  try {
    recognition.start();
  } catch {
    recording = false;
    setVoiceStatus('Voice input couldn’t start. You can type your question instead.');
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const requirement = input.value.trim();
  const voiceOrigin = nextQuestionIsVoice;
  nextQuestionIsVoice = false;
  if (!requirement || activeRequest) return;
  stopAudio();
  runRequirement(requirement, voiceOrigin);
});

reportEmail.addEventListener('input', saveReportSettings);
autoEmail.addEventListener('change', saveReportSettings);
reportSettingsTrigger.addEventListener('click', () => reportSettingsDialog.showModal());
reportSettingsSend.addEventListener('click', () => {
  if (!latestReportId) {
    reportSettingsStatus.textContent = 'Create a report before sending it.';
    reportSettingsStatus.dataset.state = 'error';
    return;
  }
  sendReport(latestReportId, reportSettingsStatus, reportSettingsSend);
});
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});
input.addEventListener('input', resizeInput);
micButton.addEventListener('click', toggleRecording);
newChatButton.addEventListener('click', startNewChat);
sidebarToggle.addEventListener('click', toggleSidebar);
sidebarCollapse.addEventListener('click', toggleSidebar);
sidebarBackdrop.addEventListener('click', closeMobileSidebar);
mobileSidebar.addEventListener('change', updateSidebarButton);

renderHistory();
loadReportSettings();
reportSettingsSend.disabled = true;
updateSidebarButton();
resizeInput();
