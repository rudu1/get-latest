const trendingList = document.querySelector("#trending-list");
const resultsList = document.querySelector("#results-list");
const trendingStatus = document.querySelector("#trending-status");
const resultsStatus = document.querySelector("#results-status");
const resultsNote = document.querySelector("#results-note");
const trendingSummary = document.querySelector("#trending-summary");
const resultsSummary = document.querySelector("#results-summary");
const emptyState = document.querySelector("#empty-state");
const searchForm = document.querySelector("#search-form");
const searchInput = document.querySelector("#search-input");
const refreshTrendingButton = document.querySelector("#refresh-trending");
const aiModePill = document.querySelector("#ai-mode-pill");
const heroStats = document.querySelector("#hero-stats");
const suggestedTopics = document.querySelector("#suggested-topics");
const cardTemplate = document.querySelector("#card-template");

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  loadTrending();
});

function bindEvents() {
  searchForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = searchInput.value.trim();

    if (!query) {
      return;
    }

    await loadSearch(query);
  });

  refreshTrendingButton.addEventListener("click", () => {
    loadTrending();
  });

  suggestedTopics.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-query]");
    if (!button) {
      return;
    }

    const query = button.dataset.query;
    if (!query || query === "Loading") {
      return;
    }

    searchInput.value = query;
    await loadSearch(query);
  });
}

async function loadTrending() {
  setStatus(trendingStatus, "Loading Bluesky, Reddit, and Hacker News trends...");
  renderLoadingSummary(trendingSummary, "Scanning live sources...");
  trendingList.innerHTML = "";

  try {
    const data = await fetchJSON("/api/trending");
    updateAiBadge(data.ai_enabled);
    renderSummary(trendingSummary, data.summary, "Landing digest");
    renderCards(trendingList, data.items);
    renderSuggestedTopics(data.top_topics || []);
    renderHeroStats(data);
    setStatus(trendingStatus, buildStatusLine(data));
  } catch (error) {
    renderError(trendingList, "I couldn't load the live trend feed.");
    renderErrorSummary(trendingSummary, error.message);
    renderSuggestedTopics([]);
    setStatus(trendingStatus, error.message);
  }
}

async function loadSearch(query) {
  emptyState.classList.add("hidden");
  resultsSummary.classList.remove("hidden");
  renderLoadingSummary(resultsSummary, `Searching for "${query}"...`);
  resultsList.innerHTML = "";
  resultsNote.textContent = `Pulling live Bluesky posts, Reddit threads, and Hacker News matches for "${query}".`;
  setStatus(resultsStatus, `Searching for "${query}"...`);

  try {
    const data = await fetchJSON(`/api/search?q=${encodeURIComponent(query)}`);
    updateAiBadge(data.ai_enabled);
    renderSummary(resultsSummary, data.summary, `AI read on "${query}"`);
    renderCards(resultsList, data.items);
    setStatus(resultsStatus, buildStatusLine(data));
  } catch (error) {
    renderError(resultsList, `I couldn't search for "${query}" right now.`);
    renderErrorSummary(resultsSummary, error.message);
    setStatus(resultsStatus, error.message);
  }
}

async function fetchJSON(url) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" }
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }

  return data;
}

function buildStatusLine(data) {
  const warningText = data.warnings?.length ? ` Warnings: ${data.warnings.join(" ")}` : "";
  return `Showing ${data.items.length} items across ${Object.keys(data.source_breakdown || {}).length} sources.${warningText}`;
}

function updateAiBadge(aiEnabled) {
  aiModePill.textContent = aiEnabled
    ? "AI digest powered by OpenAI"
    : "AI digest in fallback mode";
}

function renderHeroStats(data) {
  const sources = Object.entries(data.source_breakdown || {})
    .map(([source, count]) => `${source}: ${count}`)
    .join(" · ");
  const topics = (data.top_topics || []).slice(0, 3).join(" · ");

  heroStats.innerHTML = `
    <div class="summary-pill">Live sources: Bluesky, Reddit, Hacker News</div>
    <div class="summary-pill">${escapeHtml(sources || "Source mix unavailable")}</div>
    <div class="summary-pill">${escapeHtml(topics || "Top topics loading")}</div>
    <div class="summary-pill">${data.ai_enabled ? "OpenAI summary enabled" : "Add OPENAI_API_KEY for richer AI summaries"}</div>
  `;
}

