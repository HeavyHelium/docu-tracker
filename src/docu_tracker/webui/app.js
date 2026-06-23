const state = {
  documents: [],
  notebookNotes: [],
  selectedNoteId: null,
  notebookReferenceSearch: "",
  topics: [],
  statuses: [],
  scanPaths: [],
  waitingToScan: null,
  waitingToScanLoading: false,
  oldestWaitingModifiedAt: null,
  waitingToScanPollId: null,
  selectedId: null,
  activity: [],
  analyticsTimeframe: "8w",
  filters: {
    search: "",
    status: "",
    topics: [],
  },
  viewMode: "list",
  networkInstance: null,
  freezePhysics: false,
};

const WAITING_TO_SCAN_POLL_MS = 15 * 60 * 1000;
const DETAIL_AUTOSAVE_DELAY_MS = 700;
const MAX_NOTEBOOK_IMAGE_BYTES = 10 * 1024 * 1024;
let detailAutosaveTimer = null;
let detailAutosaveRequestId = 0;
let saveButtonResetTimer = null;
let notebookAutosaveTimer = null;
let notebookSaveStatusResetTimer = null;

const STATUS_VISUALS = {
  unread: { label: "Unread", color: "#e6b146" },
  reading: { label: "Reading", color: "#0b6e62" },
  read: { label: "Read", color: "#4a8a6f" },
  needs_review: { label: "Needs review", color: "#bf5f3e" },
};

const browserSessionId = (() => {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
})();

const els = {
  docsBody: document.getElementById("docs-body"),
  docCount: document.getElementById("doc-count"),
  clearDuplicatesAll: document.getElementById("clear-duplicates-all"),
  hardDeleteDuplicatesAll: document.getElementById("hard-delete-duplicates-all"),
  pruneMissingFiles: document.getElementById("prune-missing-files"),
  filterSearch: document.getElementById("filter-search"),
  filterStatus: document.getElementById("filter-status"),
  filterTopic: document.getElementById("filter-topic"),
  filterTopicSummary: document.getElementById("filter-topic-summary"),
  filterTopicOptions: document.getElementById("filter-topic-options"),
  scanPath: document.getElementById("scan-path"),
  scanSince: document.getElementById("scan-since"),
  scanButton: document.getElementById("scan-button"),
  rescanButton: document.getElementById("rescan-button"),
  duplicateScanButton: document.getElementById("duplicate-scan-button"),
  detailHeading: document.getElementById("detail-heading"),
  detailEmpty: document.getElementById("detail-empty"),
  detailForm: document.getElementById("detail-form"),
  saveDocument: document.getElementById("save-document"),
  detailTitle: document.getElementById("detail-title"),
  detailAuthors: document.getElementById("detail-authors"),
  detailStatus: document.getElementById("detail-status"),
  detailSummary: document.getElementById("detail-summary"),
  detailTopics: document.getElementById("detail-topics"),
  detailModified: document.getElementById("detail-modified"),
  detailScanned: document.getElementById("detail-scanned"),
  detailPaths: document.getElementById("detail-paths"),
  detailOpen: document.getElementById("detail-open"),
  detailRescan: document.getElementById("detail-rescan"),
  detailClearDuplicates: document.getElementById("detail-clear-duplicates"),
  detailHardDeleteDuplicates: document.getElementById("detail-hard-delete-duplicates"),
  topicsList: document.getElementById("topics-list"),
  topicCreate: document.getElementById("topic-create"),
  newTopicName: document.getElementById("new-topic-name"),
  newTopicDescription: document.getElementById("new-topic-description"),
  statDocs: document.getElementById("stat-docs"),
  statReading: document.getElementById("stat-reading"),
  statReview: document.getElementById("stat-review"),
  statDuplicates: document.getElementById("stat-duplicates"),
  statWaitingScan: document.getElementById("stat-waiting-scan"),
  heroStatusChart: document.getElementById("hero-status-chart"),
  heroDonutValue: document.getElementById("hero-donut-value"),
  heroStatusLegend: document.getElementById("hero-status-legend"),
  heroTopicBars: document.getElementById("hero-topic-bars"),
  heroTimeframe: document.getElementById("hero-timeframe"),
  heroLineChart: document.getElementById("hero-line-chart"),
  heroVisual: document.querySelector(".hero"),
  heroTooltip: document.getElementById("hero-tooltip"),
  activityLog: document.getElementById("activity-log"),
  flash: document.getElementById("flash"),
  viewToggleBtn: document.getElementById("view-toggle-btn"),
  notebookToggleBtn: document.getElementById("notebook-toggle-btn"),
  graphContainer: document.getElementById("graph-container"),
  notebookContainer: document.getElementById("notebook-container"),
  tablePanel: document.querySelector(".table-panel"),
  freezePhysicsCheckbox: document.getElementById("freeze-physics-checkbox"),
};

function showFlash(message, kind = "info") {
  els.flash.textContent = message;
  els.flash.className = `flash flash-${kind}`;
  els.flash.classList.remove("hidden");
  window.clearTimeout(showFlash.timeout);
  showFlash.timeout = window.setTimeout(() => {
    els.flash.classList.add("hidden");
  }, 3000);
}

async function api(path, options = {}) {
  const config = {
    headers: { "Content-Type": "application/json" },
    ...options,
  };
  if (config.body && typeof config.body !== "string") {
    config.body = JSON.stringify(config.body);
  }
  const response = await fetch(path, config);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

async function loadState(keepSelection = true) {
  const payload = await api("/api/state");
  state.documents = payload.documents;
  state.topics = payload.topics;
  state.statuses = payload.statuses;
  state.scanPaths = payload.scan_paths;
  state.notebookNotes = payload.notebook_notes || [];
  state.waitingToScan = null;
  state.oldestWaitingModifiedAt = null;
  state.waitingToScanLoading = true;

  if (keepSelection && state.selectedId) {
    const stillExists = state.documents.some((doc) => doc.id === state.selectedId);
    if (!stillExists) state.selectedId = null;
  }

  renderFilters();
  renderStats();
  renderDocuments();
  renderNotebook();
  renderDetail();
  renderTopics();
  loadWaitingToScan();
}

async function loadWaitingToScan({ showLoading = true } = {}) {
  if (showLoading) {
    state.waitingToScan = null;
    state.oldestWaitingModifiedAt = null;
    state.waitingToScanLoading = true;
    renderStats();
  }

  try {
    const payload = await api("/api/stats/waiting-to-scan");
    state.waitingToScan = payload.waiting_to_scan;
    state.oldestWaitingModifiedAt = payload.oldest_waiting_modified_at || null;
    state.waitingToScanLoading = false;
  } catch (error) {
    state.waitingToScanLoading = false;
  }

  renderStats();
}

async function openBrowserSession() {
  await api("/api/session/open", {
    method: "POST",
    body: { session_id: browserSessionId },
  });
}

function closeBrowserSession() {
  const payload = JSON.stringify({ session_id: browserSessionId });
  if (navigator.sendBeacon) {
    const blob = new Blob([payload], { type: "application/json" });
    navigator.sendBeacon("/api/session/close", blob);
    return;
  }
  fetch("/api/session/close", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload,
    keepalive: true,
  }).catch(() => {});
}

function renderFilters() {
  setSelectOptions(els.filterStatus, [{ value: "", label: "All statuses" }].concat(
    state.statuses.map((status) => ({ value: status, label: status }))
  ), state.filters.status);

  els.filterTopicOptions.innerHTML = state.topics.map((topic) => `
    <label class="multi-select-option">
      <input type="checkbox" value="${escapeHtml(topic.name)}" ${state.filters.topics.includes(topic.name) ? "checked" : ""}>
      <span>${escapeHtml(topic.name)}</span>
    </label>
  `).join("");
  els.filterTopicSummary.textContent = topicFilterLabel();

  setSelectOptions(els.scanPath, [{ value: "", label: "Configured paths" }].concat(
    state.scanPaths.map((path) => ({ value: path, label: path }))
  ), els.scanPath.value);

  setSelectOptions(els.detailStatus, state.statuses.map((status) => ({
    value: status,
    label: status,
  })), els.detailStatus.value);
}

function setSelectOptions(select, options, selectedValue) {
  const current = selectedValue ?? select.value;
  select.innerHTML = options.map((option) => `
    <option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>
  `).join("");
  select.value = options.some((option) => option.value === current) ? current : options[0]?.value || "";
}

function renderStats() {
  const totalDocs = state.documents.length;
  const readingDocs = state.documents.filter((doc) => doc.status === "reading").length;
  const reviewDocs = state.documents.filter((doc) => doc.status === "needs_review").length;
  const duplicatePaths = duplicatePathCount();
  els.statDocs.textContent = String(totalDocs);
  els.statReading.textContent = String(readingDocs);
  els.statReview.textContent = String(reviewDocs);
  els.statDuplicates.textContent = String(duplicatePaths);
  const duplicateTooltip = duplicatePaths + " duplicate tracked path" + (duplicatePaths === 1 ? "" : "s");
  els.statDuplicates.title = duplicateTooltip;
  const duplicateCard = els.statDuplicates.closest(".stat-card");
  if (duplicateCard) duplicateCard.title = duplicateTooltip;
  els.statWaitingScan.textContent = state.waitingToScanLoading
    ? "..."
    : String(state.waitingToScan ?? 0);
  els.statWaitingScan.classList.toggle("stat-loading", state.waitingToScanLoading);
  const waitingTooltip = waitingScanTooltip();
  els.statWaitingScan.title = waitingTooltip;
  const waitingCard = els.statWaitingScan.closest(".stat-card");
  if (waitingCard) waitingCard.title = waitingTooltip;
  renderHeroVisuals();
}

