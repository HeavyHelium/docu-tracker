const state = {
  documents: [],
  topics: [],
  statuses: [],
  scanPaths: [],
  selectedId: null,
  activity: [],
  analyticsTimeframe: "8w",
  filters: {
    search: "",
    status: "",
    topics: [],
  },
};

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
  filterSearch: document.getElementById("filter-search"),
  filterStatus: document.getElementById("filter-status"),
  filterTopic: document.getElementById("filter-topic"),
  filterTopicSummary: document.getElementById("filter-topic-summary"),
  filterTopicOptions: document.getElementById("filter-topic-options"),
  scanPath: document.getElementById("scan-path"),
  scanSince: document.getElementById("scan-since"),
  scanButton: document.getElementById("scan-button"),
  rescanButton: document.getElementById("rescan-button"),
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
  topicsList: document.getElementById("topics-list"),
  topicCreate: document.getElementById("topic-create"),
  newTopicName: document.getElementById("new-topic-name"),
  newTopicDescription: document.getElementById("new-topic-description"),
  statDocs: document.getElementById("stat-docs"),
  statReading: document.getElementById("stat-reading"),
  statReview: document.getElementById("stat-review"),
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

  if (keepSelection && state.selectedId) {
    const stillExists = state.documents.some((doc) => doc.id === state.selectedId);
    if (!stillExists) state.selectedId = null;
  }

  renderFilters();
  renderStats();
  renderDocuments();
  renderDetail();
  renderTopics();
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
  els.statDocs.textContent = String(totalDocs);
  els.statReading.textContent = String(readingDocs);
  els.statReview.textContent = String(reviewDocs);
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
    .map((doc) => parseDocumentDate(doc))
    .filter(Boolean)
    .sort((a, b) => a - b);

  if (!datedDocs.length) return [];

  const firstWeek = startOfWeek(datedDocs[0]);
  const lastWeek = startOfWeek(datedDocs[datedDocs.length - 1]);
  const counts = new Map();

  for (const date of datedDocs) {
    const key = weekKey(startOfWeek(date));
    counts.set(key, (counts.get(key) || 0) + 1);
  }

  const series = [];
  const cursor = new Date(firstWeek);
  while (cursor <= lastWeek) {
    const key = weekKey(cursor);
    series.push({ key, count: counts.get(key) || 0 });
    cursor.setDate(cursor.getDate() + 7);
  }
  return series;
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
          data-tooltip="${escapeAttribute(`${formatWeekTick(point.key)} week: ${point.count} documents`)}"
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

function renderDocuments() {
  const docs = getVisibleDocuments();
  els.docCount.textContent = `${docs.length} visible`;
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
    els.saveDocument.disabled = true;
    return;
  }

  els.detailHeading.textContent = `#${doc.id} ${doc.title}`;
  els.detailForm.classList.remove("hidden");
  els.detailEmpty.classList.add("hidden");
  els.saveDocument.disabled = false;
  els.detailTitle.value = doc.title;
  els.detailAuthors.value = doc.authors;
  els.detailStatus.value = doc.status;
  els.detailStatus.className = `status-select status-${doc.status}`;
  els.detailSummary.value = doc.summary;
  els.detailModified.textContent = formatDateTime(doc.file_modified_at);
  els.detailScanned.textContent = formatDateTime(doc.scanned_at);
  els.detailPaths.innerHTML = doc.paths.map((path) => `<li>${escapeHtml(path)}</li>`).join("");
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

document.addEventListener("click", (event) => {
  if (!els.filterTopic.open) return;
  if (els.filterTopic.contains(event.target)) return;
  els.filterTopic.open = false;
});

els.docsBody.addEventListener("click", async (event) => {
  const row = event.target.closest("tr[data-doc-id]");
  if (row && !event.target.closest("button, select")) {
    state.selectedId = Number(row.dataset.docId);
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

els.detailStatus.addEventListener("change", (event) => {
  event.target.className = `status-select status-${event.target.value}`;
});

els.saveDocument.addEventListener("click", async () => {
  if (!state.selectedId) return;
  try {
    await api(`/api/documents/${state.selectedId}`, {
      method: "PATCH",
      body: {
        title: els.detailTitle.value.trim(),
        authors: els.detailAuthors.value.trim(),
        status: els.detailStatus.value,
        summary: els.detailSummary.value.trim(),
        topics: selectedTopics(),
      },
    });
    await loadState();
    showFlash("Document saved.");
  } catch (error) {
    showFlash(error.message, "error");
  }
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

window.addEventListener("pagehide", closeBrowserSession);

Promise.all([openBrowserSession(), loadState()])
  .then(() => {
    renderActivity();
  })
  .catch((error) => {
    showFlash(error.message, "error");
  });