function renderSuggestedTopics(topics) {
  if (!topics.length) {
    suggestedTopics.innerHTML = `
      <button type="button" class="chip" data-query="GTA 6">GTA 6</button>
      <button type="button" class="chip" data-query="OpenAI">OpenAI</button>
      <button type="button" class="chip" data-query="Bitcoin">Bitcoin</button>
      <button type="button" class="chip" data-query="India">India</button>
      <button type="button" class="chip" data-query="Superman">Superman</button>
    `;
    return;
  }

  suggestedTopics.innerHTML = topics
    .slice(0, 5)
    .map((topic) => `<button type="button" class="chip" data-query="${escapeHtml(topic)}">${escapeHtml(topic)}</button>`)
    .join("");
}

function renderLoadingSummary(container, message) {
  container.innerHTML = `<p class="summary-loading">${escapeHtml(message)}</p>`;
}

function renderErrorSummary(container, message) {
  container.innerHTML = `
    <p class="summary-label">Summary unavailable</p>
    <h3 class="summary-title">Something went wrong</h3>
    <p class="summary-body">${escapeHtml(message)}</p>
  `;
}

function renderSummary(container, summary, label) {
  const themes = (summary?.themes || [])
    .map((theme) => `<span class="theme-chip">${escapeHtml(theme)}</span>`)
    .join("");

  const suggestedQueries = (summary?.suggested_queries || [])
    .map((query) => `<span class="summary-badge">${escapeHtml(query)}</span>`)
    .join("");

  const caveats = (summary?.caveats || [])
    .map((caveat) => `<p class="summary-caveat">${escapeHtml(caveat)}</p>`)
    .join("");

  container.innerHTML = `
    <p class="summary-label">${escapeHtml(label)}</p>
    <h3 class="summary-title">${escapeHtml(summary?.headline || "No summary available yet")}</h3>
    <p class="summary-body">${escapeHtml(summary?.summary || "No digest returned.")}</p>
    <div class="summary-meta">
      <span class="summary-badge">Sentiment: ${escapeHtml(summary?.sentiment || "unknown")}</span>
      <span class="summary-badge">${summary?.available ? "OpenAI summary" : "Fallback summary"}</span>
    </div>
    <div class="summary-chips">${themes}</div>
    <div class="summary-meta">${suggestedQueries}</div>
    <p class="summary-provider">${escapeHtml(summary?.provider_status || "")}</p>
    ${caveats}
  `;
}

function renderCards(container, items) {
  container.innerHTML = "";

  if (!items?.length) {
    renderError(container, "No matching discussion was found.");
    return;
  }

  items.forEach((item) => {
    const fragment = cardTemplate.content.cloneNode(true);
    const sourcePill = fragment.querySelector(".source-pill");
    const bucketPill = fragment.querySelector(".bucket-pill");
    const timePill = fragment.querySelector(".time-pill");
    const title = fragment.querySelector(".card-title");
    const body = fragment.querySelector(".card-body");
    const author = fragment.querySelector(".author-item");
    const community = fragment.querySelector(".community-item");
    const scoreStat = fragment.querySelector(".score-stat");
    const discussionStat = fragment.querySelector(".discussion-stat");
    const extraStat = fragment.querySelector(".extra-stat");
    const openLink = fragment.querySelector(".open-link");
    const discussLink = fragment.querySelector(".discuss-link");

    sourcePill.textContent = item.source;
    sourcePill.dataset.source = item.source;
    bucketPill.textContent = item.bucket;
    timePill.textContent = item.relative_time;
    title.textContent = item.title;
    body.textContent = item.snippet;
    author.textContent = `by ${item.author}`;
    community.textContent = item.community;
    scoreStat.textContent = `${formatNumber(item.score)} ${item.score_label}`;
    discussionStat.textContent = `${formatNumber(item.discussions)} ${item.discussions_label}`;
    extraStat.textContent = `${item.extra} ${item.extra_label}`;
    openLink.href = item.url;
    discussLink.href = item.discussion_url;

    if (item.url === item.discussion_url) {
      discussLink.classList.add("hidden");
      openLink.textContent = "Open Thread";
    }

    container.appendChild(fragment);
  });
}

function renderError(container, message) {
  container.innerHTML = `<div class="error-card">${escapeHtml(message)}</div>`;
}

function setStatus(element, message) {
  element.textContent = message;
}

function formatNumber(value) {
  if (typeof value !== "number") {
    return value;
  }

  return new Intl.NumberFormat("en-US", {
    notation: value > 999 ? "compact" : "standard",
    maximumFractionDigits: 1
  }).format(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