function renderHeroVisuals() {
  const filteredDocs = documentsForAnalyticsWindow();
  const totalDocs = filteredDocs.length;
  const statusOrder = ["unread", "reading", "read", "needs_review"];
  const statusCounts = Object.fromEntries(
    statusOrder.map((status) => [
      status,
      filteredDocs.filter((doc) => doc.status === status).length,
    ])
  );

  const gradientStops = [];
  let start = 0;
  for (const status of statusOrder) {
    const count = statusCounts[status];
    const degrees = totalDocs ? (count / totalDocs) * 360 : 0;
    const end = start + degrees;
    if (degrees > 0) {
      gradientStops.push(`${STATUS_VISUALS[status].color} ${start}deg ${end}deg`);
    }
    start = end;
  }
  if (!gradientStops.length) {
    gradientStops.push("rgba(36, 53, 48, 0.08) 0deg 360deg");
  }

  els.heroStatusChart.style.background = `conic-gradient(${gradientStops.join(", ")})`;
  els.heroStatusChart.dataset.tooltip = `${totalDocs} documents in ${timeframeLabel()}`;
  els.heroDonutValue.textContent = String(totalDocs);
  els.heroStatusLegend.innerHTML = statusOrder.map((status) => {
    const count = statusCounts[status];
    const share = totalDocs ? Math.round((count / totalDocs) * 100) : 0;
    return `
      <div class="hero-status-item" data-tooltip="${escapeAttribute(`${STATUS_VISUALS[status].label}: ${count} documents (${share}%)`)}">
        <span class="hero-status-dot" style="background:${STATUS_VISUALS[status].color}"></span>
        <div>
          <strong>${STATUS_VISUALS[status].label}</strong>
          <small>${count} · ${share}%</small>
        </div>
      </div>
    `;
  }).join("");

  const topicCounts = new Map();
  for (const doc of filteredDocs) {
    for (const topic of doc.topics) {
      topicCounts.set(topic, (topicCounts.get(topic) || 0) + 1);
    }
  }
  const topTopics = Array.from(topicCounts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 4);
  const maxTopicCount = topTopics[0]?.[1] || 1;

  if (!topTopics.length) {
    els.heroTopicBars.innerHTML = `<div class="hero-topic-empty">No topic data yet.</div>`;
  } else {
    els.heroTopicBars.innerHTML = topTopics.map(([topic, count]) => {
      const width = `${Math.max(24, Math.round((count / maxTopicCount) * 100))}%`;
      return `
        <div class="hero-topic-row" data-tooltip="${escapeAttribute(`${topic}: ${count} documents in ${timeframeLabel()}`)}">
          <div class="hero-topic-meta">
            <span>${escapeHtml(topic)}</span>
            <strong>${count}</strong>
          </div>
          <div class="hero-topic-track">
            <div class="hero-topic-fill" style="${topicColorStyle(topic)} width:${width}"></div>
          </div>
        </div>
      `;
    }).join("");
  }

  renderWeeklyLineChart(filteredDocs);
}

function documentsForAnalyticsWindow() {
  if (state.analyticsTimeframe === "all") {
    return state.documents.filter((doc) => parseDocumentDate(doc));
  }

  const weeks = Number.parseInt(state.analyticsTimeframe, 10);
  if (Number.isNaN(weeks)) {
    return state.documents.filter((doc) => parseDocumentDate(doc));
  }

  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - weeks * 7);
  return state.documents.filter((doc) => {
    const date = parseDocumentDate(doc);
    return date && date >= cutoff;
  });
}

function parseDocumentDate(doc) {
  if (!doc?.file_modified_at) return null;
  const date = new Date(doc.file_modified_at);
  return Number.isNaN(date.getTime()) ? null : date;
}

function startOfWeek(date) {
  const value = new Date(date);
  value.setHours(0, 0, 0, 0);
  value.setDate(value.getDate() - value.getDay() + 1);
  return value;
}

function weekKey(date) {
  return date.toISOString().slice(0, 10);
}

