const state = {
  documents: [],
  topics: [],
  statuses: [],
  scanPaths: [],
  selectedId: null,
  activity: [],
  filters: {
    search: "",
    status: "",
    topic: "",
  },
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

  setSelectOptions(els.filterTopic, [{ value: "", label: "All topics" }].concat(
    state.topics.map((topic) => ({ value: topic.name, label: topic.name }))
  ), state.filters.topic);

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
  els.statDocs.textContent = String(state.documents.length);
  els.statReading.textContent = String(state.documents.filter((doc) => doc.status === "reading").length);
  els.statReview.textContent = String(state.documents.filter((doc) => doc.status === "needs_review").length);
}

function getVisibleDocuments() {
  const term = state.filters.search.trim().toLowerCase();
  return state.documents.filter((doc) => {
    if (state.filters.status && doc.status !== state.filters.status) return false;
    if (state.filters.topic && !doc.topics.includes(state.filters.topic)) return false;
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
  state.filters.topic = event.target.value;
  renderDocuments();
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
