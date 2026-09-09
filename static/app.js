"use strict";

const ui = {
  local: document.getElementById("local-select"),
  form: document.getElementById("url-form"),
  url: document.getElementById("source-url"),
  inspect: document.getElementById("url-inspect"),
  refresh: document.getElementById("refresh-status"),
  error: document.getElementById("error-message"),
  panel: document.getElementById("media-panel"),
  empty: document.getElementById("empty-state"),
  content: document.getElementById("media-content"),
  state: document.getElementById("media-state"),
  dot: document.getElementById("media-dot"),
  activity: document.getElementById("activity-status"),
  indicator: document.getElementById("activity-indicator"),
  summary: document.getElementById("system-summary"),
};

let token = null;
let busy = false;
let hasMedia = false;
const toolNames = ["python", "ffmpeg", "ffprobe", "yt_dlp"];

function setActivity(message) {
  ui.activity.textContent = message;
}

function setBusy(value) {
  busy = value;
  document.querySelectorAll("[data-action]").forEach((button) => { button.disabled = value; });
  ui.url.disabled = value;
  ui.panel.setAttribute("aria-busy", String(value));
  ui.indicator.classList.toggle("busy", value);
}

function clearError() {
  ui.error.hidden = true;
  ui.error.textContent = "";
  ui.url.removeAttribute("aria-invalid");
}

function showError(error) {
  ui.error.textContent = error instanceof Error ? error.message : "Inspection failed. Please try again.";
  ui.error.hidden = false;
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { ...options, cache: "no-store", credentials: "same-origin" });
  } catch {
    throw new Error("Cannot reach the local service. Make sure AI Clipper is running, then try again.");
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`The local service returned an unreadable response (HTTP ${response.status}). Please try again.`);
  }
  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : `The request failed (HTTP ${response.status}). Please try again.`);
  }
  return data;
}

async function getSession() {
  if (token !== null) return;
  const session = await request("/api/session");
  if (typeof session?.token !== "string" || !session.token) {
    throw new Error("The local service did not provide a session token. Please refresh the status and try again.");
  }
  token = session.token;
}