function formatWeekTick(dateString) {
  const date = new Date(dateString);
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function buildWeeklySeries(filteredDocs) {
  const datedDocs = filteredDocs
    .map((doc) => ({ doc, date: parseDocumentDate(doc) }))
    .filter((entry) => entry.date)
    .sort((a, b) => a.date - b.date);

  if (!datedDocs.length) return [];

  const firstWeek = startOfWeek(datedDocs[0].date);
  const lastWeek = startOfWeek(datedDocs[datedDocs.length - 1].date);
  const weeklyStats = new Map();

  for (const entry of datedDocs) {
    const key = weekKey(startOfWeek(entry.date));
    if (!weeklyStats.has(key)) {
      weeklyStats.set(key, { count: 0, topics: new Map() });
    }
    const bucket = weeklyStats.get(key);
    bucket.count += 1;
    for (const topic of entry.doc.topics) {
      bucket.topics.set(topic, (bucket.topics.get(topic) || 0) + 1);
    }
  }

  const series = [];
  const cursor = new Date(firstWeek);
  while (cursor <= lastWeek) {
    const key = weekKey(cursor);
    const bucket = weeklyStats.get(key);
    const topTopics = bucket
      ? Array.from(bucket.topics.entries())
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
        .slice(0, 3)
      : [];
    series.push({
      key,
      count: bucket?.count || 0,
      topTopics,
    });
    cursor.setDate(cursor.getDate() + 7);
  }
  return series;
}

function weeklyTooltip(point) {
  const heading = `${formatWeekTick(point.key)} week`;
  if (!point.topTopics.length) {
    return `${heading}\n${point.count} documents\nNo topics`;
  }
  const topics = point.topTopics
    .map(([topic, count]) => `${topic} (${count})`)
    .join(", ");
  return `${heading}\n${point.count} documents\nTop topics: ${topics}`;
}

function renderWeeklyLineChart(filteredDocs) {
  const series = buildWeeklySeries(filteredDocs);
  if (!series.length) {
    els.heroLineChart.innerHTML = `<div class="hero-topic-empty">No dated documents in this window.</div>`;
    return;
  }

  const width = 520;
  const height = 190;
  const padding = { top: 18, right: 18, bottom: 34, left: 22 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const maxCount = Math.max(...series.map((point) => point.count), 1);

  const points = series.map((point, index) => {
    const x = padding.left + (series.length === 1 ? plotWidth / 2 : (index / (series.length - 1)) * plotWidth);
    const y = padding.top + plotHeight - (point.count / maxCount) * plotHeight;
    return { ...point, x, y };
  });

  const linePath = points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
  const areaPath = `${linePath} L ${points[points.length - 1].x} ${padding.top + plotHeight} L ${points[0].x} ${padding.top + plotHeight} Z`;
  const tickStep = Math.max(1, Math.ceil(series.length / 4));
  const yTicks = [0, Math.ceil(maxCount / 2), maxCount].filter((value, index, values) => values.indexOf(value) === index);

  els.heroLineChart.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" class="hero-line-svg" role="img" aria-label="Weekly document counts">
      ${yTicks.map((tick) => {
        const y = padding.top + plotHeight - (tick / maxCount) * plotHeight;
        return `
          <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" class="hero-grid-line"></line>
          <text x="${padding.left - 8}" y="${y + 4}" class="hero-axis-label hero-axis-label-y">${tick}</text>
        `;
      }).join("")}
      <path d="${areaPath}" class="hero-area-fill"></path>
      <path d="${linePath}" class="hero-line-path"></path>
      ${points.map((point) => `
        <circle
          cx="${point.x}"
          cy="${point.y}"
          r="5"
          class="hero-line-point"
          data-tooltip="${escapeAttribute(weeklyTooltip(point))}"
        ></circle>
      `).join("")}
      ${points.map((point, index) => index % tickStep === 0 || index === points.length - 1 ? `
        <text x="${point.x}" y="${height - 8}" class="hero-axis-label" text-anchor="middle">${formatWeekTick(point.key)}</text>
      ` : "").join("")}
    </svg>
  `;
}

function timeframeLabel() {
  const labels = {
    "4w": "4 weeks",
    "8w": "8 weeks",
    "12w": "12 weeks",
    "24w": "24 weeks",
    all: "all time",
  };
  return labels[state.analyticsTimeframe] || state.analyticsTimeframe;
}

function topicFilterLabel() {
  if (!state.filters.topics.length) return "All topics";
  if (state.filters.topics.length === 1) return state.filters.topics[0];
  return `${state.filters.topics.length} topics`;
}

function getVisibleDocuments() {
  const term = state.filters.search.trim().toLowerCase();
  return state.documents.filter((doc) => {
    if (state.filters.status && doc.status !== state.filters.status) return false;
    if (state.filters.topics.length && !state.filters.topics.some((topic) => doc.topics.includes(topic))) return false;
    if (!term) return true;
    const haystack = [
      doc.title,
      doc.authors,
      doc.summary,
      doc.source,
      ...doc.paths,
      ...doc.topics,
    ].join(" ").toLowerCase();
    return haystack.includes(term);
  });
}

function duplicatePathCount(docs = state.documents) {
  return docs.reduce((total, doc) => total + Math.max(0, (doc.paths?.length || 0) - 1), 0);
}

function renderDocuments() {
  const docs = getVisibleDocuments();
  const totalDuplicatePaths = duplicatePathCount();
  els.docCount.textContent = docs.length + " visible";
  els.clearDuplicatesAll.disabled = totalDuplicatePaths === 0;
  els.hardDeleteDuplicatesAll.disabled = totalDuplicatePaths === 0;
  if (state.viewMode === "graph") {
    renderGraph();
  }
  if (state.viewMode === "notebook") {
    renderNotebook();
  }
  if (!docs.length) {
    els.docsBody.innerHTML = `
      <tr>
        <td colspan="7">
          <div class="empty-state">No documents match the current filters.</div>
        </td>
      </tr>
    `;
    return;
  }
  els.docsBody.innerHTML = docs.map((doc) => `
    <tr data-doc-id="${doc.id}" class="${doc.id === state.selectedId ? "selected" : ""}">
      <td>${doc.id}</td>
      <td class="title-cell">
        <strong>${escapeHtml(doc.title)}</strong>
        <span>${escapeHtml(truncateAuthors(doc.authors))}</span>
      </td>
      <td>
        <div class="topic-tags">
          ${doc.topics.map((topic) => `<span class="topic-tag" style="${topicColorStyle(topic)}">${escapeHtml(topic)}</span>`).join("")}
        </div>
      </td>
      <td>
        <select data-status-id="${doc.id}" class="status-select status-${escapeHtml(doc.status)}">
          ${state.statuses.map((status) => `
            <option value="${escapeHtml(status)}" ${status === doc.status ? "selected" : ""}>
              ${escapeHtml(status)}
            </option>
          `).join("")}
        </select>
      </td>
      <td>${escapeHtml(doc.source || "-")}</td>
      <td>${formatDate(doc.file_modified_at)}</td>
      <td class="row-actions">
        <div class="row-actions-cell">
          <button data-open-id="${doc.id}" type="button" class="action-open">Open</button>
          <button data-rescan-id="${doc.id}" type="button" class="action-rescan">Rescan</button>
        </div>
      </td>
    </tr>
  `).join("");
}

function renderDetail() {
  const doc = state.documents.find((item) => item.id === state.selectedId);
  if (!doc) {
    els.detailHeading.textContent = "Select a document";
    els.detailForm.classList.add("hidden");
    els.detailEmpty.classList.remove("hidden");
    els.saveDocument.textContent = "Save";
    els.saveDocument.disabled = true;
    els.detailClearDuplicates.classList.add("hidden");
    els.detailClearDuplicates.disabled = true;
    els.detailHardDeleteDuplicates.classList.add("hidden");
    els.detailHardDeleteDuplicates.disabled = true;
    return;
  }

  window.clearTimeout(detailAutosaveTimer);
  clearSaveButtonResetTimer();
  detailAutosaveRequestId += 1;
  els.detailHeading.textContent = "#" + doc.id + " " + doc.title;
  els.detailForm.classList.remove("hidden");
  els.detailEmpty.classList.add("hidden");
  els.saveDocument.textContent = "Save";
  els.saveDocument.disabled = false;
  els.detailTitle.value = doc.title;
  els.detailAuthors.value = doc.authors;
  els.detailStatus.value = doc.status;
  els.detailStatus.className = `status-select status-${doc.status}`;
  els.detailSummary.value = doc.summary;
  els.detailModified.textContent = formatDateTime(doc.file_modified_at);
  els.detailScanned.textContent = formatDateTime(doc.scanned_at);
  els.detailPaths.innerHTML = doc.paths.map((path, index) => {
    const action = index === 0
      ? "<span class=\"path-badge\">Primary</span>"
      : "<div class=\"path-actions\"><button type=\"button\" class=\"path-clear-button\" data-clear-path-index=\"" + index + "\">Clear</button><button type=\"button\" class=\"path-delete-button\" data-delete-path-index=\"" + index + "\">Delete Duplicate</button></div>";
    return "<li class=\"path-item\"><span class=\"path-value\">"
      + escapeHtml(path)
      + "</span>"
      + action
      + "</li>";
  }).join("");
  els.detailClearDuplicates.classList.toggle("hidden", doc.paths.length <= 1);
  els.detailClearDuplicates.disabled = doc.paths.length <= 1;
  els.detailHardDeleteDuplicates.classList.toggle("hidden", doc.paths.length <= 1);
  els.detailHardDeleteDuplicates.disabled = doc.paths.length <= 1;
  els.detailTopics.innerHTML = state.topics.map((topic) => `
    <label class="topic-option" style="${topicColorStyle(topic.name)}">
      <input type="checkbox" value="${escapeHtml(topic.name)}" ${doc.topics.includes(topic.name) ? "checked" : ""}>
      <span>
        <strong>${escapeHtml(topic.name)}</strong><br>
        <span class="topic-note">${escapeHtml(topic.description || "No description")}</span>
      </span>
    </label>
  `).join("");
}

function renderTopics() {
  els.topicsList.innerHTML = state.topics.map((topic) => `
    <div class="topic-card" data-topic-name="${escapeHtml(topic.name)}" style="${topicColorStyle(topic.name)}">
      <div class="topic-card-header">
        <strong>${escapeHtml(topic.name)}</strong>
        <div class="topic-card-actions">
          <button type="button" data-topic-save="${escapeHtml(topic.name)}">Save</button>
          <button type="button" data-topic-delete="${escapeHtml(topic.name)}" ${topic.name === "Other" ? "disabled" : ""}>Delete</button>
        </div>
      </div>
      <label>
        Name
        <input data-topic-name-input="${escapeHtml(topic.name)}" type="text" value="${escapeHtml(topic.name)}" ${topic.name === "Other" ? "disabled" : ""}>
      </label>
      <label>
        Description
        <input data-topic-description-input="${escapeHtml(topic.name)}" type="text" value="${escapeHtml(topic.description || "")}">
      </label>
    </div>
  `).join("");
}


function selectedNotebookNote() {
  return state.notebookNotes.find((note) => note.id === state.selectedNoteId) || null;
}

function noteReferencedDocuments(note) {
  const ids = new Set(note?.document_ids || []);
  return state.documents.filter((doc) => ids.has(doc.id));
}

function notebookExcerpt(note) {
  const body = (note.body || "").trim().replace(/\s+/g, " ");
  if (!body) return "No notes yet";
  return body.length > 120 ? body.slice(0, 117) + "..." : body;
}

function renderNotebook() {
  if (!state.selectedNoteId && state.notebookNotes.length) {
    state.selectedNoteId = state.notebookNotes[0].id;
  }
  if (state.selectedNoteId && !state.notebookNotes.some((note) => note.id === state.selectedNoteId)) {
    state.selectedNoteId = state.notebookNotes[0]?.id || null;
  }

  const note = selectedNotebookNote();
  els.notebookContainer.innerHTML = `
    <div class="notebook-layout">
      ${note ? renderNotebookReferenceSearch(note) : `<section class="notebook-reference-strip"><div class="empty-state">Create or select a note to search file references.</div></section>`}
      <div class="notebook-panes">
        <section class="notebook-list-panel">
          <div class="notebook-panel-heading">
            <div>
              <p class="section-kicker">Notebook</p>
              <h3>Research Notes</h3>
            </div>
            <button id="notebook-new" class="button button-primary" type="button">New</button>
          </div>
          <div class="notebook-note-list">
            ${state.notebookNotes.length ? state.notebookNotes.map((item) => `
              <button type="button" class="notebook-note-card ${item.id === state.selectedNoteId ? "selected" : ""}" data-note-id="${item.id}">
                <strong>${escapeHtml(item.title || "Untitled note")}</strong>
                <span>${escapeHtml(notebookExcerpt(item))}</span>
                <small>${item.document_ids.length} reference${item.document_ids.length === 1 ? "" : "s"} · ${formatDateTime(item.updated_at)}</small>
              </button>
            `).join("") : `<div class="empty-state">Create a note to start a research map around the files in this tracker.</div>`}
          </div>
        </section>
        <div class="notebook-resizer" data-resizer="notebook-list" role="separator" aria-orientation="vertical" aria-label="Resize note list"></div>
        <section class="notebook-editor-panel">
          ${note ? renderNotebookEditor(note) : renderNotebookEmptyEditor()}
        </section>
      </div>
    </div>
  `;
}


function renderNotebookEmptyEditor() {
  return `
    <div class="notebook-empty-editor empty-state">
      <strong>No notebook entry selected.</strong>
      <span>Use New Note to create a durable research note that can reference tracked files.</span>
    </div>
  `;
}



function safeMarkdownUrl(value) {
  const trimmed = String(value || "").trim();
  const lowered = trimmed.toLowerCase();
  if (lowered.startsWith("javascript:") || lowered.startsWith("data:")) return "#";
  return trimmed;
}

function markdownToolbarHtml() {
  const tools = [
    ["bold", "B"],
    ["italic", "I"],
    ["h1", "H1"],
    ["h2", "H2"],
    ["list", "List"],
    ["task", "Task"],
    ["quote", "Quote"],
    ["inline-code", "</>"],
    ["code-block", "Code block"],
    ["link", "Link"],
    ["inline-math", "pi"],
    ["display-math", "$$"],
    ["image", "Image"],
    ["rule", "-"],
  ];
  return `
    <div class="notebook-markdown-toolbar" aria-label="Markdown formatting">
      ${tools.map(([action, label]) => `
        <button type="button" data-markdown-action="${action}" title="${escapeAttribute(label)}">${escapeHtml(label)}</button>
      `).join("")}
    </div>
  `;
}

function selectedTextareaRange(textarea) {
  return {
    start: textarea.selectionStart,
    end: textarea.selectionEnd,
    selected: textarea.value.slice(textarea.selectionStart, textarea.selectionEnd),
  };
}

function replaceTextareaRange(textarea, nextValue, selectionStart, selectionEnd, options = {}) {
  const { autosave = true } = options;
  const { start, end } = selectedTextareaRange(textarea);
  textarea.value = textarea.value.slice(0, start) + nextValue + textarea.value.slice(end);
  textarea.focus();
  textarea.setSelectionRange(start + selectionStart, start + selectionEnd);
  updateNotebookPreview();
  if (autosave) scheduleNotebookAutosave();
}

function prefixSelectedLines(textarea, prefix) {
  const { start, end } = selectedTextareaRange(textarea);
  const lineStart = textarea.value.lastIndexOf("\n", start - 1) + 1;
  const lineEndIndex = textarea.value.indexOf("\n", end);
  const lineEnd = lineEndIndex === -1 ? textarea.value.length : lineEndIndex;
  const block = textarea.value.slice(lineStart, lineEnd);
  const nextBlock = block.split("\n").map((line) => prefix + line).join("\n");
  textarea.value = textarea.value.slice(0, lineStart) + nextBlock + textarea.value.slice(lineEnd);
  textarea.focus();
  textarea.setSelectionRange(lineStart, lineStart + nextBlock.length);
  updateNotebookPreview();
  scheduleNotebookAutosave();
}

function applyMarkdownToolbarAction(action) {
  const textarea = els.notebookContainer.querySelector("#notebook-body");
  if (!textarea) return;
  const { selected } = selectedTextareaRange(textarea);
  const text = selected || "text";

  if (action === "bold") return replaceTextareaRange(textarea, `**${text}**`, 2, 2 + text.length);
  if (action === "italic") return replaceTextareaRange(textarea, `*${text}*`, 1, 1 + text.length);
  if (action === "inline-code") return replaceTextareaRange(textarea, `\`${text}\``, 1, 1 + text.length);
  if (action === "inline-math") return replaceTextareaRange(textarea, `$${selected || "x"}$`, 1, 1 + (selected || "x").length);
  if (action === "h1") return prefixSelectedLines(textarea, "# ");
  if (action === "h2") return prefixSelectedLines(textarea, "## ");
  if (action === "list") return prefixSelectedLines(textarea, "- ");
  if (action === "task") return prefixSelectedLines(textarea, "- [ ] ");
  if (action === "quote") return prefixSelectedLines(textarea, "> ");
  if (action === "rule") return replaceTextareaRange(textarea, "\n---\n", 5, 5);
  if (action === "code-block") return replaceTextareaRange(textarea, `\n\`\`\`\n${selected || "code"}\n\`\`\`\n`, 5, 5 + (selected || "code").length);
  if (action === "display-math") return replaceTextareaRange(textarea, `\n$$\n${selected || "x = y"}\n$$\n`, 4, 4 + (selected || "x = y").length);
  if (action === "link") return replaceTextareaRange(textarea, `[${selected || "title"}](https://example.com)`, 1, 1 + (selected || "title").length);
  if (action === "image") return replaceTextareaRange(textarea, `![${selected || "image"}](https://example.com/image.png)`, 2, 2 + (selected || "image").length);
}

function imageMarkdownAlt(file, index = 0) {
  const raw = String(file?.name || `pasted-image-${index + 1}`)
    .replace(/\.[^.]+$/, "")
    .replace(/[-_]+/g, " ")
    .trim();
  return (raw || `Pasted image ${index + 1}`)
    .replaceAll("[", "\\[")
    .replaceAll("]", "\\]");
}

function imageMarkdownText(alt, url) {
  return `![${alt}](${url})`;
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error || new Error("Could not read pasted image"));
    reader.readAsDataURL(file);
  });
}

async function uploadNotebookImageAsDataUrl(file) {
  const dataUrl = await fileToDataUrl(file);
  return api("/api/notebook/attachments", {
    method: "POST",
    body: {
      data_url: dataUrl,
      name: file.name || "pasted-image",
    },
  });
}

async function uploadNotebookImage(file) {
  const response = await fetch(`/api/notebook/attachments?name=${encodeURIComponent(file.name || "pasted-image")}`, {
    method: "POST",
    headers: { "Content-Type": file.type || "image/png" },
    body: file,
  });
  const payload = await response.json().catch(() => ({}));
  if (response.ok) return payload;
  return uploadNotebookImageAsDataUrl(file);
}

function insertMarkdownAtCursor(textarea, markdown, options = {}) {
  const { start } = selectedTextareaRange(textarea);
  const leading = start > 0 && textarea.value[start - 1] !== "\n" ? "\n" : "";
  const trailing = textarea.value[start] && textarea.value[start] !== "\n" ? "\n" : "";
  const insertion = `${leading}${markdown}${trailing}`;
  replaceTextareaRange(textarea, insertion, insertion.length, insertion.length, options);
}

function replaceNotebookImageUrl(textarea, previewUrl, savedUrl) {
  if (!textarea.value.includes(previewUrl)) return false;
  textarea.value = textarea.value.replaceAll(previewUrl, savedUrl);
  updateNotebookPreview();
  scheduleNotebookAutosave();
  return true;
}

async function pasteNotebookImages(event) {
  if (!event.target.matches("#notebook-body")) return;
  const files = Array.from(event.clipboardData?.items || [])
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (!files.length) return;

  event.preventDefault();
  const oversized = files.find((file) => file.size > MAX_NOTEBOOK_IMAGE_BYTES);
  if (oversized) {
    showFlash(`${oversized.name || "This image"} is larger than 10 MB.`, "error");
    return;
  }

  const textarea = event.target;
  const uploads = files.map((file, index) => {
    const previewUrl = URL.createObjectURL(file);
    insertMarkdownAtCursor(textarea, imageMarkdownText(imageMarkdownAlt(file, index), previewUrl), { autosave: false });
    return uploadNotebookImage(file)
      .then((result) => {
        if (replaceNotebookImageUrl(textarea, previewUrl, result.url)) {
          URL.revokeObjectURL(previewUrl);
        }
      })
      .catch((error) => {
        showFlash(error.message, "error");
        setNotebookSaveStatus("Image upload failed", "error");
      });
  });

  showFlash(files.length === 1 ? "Image inserted." : `${files.length} images inserted.`);
  setNotebookSaveStatus("Uploading image...", "saving");
  await Promise.all(uploads);
}