async function post(path, body) {
  await getSession();
  const options = { method: "POST", headers: { "X-AI-Clipper-Token": token } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  try {
    return await request(path, options);
  } catch (error) {
    // Refresh the session on the next action, without replaying a native picker request.
    token = null;
    throw error;
  }
}

function updateTools(tools) {
  let availableCount = 0;
  for (const name of toolNames) {
    const tool = tools?.[name];
    const available = tool?.available === true;
    if (available) availableCount += 1;
    const status = document.getElementById(`${name}-status`);
    const version = document.getElementById(`${name}-version`);
    status.textContent = tools ? (available ? "Available" : "Missing") : "Offline";
    status.className = `tool-status ${available ? "available" : "unavailable"}`;
    version.textContent = tools ? (tool?.version ?? (available ? "Version not reported" : "Not available")) : "Service unreachable";
    version.title = version.textContent;
  }
  ui.summary.textContent = tools ? `${availableCount} of 4 tools available` : "Local service unavailable";
}

async function refreshConnection() {
  if (busy) return;
  clearError();
  setBusy(true);
  setActivity("Checking the local service and session...");
  token = null;
  const results = await Promise.allSettled([
    request("/api/health").then((health) => {
      if (health?.status !== "ok" || !health.tools) throw new Error("The local service is not ready. Please try refreshing its status.");
      updateTools(health.tools);
    }).catch((error) => { updateTools(null); throw error; }),
    getSession(),
  ]);
  const failure = results.find((result) => result.status === "rejected");
  if (failure) {
    showError(failure.reason);
    setActivity("Connection check failed. Refresh status or try a source again.");
  } else {
    setActivity(hasMedia ? "System status refreshed. Your inspection is unchanged." : "Workspace connected. Choose a source to get started.");
  }
  setBusy(false);
}

function text(value, fallback = "Not provided") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function numeric(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function duration(value) {
  if (!numeric(value)) return "Not provided";
  const seconds = Math.floor(value);
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map((part) => String(part).padStart(2, "0")).join(":");
}

function fileSize(value) {
  if (!numeric(value)) return "Not provided";
  if (value < 1024) return `${value} B`;
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / (1024 ** unit)).toLocaleString("en-US", { maximumFractionDigits: 2 })} ${units[unit]}`;
}

function modifiedDate(value) {
  if (value === null || value === undefined || value === "") return "Not provided";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return text(value);
  return new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" }).format(date);
}

function safeUrl(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function addDetail(list, label, value, options = {}) {
  const row = element("div");
  const description = element("dd", options.mono ? "mono" : "");
  const href = options.link ? safeUrl(value) : null;
  if (href) {
    const link = element("a", "", text(value));
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    description.append(link);
  } else {
    description.textContent = text(value);
  }
  if (options.title) description.title = options.title;
  row.append(element("dt", "", label), description);
  list.append(row);
}

function renderMedia(media) {
  if (!media || (media.kind !== "local" && media.kind !== "url")) {
    throw new Error("The local service returned invalid media details. Please inspect the source again.");
  }
  const local = media.kind === "local";
  const content = document.createDocumentFragment();
  const heading = element("div", "media-heading");
  heading.append(element("p", "eyebrow", local ? "LOCAL FILE / READ-ONLY" : "WEB SOURCE / METADATA ONLY"));
  heading.append(element("h3", "", text(media.title, "Untitled media")));
  heading.append(element("p", "media-kind", local ? "Local video" : text(media.platform, "URL source")));
  content.append(heading);

  if (!local) {
    const preview = element("div", "thumbnail");
    const fallback = element("div", "thumbnail-fallback", "No thumbnail available");
    const thumbnail = safeUrl(media.thumbnail_url);
    preview.append(fallback);
    if (thumbnail) {
      const image = element("img");
      image.alt = `Thumbnail for ${text(media.title, "this video")}`;
      image.referrerPolicy = "no-referrer";
      image.hidden = true;
      image.addEventListener("load", () => { image.hidden = false; fallback.hidden = true; }, { once: true });
      image.addEventListener("error", () => { image.remove(); fallback.hidden = false; fallback.textContent = "Thumbnail could not be loaded"; }, { once: true });
      fallback.textContent = "Loading thumbnail...";
      preview.append(image);
      image.src = thumbnail;
    }
    content.append(preview);
  }

  const stats = element("dl", "media-stats");
  const resolution = media.width == null && media.height == null ? "Not provided" : `${text(media.width, "?")} x ${text(media.height, "?")}`;
  addDetail(stats, "Duration", duration(media.duration));
  addDetail(stats, "Resolution", resolution);
  addDetail(stats, "File size", fileSize(media.size_bytes), { title: numeric(media.size_bytes) ? `${media.size_bytes.toLocaleString("en-US")} bytes` : "" });
  content.append(stats, element("h4", "details-heading", "Media details"));
  const technical = element("dl", "metadata-list");
  addDetail(technical, "Duration", numeric(media.duration) ? `${media.duration} seconds` : null);
  addDetail(technical, "Width", media.width == null ? null : `${media.width} px`);
  addDetail(technical, "Height", media.height == null ? null : `${media.height} px`);
  addDetail(technical, "Frame rate", media.fps == null ? null : `${media.fps} fps`);
  addDetail(technical, "Video codec", media.video_codec);
  addDetail(technical, "Audio codec", media.audio_codec == null || media.audio_codec === "" || media.audio_codec === "none" ? "No audio stream" : media.audio_codec);
  addDetail(technical, "Container", media.container);
  if (!local) addDetail(technical, "Format info", media.format_info);
  content.append(technical, element("h4", "details-heading", "Source details"));
  const source = element("dl", "metadata-list");
  addDetail(source, "Source", media.source, { mono: local, link: !local });
  if (local) {
    addDetail(source, "Filename", media.filename, { mono: true });
    addDetail(source, "Full path", media.path, { mono: true });
    addDetail(source, "Modified", modifiedDate(media.modified_at), { title: text(media.modified_at, "") });
  } else {
    addDetail(source, "Platform", media.platform);
    addDetail(source, "Uploader", media.uploader);
    addDetail(source, "Original URL", media.source_url, { link: true });
    addDetail(source, "Thumbnail URL", media.thumbnail_url, { link: true });
  }
  content.append(source);
  ui.content.replaceChildren(content);
  ui.content.hidden = false;
  ui.empty.hidden = true;
  ui.state.textContent = media.status === "Ready" ? "Ready" : text(media.status, "Inspected");
  ui.dot.classList.add("ready");
  hasMedia = true;
}

async function inspectSource(kind) {
  if (busy) return;
  clearError();
  const url = ui.url.value.trim();
  if (kind === "url" && !safeUrl(url)) {
    ui.url.setAttribute("aria-invalid", "true");
    showError(new Error("Enter a complete http:// or https:// video URL from YouTube, Twitch, or TikTok."));
    ui.url.focus();
    return;
  }
  setBusy(true);
  try {
    let media;
    if (kind === "local") {
      setActivity("Opening the Windows file picker. Choose a video or cancel to return.");
      const selection = await post("/api/local/select");
      if (selection?.cancelled === true) {
        setActivity(hasMedia ? "Selection cancelled. Your previous inspection is unchanged." : "Selection cancelled. No video selected.");
        return;
      }
      if (typeof selection?.path !== "string" || !selection.path) throw new Error("No local path was returned. Please select the video again.");
      setActivity("Inspecting the local video in place. The source file is not being uploaded or changed.");
      media = await post("/api/local/inspect", { path: selection.path });
    } else {
      setActivity("Fetching URL metadata. No full video is being downloaded.");
      media = await post("/api/url/inspect", { url });
    }
    renderMedia(media);
    setActivity(kind === "local" ? "Inspection complete. The original file is unchanged." : "Inspection complete. URL metadata only; no full video downloaded.");
  } catch (error) {
    showError(error);
    setActivity(hasMedia ? "Inspection failed. Previous media details are still shown." : "Inspection failed. Check the message above and try again.");
  } finally {
    setBusy(false);
  }
}

ui.local.addEventListener("click", () => { void inspectSource("local"); });
ui.form.addEventListener("submit", (event) => { event.preventDefault(); void inspectSource("url"); });
ui.refresh.addEventListener("click", () => { void refreshConnection(); });
ui.url.addEventListener("input", () => { ui.url.removeAttribute("aria-invalid"); });
void refreshConnection();