function renderInlineMarkdown(value) {
  const codeSpans = [];
  let text = String(value ?? "").replace(/`([^`]+)`/g, (_match, code) => {
    const token = `@@CODE${codeSpans.length}@@`;
    codeSpans.push(`<code>${escapeHtml(code)}</code>`);
    return token;
  });

  text = escapeHtml(text);
  text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;([^&]*)&quot;)?\)/g, (_match, alt, url, title) => {
    const titleAttribute = title ? ` title="${escapeAttribute(title)}"` : "";
    return `<img src="${escapeAttribute(safeMarkdownUrl(url))}" alt="${escapeAttribute(alt)}"${titleAttribute} loading="lazy">`;
  });
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;([^&]*)&quot;)?\)/g, (_match, label, url, title) => {
    const titleAttribute = title ? ` title="${escapeAttribute(title)}"` : "";
    return `<a href="${escapeAttribute(safeMarkdownUrl(url))}"${titleAttribute} target="_blank" rel="noopener noreferrer">${label}</a>`;
  });
  text = text
    .replace(/\$([^$]+)\$/g, `<span class="math-inline">$1</span>`)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");

  for (const [index, code] of codeSpans.entries()) {
    text = text.replaceAll(`@@CODE${index}@@`, code);
  }
  return text;
}

function renderMarkdown(value) {
  const lines = String(value || "").split(/\r?\n/);
  const html = [];
  let paragraph = [];
  let listItems = [];
  let codeLines = [];
  let mathLines = [];
  let inCode = false;
  let inMath = false;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!listItems.length) return;
    html.push(`<ul>${listItems.map((item) => {
      if (item.task) {
        return `<li class="task-list-item"><input type="checkbox" disabled ${item.checked ? "checked" : ""}> <span>${renderInlineMarkdown(item.text)}</span></li>`;
      }
      return `<li>${renderInlineMarkdown(item.text)}</li>`;
    }).join("")}</ul>`);
    listItems = [];
  };
  const flushCode = () => {
    if (!codeLines.length) return;
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    codeLines = [];
  };
  const flushMath = () => {
    if (!mathLines.length) return;
    html.push(`<div class="math-display">${escapeHtml(mathLines.join("\n"))}</div>`);
    mathLines = [];
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      if (inCode) {
        flushCode();
        inCode = false;
      } else {
        flushParagraph();
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }

    if (trimmed === "$$" || trimmed === "\\[" || trimmed === "\\]") {
      if (inMath) {
        flushMath();
        inMath = false;
      } else {
        flushParagraph();
        flushList();
        inMath = true;
      }
      continue;
    }
    if (inMath) {
      mathLines.push(line);
      continue;
    }

    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }

    const oneLineMath = trimmed.match(/^\$\$\s*(.+?)\s*\$\$$/) || trimmed.match(/^\\\[\s*(.+?)\s*\\\]$/);
    if (oneLineMath) {
      flushParagraph();
      flushList();
      html.push(`<div class="math-display">${escapeHtml(oneLineMath[1])}</div>`);
      continue;
    }

    if (/^(-{3,}|={3,})$/.test(trimmed)) {
      flushParagraph();
      flushList();
      html.push("<hr>");
      continue;
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length + 2;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const task = trimmed.match(/^[-*]\s+\[([ xX])\]\s+(.+)$/);
    if (task) {
      flushParagraph();
      listItems.push({ task: true, checked: task[1].toLowerCase() === "x", text: task[2] });
      continue;
    }

    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      listItems.push({ task: false, text: bullet[1] });
      continue;
    }

    const quote = trimmed.match(/^>\s?(.+)$/);
    if (quote) {
      flushParagraph();
      flushList();
      html.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }

    flushList();
    paragraph.push(trimmed);
  }

  flushCode();
  flushMath();
  flushParagraph();
  flushList();
  return html.join("") || `<p class="notebook-preview-empty">Nothing to preview yet.</p>`;
}


function updateNotebookPreview() {
  const bodyInput = els.notebookContainer.querySelector("#notebook-body");
  const preview = els.notebookContainer.querySelector("#notebook-preview");
  if (!bodyInput || !preview) return;
  preview.innerHTML = renderMarkdown(bodyInput.value);
}


function renderNotebookEditor(note) {
  return `
    <div class="notebook-editor-header">
      <div>
        <p class="section-kicker notebook-entry-kicker">
          <span>Entry</span>
          <span id="notebook-save-status" class="notebook-save-status saved">Saved</span>
        </p>
        <input id="notebook-title" class="notebook-title-input" type="text" value="${escapeAttribute(note.title)}" placeholder="Note title">
      </div>
      <div class="notebook-editor-actions">
        <button id="notebook-save" class="button button-primary" type="button">Save</button>
        <button id="notebook-delete" class="button button-danger" type="button">Delete</button>
      </div>
    </div>
    ${markdownToolbarHtml()}
    <div class="notebook-compose">
      <label class="notebook-write-panel">
        <span class="section-kicker">Markdown</span>
        <textarea id="notebook-body" class="notebook-body-input" rows="18" placeholder="Write synthesis notes, claims, open questions, and links between files.">${escapeHtml(note.body)}</textarea>
      </label>
      <div class="notebook-resizer notebook-compose-resizer" data-resizer="notebook-compose" role="separator" aria-orientation="vertical" aria-label="Resize markdown preview"></div>
      <section class="notebook-preview-panel">
        <p class="section-kicker">Preview</p>
        <div id="notebook-preview" class="notebook-markdown-preview">${renderMarkdown(note.body)}</div>
      </section>
    </div>
    <div class="notebook-linked-files">
      <p class="section-kicker">Linked Files</p>
      ${noteReferencedDocuments(note).length ? noteReferencedDocuments(note).map((doc) => `
        <button type="button" class="notebook-linked-file" data-notebook-open-doc="${doc.id}">
          <strong>${escapeHtml(doc.title || `Document #${doc.id}`)}</strong>
          <span>${escapeHtml(doc.authors || doc.source || "No authors")}</span>
        </button>
      `).join("") : `<div class="empty-state">No files linked yet.</div>`}
    </div>
  `;
}

function documentMarkdownLink(doc) {
  const title = (doc.title || `Document #${doc.id}`).replaceAll("[", "\\[").replaceAll("]", "\\]");
  const url = new URL(`/api/documents/${doc.id}/open`, window.location.href).href;
  return `[${title}](${url})`;
}

function referenceSearchMatches(doc, term) {
  if (!term) return true;
  const haystack = [
    doc.title,
    doc.authors,
    doc.summary,
    doc.status,
    doc.source,
    ...doc.topics,
    ...doc.paths,
  ].join(" ").toLowerCase();
  return haystack.includes(term);
}

function documentsForNotebookReferenceSearch(note) {
  const term = state.notebookReferenceSearch.trim().toLowerCase();
  const selectedIds = new Set(note.document_ids || []);
  return getVisibleDocuments()
    .filter((doc) => referenceSearchMatches(doc, term))
    .sort((a, b) => Number(selectedIds.has(b.id)) - Number(selectedIds.has(a.id)) || a.title.localeCompare(b.title))
    .slice(0, 12);
}

function renderNotebookReferenceSearch(note) {
  return `
    <section class="notebook-reference-strip">
      <div class="notebook-reference-search-head">
        <div>
          <p class="section-kicker">References</p>
          <h3>Search Files</h3>
        </div>
        <input id="notebook-reference-search" type="search" value="${escapeAttribute(state.notebookReferenceSearch)}" placeholder="Search tracked files to link or copy markdown">
      </div>
      <div id="notebook-reference-results" class="notebook-reference-results">
        ${renderNotebookReferenceResults(note)}
      </div>
    </section>
  `;
}

function renderNotebookReferenceResults(note) {
  const selectedIds = new Set(note.document_ids || []);
  const docs = documentsForNotebookReferenceSearch(note);
  if (!docs.length) {
    return `<div class="empty-state">No matching tracked files.</div>`;
  }
  return docs.map((doc) => `
    <div class="notebook-reference-result ${selectedIds.has(doc.id) ? "selected" : ""}">
      <label>
        <input type="checkbox" data-note-ref-id="${doc.id}" ${selectedIds.has(doc.id) ? "checked" : ""}>
        <span>
          <strong>${escapeHtml(doc.title || `Document #${doc.id}`)}</strong>
          <small>${escapeHtml(truncateAuthors(doc.authors))} · ${escapeHtml(doc.status)}</small>
        </span>
      </label>
      <div class="notebook-reference-actions">
        <button type="button" class="copy-md" data-copy-doc-markdown="${doc.id}">Copy MD</button>
        <button type="button" class="open-doc" data-notebook-open-doc="${doc.id}">Open</button>
      </div>
    </div>
  `).join("");
}

function updateNotebookReferenceResults() {
  const note = selectedNotebookNote();
  const results = els.notebookContainer.querySelector("#notebook-reference-results");
  if (!note || !results) return;
  results.innerHTML = renderNotebookReferenceResults(note);
}

function setNotebookReference(docId, linked) {
  const note = selectedNotebookNote();
  if (!note) return;
  const ids = new Set(note.document_ids || []);
  if (linked) {
    ids.add(docId);
  } else {
    ids.delete(docId);
  }
  note.document_ids = Array.from(ids).sort((a, b) => a - b);
}

function hasTemporaryNotebookImages(body) {
  return /!\[[^\]]*\]\(blob:[^)]+\)/.test(String(body || ""));
}

function currentNotebookPayload() {
  const titleInput = els.notebookContainer.querySelector("#notebook-title");
  const bodyInput = els.notebookContainer.querySelector("#notebook-body");
  const note = selectedNotebookNote();
  const checkedRefs = note?.document_ids || [];
  return {
    title: (titleInput?.value || "").trim() || "Untitled note",
    body: bodyInput?.value || "",
    document_ids: checkedRefs,
  };
}

async function createNotebookNote() {
  const documentIds = state.selectedId ? [state.selectedId] : [];
  const result = await api("/api/notebook", {
    method: "POST",
    body: {
      title: "Untitled note",
      body: "",
      document_ids: documentIds,
    },
  });
  state.notebookNotes.unshift(result.note);
  state.selectedNoteId = result.note.id;
  renderNotebook();
  showFlash("Notebook note created.");
}


function setNotebookSaveStatus(label, kind = "saved") {
  const status = els.notebookContainer.querySelector("#notebook-save-status");
  if (!status) return;
  window.clearTimeout(notebookSaveStatusResetTimer);
  status.textContent = label;
  status.className = `notebook-save-status ${kind}`;
}

function resetNotebookSaveStatusSoon() {
  window.clearTimeout(notebookSaveStatusResetTimer);
  notebookSaveStatusResetTimer = window.setTimeout(() => {
    setNotebookSaveStatus("Saved", "saved");
  }, 1400);
}


async function saveNotebookNote({ renderAfterSave = false } = {}) {
  if (!state.selectedNoteId) return;
  window.clearTimeout(notebookAutosaveTimer);
  notebookAutosaveTimer = null;
  const payload = currentNotebookPayload();
  if (hasTemporaryNotebookImages(payload.body)) {
    setNotebookSaveStatus("Image upload pending", "pending");
    return;
  }
  setNotebookSaveStatus("Saving...", "saving");
  let result;
  try {
    result = await api("/api/notebook/" + state.selectedNoteId, {
    method: "PATCH",
    body: payload,
  });
  } catch (error) {
    setNotebookSaveStatus("Save failed", "error");
    throw error;
  }
  const index = state.notebookNotes.findIndex((note) => note.id === result.note.id);
  if (index !== -1) state.notebookNotes[index] = result.note;
  setNotebookSaveStatus("Saved", "saved");
  resetNotebookSaveStatusSoon();
  if (renderAfterSave) renderNotebook();
}

function scheduleNotebookAutosave() {
  setNotebookSaveStatus("Autosave pending", "pending");
  window.clearTimeout(notebookAutosaveTimer);
  notebookAutosaveTimer = window.setTimeout(() => {
    saveNotebookNote().catch((error) => showFlash(error.message, "error"));
  }, DETAIL_AUTOSAVE_DELAY_MS);
}

async function deleteNotebookNote() {
  if (!state.selectedNoteId) return;
  if (!window.confirm("Delete this notebook note? File references are not deleted.")) return;
  await api("/api/notebook/" + state.selectedNoteId, { method: "DELETE" });
  state.notebookNotes = state.notebookNotes.filter((note) => note.id !== state.selectedNoteId);
  state.selectedNoteId = state.notebookNotes[0]?.id || null;
  renderNotebook();
  showFlash("Notebook note deleted.");
}

function waitingScanTooltip() {
  if (state.waitingToScanLoading) {
    return "Checking configured folders for files changed since the last scan.";
  }
  if (!state.waitingToScan) {
    return "No supported files changed since the last scan.";
  }
  if (!state.oldestWaitingModifiedAt) {
    return state.waitingToScan + " supported files changed since the last scan.";
  }
  return "Oldest pending file changed " + formatRelativeAge(state.oldestWaitingModifiedAt) + ".";
}

function formatRelativeAge(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "at an unknown time";
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  const units = [
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [unit, unitSeconds] of units) {
    if (seconds >= unitSeconds) {
      const amount = Math.floor(seconds / unitSeconds);
      return amount + " " + unit + (amount === 1 ? "" : "s") + " ago";
    }
  }
  return "less than a minute ago";
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleDateString();
}

function formatDateTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function truncateAuthors(value) {
  if (!value) return "No authors";
  const authors = value.split(",").map((part) => part.trim()).filter(Boolean);
  if (authors.length <= 3) return authors.join(", ");
  return `${authors.slice(0, 3).join(", ")} et al.`;
}

function selectedTopics() {
  return Array.from(els.detailTopics.querySelectorAll("input:checked")).map((input) => input.value);
}

function clearSaveButtonResetTimer() {
  if (saveButtonResetTimer) {
    window.clearTimeout(saveButtonResetTimer);
    saveButtonResetTimer = null;
  }
}

function setSaveButtonState(label, disabled = false) {
  clearSaveButtonResetTimer();
  els.saveDocument.textContent = label;
  els.saveDocument.disabled = disabled || !state.selectedId;
}

function restoreSaveButtonSoon() {
  clearSaveButtonResetTimer();
  saveButtonResetTimer = window.setTimeout(() => {
    if (state.selectedId) setSaveButtonState("Save", false);
  }, 1200);
}

function currentDetailPayload() {
  return {
    title: els.detailTitle.value.trim(),
    authors: els.detailAuthors.value.trim(),
    status: els.detailStatus.value,
    summary: els.detailSummary.value.trim(),
    topics: selectedTopics(),
  };
}

function applySavedDocument(document) {
  const index = state.documents.findIndex((item) => item.id === document.id);
  if (index !== -1) {
    state.documents[index] = document;
  }
  if (document.id === state.selectedId) {
    els.detailHeading.textContent = "#" + document.id + " " + document.title;
    els.detailStatus.value = document.status;
    els.detailStatus.className = "status-select status-" + document.status;
    els.detailTopics.querySelectorAll("input[type=\"checkbox\"]").forEach((input) => {
      input.checked = document.topics.includes(input.value);
    });
  }
  renderStats();
  renderDocuments();
}

async function saveSelectedDocument({ showSavedFlash = false, requestId = ++detailAutosaveRequestId } = {}) {
  if (!state.selectedId) return;
  const docId = state.selectedId;
  window.clearTimeout(detailAutosaveTimer);
  detailAutosaveTimer = null;
  setSaveButtonState("Saving...", true);
  try {
    const result = await api("/api/documents/" + docId, {
      method: "PATCH",
      body: currentDetailPayload(),
    });
    if (requestId !== detailAutosaveRequestId) return;
    applySavedDocument(result.document);
    setSaveButtonState("Saved", false);
    restoreSaveButtonSoon();
    if (showSavedFlash) showFlash("Document saved.");
  } catch (error) {
    if (requestId !== detailAutosaveRequestId) return;
    setSaveButtonState("Save", false);
    showFlash(error.message, "error");
  }
}

function scheduleDetailAutosave() {
  if (!state.selectedId) return;
  const requestId = ++detailAutosaveRequestId;
  window.clearTimeout(detailAutosaveTimer);
  detailAutosaveTimer = window.setTimeout(() => {
    saveSelectedDocument({ requestId });
  }, DETAIL_AUTOSAVE_DELAY_MS);
}

async function flushPendingDetailAutosave() {
  if (!detailAutosaveTimer || !state.selectedId) return;
  const requestId = ++detailAutosaveRequestId;
  await saveSelectedDocument({ requestId });
}

function topicColorStyle(topicName) {
  const hue = hashTopic(topicName) % 360;
  const bg = `hsla(${hue}, 70%, 92%, 1)`;
  const border = `hsla(${hue}, 52%, 58%, 0.38)`;
  const fg = `hsl(${hue}, 48%, 28%)`;
  return `--topic-bg:${bg};--topic-border:${border};--topic-fg:${fg};`;
}

function hashTopic(value) {
  let hash = 0;
  for (const char of value) {
    hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  }
  return Math.abs(hash);
}

function activitySummary(result) {
  if (result.mode === "duplicate_scan") {
    return result.recorded_count + " duplicate paths, " + result.new_group_count + " new groups";
  }
  if (result.mode === "scan") {
    return `${result.new_count} new, ${result.duplicate_count} duplicates, ${result.failed_count} failed`;
  }
  return `${result.updated_count} updated, ${result.failed_count} failed`;
}

function prependActivity(title, result) {
  state.activity.unshift({
    title,
    detail: activitySummary(result),
    items: result.items.slice(0, 4),
  });
  state.activity = state.activity.slice(0, 8);
  renderActivity();
}

function renderActivity() {
  if (!state.activity.length) {
    els.activityLog.innerHTML = `<div class="empty-state">Scan and rescan results will appear here.</div>`;
    return;
  }
  els.activityLog.innerHTML = state.activity.map((item) => `
    <article class="activity-item">
      <strong>${escapeHtml(item.title)}</strong>
      <small>${escapeHtml(item.detail)}</small>
      ${item.items.map((entry) => `
        <div>
          <strong>${escapeHtml(entry.title || entry.kind)}</strong>
          <small>${escapeHtml(entry.detail || "")}</small>
        </div>
      `).join("")}
    </article>
  `).join("");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}

function hideHeroTooltip() {
  els.heroTooltip.classList.add("hidden");
}

function showHeroTooltip(event, message) {
  if (!message) {
    hideHeroTooltip();
    return;
  }
  const rect = els.heroVisual.getBoundingClientRect();
  els.heroTooltip.textContent = message;
  els.heroTooltip.classList.remove("hidden");
  const tooltipRect = els.heroTooltip.getBoundingClientRect();
  const left = Math.min(
    Math.max(12, event.clientX - rect.left + 12),
    Math.max(12, rect.width - tooltipRect.width - 12)
  );
  const top = Math.min(
    Math.max(12, event.clientY - rect.top + 12),
    Math.max(12, rect.height - tooltipRect.height - 12)
  );
  els.heroTooltip.style.left = `${left}px`;
  els.heroTooltip.style.top = `${top}px`;
}

function setButtonBusy(button, busyText) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = busyText;
  return () => {
    button.disabled = false;
    button.textContent = originalText;
  };
}

els.filterSearch.addEventListener("input", (event) => {
  state.filters.search = event.target.value;
  renderDocuments();
});

els.filterStatus.addEventListener("change", (event) => {
  state.filters.status = event.target.value;
  renderDocuments();
});

els.filterTopic.addEventListener("change", (event) => {
  const input = event.target.closest("input[type='checkbox']");
  if (!input) return;
  state.filters.topics = Array.from(
    els.filterTopicOptions.querySelectorAll("input:checked")
  ).map((checkbox) => checkbox.value);
  els.filterTopicSummary.textContent = topicFilterLabel();
  renderDocuments();
});

els.heroVisual.addEventListener("mousemove", (event) => {
  const target = event.target.closest("[data-tooltip]");
  if (!target) {
    hideHeroTooltip();
    return;
  }
  showHeroTooltip(event, target.dataset.tooltip);
});

els.heroVisual.addEventListener("mouseleave", hideHeroTooltip);

els.heroTimeframe.addEventListener("change", (event) => {
  state.analyticsTimeframe = event.target.value;
  renderHeroVisuals();
});

function refreshWaitingToScan() {
  loadWaitingToScan({ showLoading: true });
}

const waitingScanCard = els.statWaitingScan.closest(".stat-card");
if (waitingScanCard) {
  waitingScanCard.addEventListener("click", refreshWaitingToScan);
  waitingScanCard.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    refreshWaitingToScan();
  });
}

document.addEventListener("click", (event) => {
  if (!els.filterTopic.open) return;
  if (els.filterTopic.contains(event.target)) return;
  els.filterTopic.open = false;
});

els.docsBody.addEventListener("click", async (event) => {
  const row = event.target.closest("tr[data-doc-id]");
  if (row && !event.target.closest("button, select")) {
    const nextDocId = Number(row.dataset.docId);
    if (nextDocId !== state.selectedId) {
      await flushPendingDetailAutosave();
    }
    state.selectedId = nextDocId;
    renderDocuments();
    renderDetail();
  }

  const rescanButton = event.target.closest("button[data-rescan-id]");
  const openButton = event.target.closest("button[data-open-id]");
  if (openButton) {
    const docId = Number(openButton.dataset.openId);
    window.open(`/api/documents/${docId}/open`, "_blank", "noopener");
    showFlash("Document opened.");
  }

  if (rescanButton) {
    const docId = Number(rescanButton.dataset.rescanId);
    try {
      rescanButton.disabled = true;
      const result = await api(`/api/documents/${docId}/rescan`, { method: "POST" });
      prependActivity(`Rescanned document #${docId}`, result);
      await loadState();
      showFlash("Document rescanned.");
    } catch (error) {
      showFlash(error.message, "error");
    } finally {
      rescanButton.disabled = false;
    }
  }
});

els.docsBody.addEventListener("change", async (event) => {
  if (!event.target.matches("select[data-status-id]")) return;
  const docId = Number(event.target.dataset.statusId);
  event.target.className = `status-select status-${event.target.value}`;
  try {
    await api(`/api/documents/${docId}`, {
      method: "PATCH",
      body: { status: event.target.value },
    });
    await loadState();
    showFlash("Status updated.");
  } catch (error) {
    showFlash(error.message, "error");
  }
});

els.detailForm.addEventListener("input", (event) => {
  if (!event.target.matches("#detail-title, #detail-authors, #detail-summary")) return;
  scheduleDetailAutosave();
});

els.detailStatus.addEventListener("change", (event) => {
  event.target.className = "status-select status-" + event.target.value;
  scheduleDetailAutosave();
});

els.detailTopics.addEventListener("change", (event) => {
  if (!event.target.matches("input[type=\"checkbox\"]")) return;
  scheduleDetailAutosave();
});

els.saveDocument.addEventListener("click", async () => {
  await saveSelectedDocument({ showSavedFlash: true });
});

els.detailOpen.addEventListener("click", async () => {
  if (!state.selectedId) return;
  window.open(`/api/documents/${state.selectedId}/open`, "_blank", "noopener");
  showFlash("Document opened.");
});

els.detailRescan.addEventListener("click", async () => {
  if (!state.selectedId) return;
  try {
    els.detailRescan.disabled = true;
    const result = await api(`/api/documents/${state.selectedId}/rescan`, { method: "POST" });
    prependActivity(`Rescanned document #${state.selectedId}`, result);
    await loadState();
    showFlash("Document rescanned.");
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    els.detailRescan.disabled = false;
  }
});

els.detailPaths.addEventListener("click", async (event) => {
  const clearButton = event.target.closest("button[data-clear-path-index]");
  const deleteButton = event.target.closest("button[data-delete-path-index]");
  const actionButton = clearButton || deleteButton;
  if (!actionButton || !state.selectedId) return;
  const doc = state.documents.find((item) => item.id === state.selectedId);
  const pathIndex = Number(clearButton ? clearButton.dataset.clearPathIndex : deleteButton.dataset.deletePathIndex);
  const filePath = doc?.paths?.[pathIndex];
  if (!filePath) return;
  const hardDelete = Boolean(deleteButton);
  const message = hardDelete
    ? "Permanently delete this duplicate file from disk and clear its tracker record? This cannot be undone."
    : "Clear this duplicate path record? The file on disk will not be deleted.";
  if (!window.confirm(message)) return;

  try {
    actionButton.disabled = true;
    const result = await api("/api/documents/" + state.selectedId + "/paths", {
      method: "DELETE",
      body: { path: filePath, hard_delete: hardDelete },
    });
    await loadState();
    showFlash(hardDelete
      ? result.deleted_count + " duplicate file" + (result.deleted_count === 1 ? "" : "s") + " deleted."
      : "Duplicate path cleared.");
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    actionButton.disabled = false;
  }
});

els.detailClearDuplicates.addEventListener("click", async () => {
  if (!state.selectedId) return;
  if (!window.confirm("Clear duplicate path records for this document? Files on disk will not be deleted.")) {
    return;
  }

  const resetButton = setButtonBusy(els.detailClearDuplicates, "Clearing...");
  try {
    const result = await api("/api/documents/" + state.selectedId + "/duplicates/clear", { method: "POST" });
    await loadState();
    showFlash(result.removed_count + " duplicate path" + (result.removed_count === 1 ? "" : "s") + " cleared.");
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    resetButton();
    renderDetail();
  }
});

els.detailHardDeleteDuplicates.addEventListener("click", async () => {
  if (!state.selectedId) return;
  if (!window.confirm("Permanently delete duplicate files for this document and clear their tracker records? Primary file is kept. This cannot be undone.")) {
    return;
  }

  const resetButton = setButtonBusy(els.detailHardDeleteDuplicates, "Deleting...");
  try {
    const result = await api("/api/documents/" + state.selectedId + "/duplicates/clear", {
      method: "POST",
      body: { hard_delete: true },
    });
    await loadState();
    showFlash(result.deleted_count + " duplicate file" + (result.deleted_count === 1 ? "" : "s") + " deleted.");
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    resetButton();
    renderDetail();
  }
});

els.clearDuplicatesAll.addEventListener("click", async () => {
  const totalDuplicatePaths = duplicatePathCount();
  if (!totalDuplicatePaths) return;
  if (!window.confirm("Clear all duplicate path records for tracked documents? Files on disk will not be deleted.")) {
    return;
  }

  const resetButton = setButtonBusy(els.clearDuplicatesAll, "Clearing...");
  try {
    const result = await api("/api/duplicates/clear", { method: "POST" });
    await loadState();
    showFlash(result.removed_count + " duplicate path" + (result.removed_count === 1 ? "" : "s") + " cleared.");
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    resetButton();
    renderDocuments();
  }
});

els.hardDeleteDuplicatesAll.addEventListener("click", async () => {
  const totalDuplicatePaths = duplicatePathCount();
  if (!totalDuplicatePaths) return;
  if (!window.confirm("Permanently delete all duplicate files from disk and clear their tracker records? Primary files are kept. This cannot be undone.")) {
    return;
  }

  const resetButton = setButtonBusy(els.hardDeleteDuplicatesAll, "Deleting...");
  try {
    const result = await api("/api/duplicates/clear", {
      method: "POST",
      body: { hard_delete: true },
    });
    await loadState();
    showFlash(result.deleted_count + " duplicate file" + (result.deleted_count === 1 ? "" : "s") + " deleted.");
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    resetButton();
    renderDocuments();
  }
});

els.pruneMissingFiles.addEventListener("click", async () => {
  if (!window.confirm("Remove tracker records for missing files? Documents with no remaining paths will be removed.")) {
    return;
  }

  const resetButton = setButtonBusy(els.pruneMissingFiles, "Pruning...");
  try {
    const result = await api("/api/missing-files/prune", { method: "POST" });
    await loadState();
    showFlash(
      "Pruned " + result.removed_path_count + " missing path" + (result.removed_path_count === 1 ? "" : "s")
      + " and removed " + result.removed_document_count + " document" + (result.removed_document_count === 1 ? "" : "s") + "."
    );
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    resetButton();
    renderDocuments();
  }
});

els.scanButton.addEventListener("click", async () => {
  const resetButton = setButtonBusy(els.scanButton, "Scanning...");
  try {
    showFlash("Scan started.", "info");
    const result = await api("/api/scan", {
      method: "POST",
      body: {
        path: els.scanPath.value || null,
        since: els.scanSince.value.trim() || null,
      },
    });
    prependActivity("Scan completed", result);
    await loadState(false);
    showFlash("Scan finished.");
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    resetButton();
  }
});

els.duplicateScanButton.addEventListener("click", async () => {
  const resetButton = setButtonBusy(els.duplicateScanButton, "Scanning...");
  try {
    showFlash("Duplicate scan started.", "info");
    const result = await api("/api/duplicates/scan", {
      method: "POST",
      body: {
        path: els.scanPath.value || null,
        since: els.scanSince.value.trim() || null,
      },
    });
    prependActivity("Duplicate scan completed", result);
    await loadState(false);
    showFlash(result.recorded_count + " duplicate path" + (result.recorded_count === 1 ? "" : "s") + " recorded.");
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    resetButton();
  }
});

els.rescanButton.addEventListener("click", async () => {
  const resetButton = setButtonBusy(els.rescanButton, "Rescanning...");
  try {
    showFlash("Metadata rescan started.", "info");
    const result = await api("/api/rescan", {
      method: "POST",
      body: {
        since: els.scanSince.value.trim() || null,
      },
    });
    prependActivity("Metadata rescan completed", result);
    await loadState();
    showFlash("Metadata rescan finished.");
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    resetButton();
  }
});

els.topicCreate.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = els.newTopicName.value.trim();
  const description = els.newTopicDescription.value.trim();
  if (!name) {
    showFlash("Topic name is required.", "error");
    return;
  }
  try {
    await api("/api/topics", {
      method: "POST",
      body: { name, description },
    });
    els.newTopicName.value = "";
    els.newTopicDescription.value = "";
    await loadState();
    showFlash("Topic created.");
  } catch (error) {
    showFlash(error.message, "error");
  }
});

els.topicsList.addEventListener("click", async (event) => {
  const saveButton = event.target.closest("button[data-topic-save]");
  if (saveButton) {
    const topicName = saveButton.dataset.topicSave;
    const nameInput = els.topicsList.querySelector(`[data-topic-name-input="${CSS.escape(topicName)}"]`);
    const descriptionInput = els.topicsList.querySelector(`[data-topic-description-input="${CSS.escape(topicName)}"]`);
    try {
      await api(`/api/topics/${encodeURIComponent(topicName)}`, {
        method: "PATCH",
        body: {
          name: nameInput.value.trim(),
          description: descriptionInput.value.trim(),
        },
      });
      await loadState();
      showFlash("Topic updated.");
    } catch (error) {
      showFlash(error.message, "error");
    }
  }

  const deleteButton = event.target.closest("button[data-topic-delete]");
  if (deleteButton) {
    const topicName = deleteButton.dataset.topicDelete;
    if (!window.confirm(`Delete topic "${topicName}"? Documents will be reassigned to Other.`)) {
      return;
    }
    try {
      await api(`/api/topics/${encodeURIComponent(topicName)}`, { method: "DELETE" });
      await loadState();
      showFlash("Topic deleted.");
    } catch (error) {
      showFlash(error.message, "error");
    }
  }
});



function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function applyNotebookPaneSizes() {
  const listWidth = window.localStorage.getItem("docuTrackerNotebookListWidth");
  const markdownWidth = window.localStorage.getItem("docuTrackerNotebookMarkdownWidth");
  if (listWidth) els.notebookContainer.style.setProperty("--notebook-list-width", listWidth);
  if (markdownWidth) els.notebookContainer.style.setProperty("--notebook-markdown-width", markdownWidth);
}

function startNotebookResize(event, kind) {
  const container = kind === "notebook-list"
    ? els.notebookContainer.querySelector(".notebook-panes")
    : els.notebookContainer.querySelector(".notebook-compose");
  if (!container) return;
  event.preventDefault();
  const rect = container.getBoundingClientRect();
  const startX = event.clientX;
  const propertyName = kind === "notebook-list" ? "--notebook-list-width" : "--notebook-markdown-width";
  const storageKey = kind === "notebook-list" ? "docuTrackerNotebookListWidth" : "docuTrackerNotebookMarkdownWidth";
  const fallback = kind === "notebook-list" ? 280 : Math.round(rect.width * 0.52);
  const current = Number.parseFloat(els.notebookContainer.style.getPropertyValue(propertyName)) || fallback;
  const min = kind === "notebook-list" ? 190 : 260;
  const max = Math.max(min, rect.width - (kind === "notebook-list" ? 520 : 320));

  const onMove = (moveEvent) => {
    const next = clamp(current + moveEvent.clientX - startX, min, max);
    const value = `${Math.round(next)}px`;
    els.notebookContainer.style.setProperty(propertyName, value);
    window.localStorage.setItem(storageKey, value);
  };
  const onUp = () => {
    document.body.classList.remove("is-resizing-notebook");
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
  };

  document.body.classList.add("is-resizing-notebook");
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp, { once: true });
}

async function copyMarkdownLinkForDocument(docId) {
  const doc = state.documents.find((item) => item.id === docId);
  if (!doc) return;
  const value = documentMarkdownLink(doc);
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
  } else {
    window.prompt("Markdown link", value);
  }
  showFlash("Markdown link copied.");
}


els.notebookContainer.addEventListener("click", async (event) => {
  const newButton = event.target.closest("#notebook-new");
  const noteButton = event.target.closest("button[data-note-id]");
  const saveButton = event.target.closest("#notebook-save");
  const deleteButton = event.target.closest("#notebook-delete");
  const openButton = event.target.closest("button[data-notebook-open-doc]");
  const copyButton = event.target.closest("button[data-copy-doc-markdown]");
  const markdownButton = event.target.closest("button[data-markdown-action]");

  try {
    if (newButton) {
      await saveNotebookNote({ renderAfterSave: false });
      await createNotebookNote();
      return;
    }
    if (noteButton) {
      const nextNoteId = Number(noteButton.dataset.noteId);
      if (nextNoteId !== state.selectedNoteId) {
        await saveNotebookNote({ renderAfterSave: false });
        state.selectedNoteId = nextNoteId;
        renderNotebook();
      }
      return;
    }
    if (saveButton) {
      await saveNotebookNote({ renderAfterSave: true });
      showFlash("Notebook note saved.");
      return;
    }
    if (deleteButton) {
      await deleteNotebookNote();
      return;
    }
    if (copyButton) {
      await copyMarkdownLinkForDocument(Number(copyButton.dataset.copyDocMarkdown));
      return;
    }
    if (markdownButton) {
      applyMarkdownToolbarAction(markdownButton.dataset.markdownAction);
      return;
    }
    if (openButton) {
      const docId = Number(openButton.dataset.notebookOpenDoc);
      window.open(`/api/documents/${docId}/open`, "_blank", "noopener");
      showFlash("Document opened.");
    }
  } catch (error) {
    showFlash(error.message, "error");
  }
});

els.notebookContainer.addEventListener("paste", (event) => {
  pasteNotebookImages(event).catch((error) => showFlash(error.message, "error"));
});

els.notebookContainer.addEventListener("input", (event) => {
  if (event.target.matches("#notebook-reference-search")) {
    state.notebookReferenceSearch = event.target.value;
    updateNotebookReferenceResults();
    return;
  }
  if (!event.target.matches("#notebook-title, #notebook-body")) return;
  if (event.target.matches("#notebook-body")) updateNotebookPreview();
  scheduleNotebookAutosave();
});

els.notebookContainer.addEventListener("change", async (event) => {
  if (!event.target.matches("input[data-note-ref-id]")) return;
  setNotebookReference(Number(event.target.dataset.noteRefId), event.target.checked);
  try {
    await saveNotebookNote({ renderAfterSave: true });
    showFlash("Notebook references updated.");
  } catch (error) {
    showFlash(error.message, "error");
  }
});

els.notebookContainer.addEventListener("pointerdown", (event) => {
  const resizer = event.target.closest("[data-resizer]");
  if (!resizer) return;
  startNotebookResize(event, resizer.dataset.resizer);
});

function setLibraryViewMode(mode) {
  state.viewMode = mode;
  els.tablePanel.classList.toggle("graph-mode-active", mode === "graph");
  els.tablePanel.classList.toggle("notebook-mode-active", mode === "notebook");
  document.body.classList.toggle("notebook-app-mode", mode === "notebook");
  els.viewToggleBtn.textContent = mode === "graph" ? "List View" : "Graph View";
  els.viewToggleBtn.classList.toggle("active", mode === "graph");
  els.notebookToggleBtn.textContent = mode === "notebook" ? "Library View" : "Notebook";
  els.notebookToggleBtn.classList.toggle("active", mode === "notebook");

  if (mode !== "graph" && state.networkInstance) {
    state.networkInstance.destroy();
    state.networkInstance = null;
  }
  if (mode === "graph") {
    els.freezePhysicsCheckbox.checked = state.freezePhysics;
    renderGraph();
  }
  if (mode === "notebook") {
    applyNotebookPaneSizes();
    renderNotebook();
  }
}

window.addEventListener("pagehide", closeBrowserSession);

// Library view actions
els.viewToggleBtn.addEventListener("click", async () => {
  if (state.viewMode === "notebook") {
    await saveNotebookNote({ renderAfterSave: false }).catch((error) => showFlash(error.message, "error"));
  }
  setLibraryViewMode(state.viewMode === "graph" ? "list" : "graph");
});

els.notebookToggleBtn.addEventListener("click", async () => {
  if (state.viewMode === "notebook") {
    await saveNotebookNote({ renderAfterSave: false }).catch((error) => showFlash(error.message, "error"));
    setLibraryViewMode("list");
    return;
  }
  setLibraryViewMode("notebook");
});

// Freeze physics checkbox listener
els.freezePhysicsCheckbox.addEventListener("change", (e) => {
  state.freezePhysics = e.target.checked;
  if (state.networkInstance) {
    state.networkInstance.setOptions({ physics: { enabled: !state.freezePhysics } });
  }
});

function renderGraph() {
  if (!window.vis) {
    showFlash("Graph visualization library (vis-network) failed to load.", "error");
    return;
  }

  const docs = getVisibleDocuments();

  // 1. Construct topic nodes
  const topicNodes = state.topics.map((t) => {
    const hue = hashTopic(t.name) % 360;
    const bg = `hsl(${hue}, 70%, 92%)`;
    const border = `hsl(${hue}, 52%, 48%)`;
    const highlightBg = `hsl(${hue}, 80%, 85%)`;
    const highlightBorder = `hsl(${hue}, 60%, 35%)`;
    const textColor = `hsl(${hue}, 52%, 24%)`;

    return {
      id: `topic:${t.name}`,
      label: t.name,
      shape: "dot",
      size: 28,
      color: {
        background: bg,
        border: border,
        highlight: {
          background: highlightBg,
          border: highlightBorder,
        },
      },
      font: {
        face: "Georgia, serif",
        size: 14,
        color: textColor,
        bold: true,
      },
      borderWidth: 2.5,
      shadow: {
        enabled: true,
        color: "rgba(36, 53, 48, 0.08)",
        size: 4,
        x: 0,
        y: 2,
      },
      docStatus: null,
    };
  });

  // 2. Construct document nodes
  const docNodes = docs.map((doc) => {
    const statusColor = STATUS_VISUALS[doc.status]?.color || "#67746f";
    const shortTitle = doc.title && doc.title.length > 35 
      ? doc.title.slice(0, 32) + "..." 
      : (doc.title || `Doc #${doc.id}`);
    
    const tooltipText = `Title: ${doc.title || "Untitled"}\nAuthors: ${doc.authors || "Unknown"}\nStatus: ${doc.status}\nSummary: ${doc.summary || ""}`;

    return {
      id: `doc:${doc.id}`,
      label: shortTitle,
      title: tooltipText,
      shape: "dot",
      size: 14,
      color: {
        background: statusColor,
        border: "#243530",
        highlight: {
          background: statusColor,
          border: "#0b6e62",
        },
      },
      font: {
        face: "Avenir Next, Segoe UI, sans-serif",
        size: 11,
        color: "#67746f",
      },
      borderWidth: 1.5,
      docStatus: doc.status,
    };
  });

  // 3. Construct edges based on topic annotations
  const edges = [];
  docs.forEach((doc) => {
    const topicsToUse = doc.topics && doc.topics.length ? doc.topics : ["Other"];
    topicsToUse.forEach((topicName) => {
      if (state.topics.some((t) => t.name === topicName)) {
        const hue = hashTopic(topicName) % 360;
        const edgeColor = `hsla(${hue}, 40%, 48%, 0.16)`;
        const edgeHighlight = `hsl(${hue}, 52%, 40%)`;

        edges.push({
          from: `doc:${doc.id}`,
          to: `topic:${topicName}`,
          color: {
            color: edgeColor,
            highlight: edgeHighlight,
            hover: edgeHighlight,
          },
          width: 1.5,
        });
      }
    });
  });

  const nodes = [...topicNodes, ...docNodes];

  if (state.networkInstance) {
    state.networkInstance.destroy();
    state.networkInstance = null;
  }

  const data = {
    nodes: new vis.DataSet(nodes),
    edges: new vis.DataSet(edges),
  };

  const options = {
    nodes: {
      scaling: { min: 10, max: 30 },
    },
    edges: {
      arrows: { to: { enabled: false } },
      smooth: {
        type: "continuous",
        forceDirection: "none",
      },
    },
    physics: {
      solver: "forceAtlas2Based",
      forceAtlas2Based: {
        gravitationalConstant: -40,
        centralGravity: 0.01,
        springLength: 100,
        springConstant: 0.08,
        damping: 0.4,
      },
      stabilization: {
        iterations: 100,
        updateInterval: 25,
      },
    },
    interaction: {
      hover: true,
      tooltipDelay: 150,
      selectable: true,
      selectConnectedEdges: false,
    },
  };

  state.networkInstance = new vis.Network(els.graphContainer, data, options);

  state.networkInstance.on("stabilized", () => {
    if (state.freezePhysics) {
      state.networkInstance.setOptions({ physics: { enabled: false } });
    }
  });

  state.networkInstance.on("click", (params) => {
    if (params.nodes.length > 0) {
      const clickedNodeId = params.nodes[0];
      if (clickedNodeId.startsWith("doc:")) {
        const docId = parseInt(clickedNodeId.split(":")[1], 10);
        state.selectedId = docId;
        renderDetail();
        highlightNeighbors(clickedNodeId);
      } else if (clickedNodeId.startsWith("topic:")) {
        highlightNeighbors(clickedNodeId);
      }
    } else {
      resetGraphHighlight();
    }
  });

  state.networkInstance.on("doubleClick", (params) => {
    if (params.nodes.length > 0) {
      const clickedNodeId = params.nodes[0];
      if (clickedNodeId.startsWith("doc:")) {
        const docId = parseInt(clickedNodeId.split(":")[1], 10);
        window.open(`/api/documents/${docId}/open`, "_blank", "noopener");
        showFlash("Document opened.");
      }
    }
  });
}

function highlightNeighbors(selectedNodeId) {
  if (!state.networkInstance) return;
  const allNodes = state.networkInstance.body.data.nodes.get();
  const connectedNodes = state.networkInstance.getConnectedNodes(selectedNodeId);

  const updatedNodes = allNodes.map((node) => {
    const isSelected = node.id === selectedNodeId;
    const isNeighbor = connectedNodes.includes(node.id);
    const opacity = (isSelected || isNeighbor) ? 1.0 : 0.15;
    
    return {
      id: node.id,
      color: {
        background: addOpacityToHex(node.color.background, opacity),
        border: addOpacityToHex(node.color.border, opacity),
      },
      font: {
        color: addOpacityToHex(node.id.startsWith("topic:") ? `hsl(${hashTopic(node.id.split(":")[1]) % 360}, 52%, 24%)` : "#67746f", opacity),
      },
    };
  });

  state.networkInstance.body.data.nodes.update(updatedNodes);
}

function resetGraphHighlight() {
  if (!state.networkInstance) return;
  const allNodes = state.networkInstance.body.data.nodes.get();
  const updatedNodes = allNodes.map((node) => {
    const isTopic = node.id.startsWith("topic:");
    let baseBg, baseBorder, textColor;

    if (isTopic) {
      const topicName = node.id.split(":")[1];
      const hue = hashTopic(topicName) % 360;
      baseBg = `hsl(${hue}, 70%, 92%)`;
      baseBorder = `hsl(${hue}, 52%, 48%)`;
      textColor = `hsl(${hue}, 52%, 24%)`;
    } else {
      baseBg = STATUS_VISUALS[node.docStatus]?.color || "#67746f";
      baseBorder = "#243530";
      textColor = "#67746f";
    }
    
    return {
      id: node.id,
      color: {
        background: baseBg,
        border: baseBorder,
      },
      font: {
        color: textColor,
      },
    };
  });

  state.networkInstance.body.data.nodes.update(updatedNodes);
}

function addOpacityToHex(color, opacity) {
  if (!color) return color;
  if (color.startsWith("rgba")) return color;
  
  let hex = color.replace("#", "");
  if (hex.length === 3) {
    hex = hex.split("").map(c => c + c).join("");
  }
  
  const r = parseInt(hex.substring(0, 2), 16);
  const g = parseInt(hex.substring(2, 4), 16);
  const b = parseInt(hex.substring(4, 6), 16);
  
  return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

Promise.all([openBrowserSession(), loadState()])
  .then(() => {
    renderActivity();
    state.waitingToScanPollId = window.setInterval(() => {
      loadWaitingToScan({ showLoading: false });
    }, WAITING_TO_SCAN_POLL_MS);
  })
  .catch((error) => {
    showFlash(error.message, "error");
  });
