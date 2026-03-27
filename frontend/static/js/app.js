/* eslint-disable no-undef */

const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const resetBtn = document.getElementById("resetBtn");

const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");

const errorBox = document.getElementById("errorBox");
const fpsValue = document.getElementById("fpsValue");
const activeTracksValue = document.getElementById("activeTracksValue");
const typeCountsEl = document.getElementById("typeCounts");
const directionCountsEl = document.getElementById("directionCounts");
const lastEventEl = document.getElementById("lastEvent");
const cameraMetaEl = document.getElementById("cameraMeta");

const video = document.getElementById("video");
const overlay = document.getElementById("overlay");
const overlayCtx = overlay.getContext("2d");

// Hidden canvas used to downscale frames before sending to the backend.
const captureCanvas = document.createElement("canvas");
const captureCtx = captureCanvas.getContext("2d", { willReadFrequently: true });

let procWidth = 640;
let procHeight = 360;
let zones = [];
let sessionId = null;
let ws = null;

let isRunning = false;
let sendTimer = null;
let frameNumber = 0;

let activeCountsByType = {};
let loggedCountsByDirection = {};
let lastLoggedEvent = null;
let directionByTrack = {}; // trackId -> direction
let lastCentroidsByTrack = {}; // trackId -> {x,y} in proc coords

let scaleX = 1.0;
let scaleY = 1.0;

function setStatus(kind, text) {
  statusText.textContent = text;
  const dot = statusDot;
  dot.style.boxShadow = "0 0 0 6px rgba(255, 255, 255, 0.08)";
  if (kind === "running") {
    dot.style.background = "#2ee59d";
    dot.style.boxShadow = "0 0 0 6px rgba(46,229,157,0.18)";
  } else if (kind === "error") {
    dot.style.background = "#ff4d6d";
    dot.style.boxShadow = "0 0 0 6px rgba(255,77,109,0.16)";
  } else if (kind === "stopped") {
    dot.style.background = "rgba(255,255,255,0.35)";
    dot.style.boxShadow = "0 0 0 6px rgba(255,255,255,0.08)";
  } else {
    dot.style.background = "rgba(255,255,255,0.35)";
    dot.style.boxShadow = "0 0 0 6px rgba(255,255,255,0.08)";
  }
}

function showError(msg) {
  errorBox.classList.remove("hidden");
  errorBox.textContent = msg;
  setStatus("error", "Error");
}

function clearError() {
  errorBox.classList.add("hidden");
  errorBox.textContent = "";
}

function vehicleColor(vehicleType) {
  switch (vehicleType) {
    case "bike":
      return "#ffcc00";
    case "car":
      return "#2ee59d";
    case "van":
      return "#00d1ff";
    case "truck":
      return "#ff9f43";
    case "bus":
      return "#bf5bff";
    default:
      return "#aaaaaa";
  }
}

function getIsMobile() {
  return /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
}

function getCameraTypeAndFacingMode() {
  // Practical mapping: mobile prefers back camera, desktop prefers front.
  if (getIsMobile()) {
    return { cameraType: "mobile_back", facingMode: "environment" };
  }
  return { cameraType: "laptop_front", facingMode: "user" };
}

function drawArrow(x1, y1, x2, y2, color) {
  overlayCtx.strokeStyle = color;
  overlayCtx.lineWidth = 2;
  overlayCtx.beginPath();
  overlayCtx.moveTo(x1, y1);
  overlayCtx.lineTo(x2, y2);
  overlayCtx.stroke();

  // Arrow head
  const headlen = 6;
  const angle = Math.atan2(y2 - y1, x2 - x1);
  overlayCtx.beginPath();
  overlayCtx.moveTo(x2, y2);
  overlayCtx.lineTo(x2 - headlen * Math.cos(angle - Math.PI / 7), y2 - headlen * Math.sin(angle - Math.PI / 7));
  overlayCtx.lineTo(x2 - headlen * Math.cos(angle + Math.PI / 7), y2 - headlen * Math.sin(angle + Math.PI / 7));
  overlayCtx.lineTo(x2, y2);
  overlayCtx.fillStyle = color;
  overlayCtx.fill();
}

function resizeOverlayToVideo(procW, procH) {
  const rect = overlay.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  overlay.width = Math.max(1, Math.round(rect.width * dpr));
  overlay.height = Math.max(1, Math.round(rect.height * dpr));

  // Draw in CSS pixels.
  overlayCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  scaleX = rect.width / procW;
  scaleY = rect.height / procH;
}

function clearOverlay() {
  const rect = overlay.getBoundingClientRect();
  overlayCtx.clearRect(0, 0, rect.width, rect.height);
}

function drawZones() {
  const colors = ["#4f8cff", "#2ee59d", "#00d1ff", "#ff9f43", "#bf5bff", "#ffffff"];
  zones.forEach((z, idx) => {
    const pts = z.points;
    if (!pts || pts.length < 3) return;

    overlayCtx.fillStyle = "rgba(79, 140, 255, 0.08)";
    overlayCtx.strokeStyle = colors[idx % colors.length];
    overlayCtx.lineWidth = 2;

    overlayCtx.beginPath();
    pts.forEach((p, i) => {
      const x = p[0] * scaleX;
      const y = p[1] * scaleY;
      if (i === 0) overlayCtx.moveTo(x, y);
      else overlayCtx.lineTo(x, y);
    });
    overlayCtx.closePath();
    overlayCtx.fill();
    overlayCtx.stroke();

    // Label near first point
    const x0 = pts[0][0] * scaleX;
    const y0 = pts[0][1] * scaleY;
    overlayCtx.font = "bold 12px ui-sans-serif, system-ui, -apple-system";
    overlayCtx.fillStyle = "rgba(255,255,255,0.92)";
    overlayCtx.fillText(z.name, x0 + 6, y0 + 14);
  });
}

function drawTracks(tracks) {
  tracks.forEach((t) => {
    const [x1, y1, x2, y2] = t.bbox;
    const color = vehicleColor(t.vehicleType);

    const sx1 = x1 * scaleX;
    const sy1 = y1 * scaleY;
    const sx2 = x2 * scaleX;
    const sy2 = y2 * scaleY;

    overlayCtx.strokeStyle = color;
    overlayCtx.lineWidth = 2;
    overlayCtx.strokeRect(sx1, sy1, sx2 - sx1, sy2 - sy1);

    const cx = (x1 + x2) / 2.0;
    const cy = (y1 + y2) / 2.0;
    const scx = cx * scaleX;
    const scy = cy * scaleY;

    const prev = lastCentroidsByTrack[t.trackId];
    if (prev) {
      drawArrow(prev.x * scaleX, prev.y * scaleY, scx, scy, color);
    }
    lastCentroidsByTrack[t.trackId] = { x: cx, y: cy };

    const direction = directionByTrack[String(t.trackId)];
    const label = `${t.vehicleType} #${t.trackId} (${t.confidence.toFixed(2)})${direction ? " " + direction : ""}`;

    overlayCtx.font = "bold 12px ui-sans-serif, system-ui, -apple-system";
    const metrics = overlayCtx.measureText(label);
    const padX = 6;
    const padY = 6;
    const textW = metrics.width + padX * 2;
    const textH = 18;

    const bgX = sx1;
    const bgY = sy1 - textH - 6;
    overlayCtx.fillStyle = "rgba(0,0,0,0.55)";
    overlayCtx.fillRect(bgX, bgY, textW, textH);

    overlayCtx.fillStyle = "rgba(255,255,255,0.94)";
    overlayCtx.fillText(label, bgX + padX, bgY + 13);
  });
}

function renderCounts() {
  const totalActive = Object.values(activeCountsByType).reduce((a, b) => a + b, 0);
  activeTracksValue.textContent = String(totalActive);

  typeCountsEl.innerHTML = "";
  Object.entries(activeCountsByType)
    .sort((a, b) => b[1] - a[1])
    .forEach(([k, v]) => {
      const div = document.createElement("div");
      div.className = "pill";
      div.innerHTML = `<span class="pillKey">${k}</span><span class="pillVal">${v}</span>`;
      typeCountsEl.appendChild(div);
    });

  directionCountsEl.innerHTML = "";
  const hasDir = Object.keys(loggedCountsByDirection).length > 0;
  if (!hasDir) {
    directionCountsEl.innerHTML = `<div class="muted">No logged events yet.</div>`;
    return;
  }
  const sorted = Object.entries(loggedCountsByDirection).sort((a, b) => b[1] - a[1]);
  sorted.forEach(([k, v]) => {
    const div = document.createElement("div");
    div.className = "pill";
    div.innerHTML = `<span class="pillKey">${k}</span><span class="pillVal">${v}</span>`;
    directionCountsEl.appendChild(div);
  });
}

function renderLastEvent() {
  if (!lastLoggedEvent) {
    lastEventEl.innerHTML = `<div class="muted">No events yet.</div>`;
    return;
  }
  const e = lastLoggedEvent;
  lastEventEl.innerHTML = `
    <div><b>Direction:</b> ${e.direction}</div>
    <div><b>Vehicle:</b> ${e.vehicleType} (Track ${e.trackId})</div>
    <div><b>Confidence:</b> ${Number(e.confidence).toFixed(2)}</div>
    <div><b>Zones:</b> ${e.entryZone} → ${e.exitZone}</div>
    <div class="muted" style="margin-top:6px;"><b>Frame:</b> ${e.frameNumber}</div>
  `;
}

function renderOverlay(update) {
  clearOverlay();
  drawZones();
  drawTracks(update.tracks || []);
}

function stopFrameSend() {
  if (sendTimer) {
    clearInterval(sendTimer);
    sendTimer = null;
  }
}

async function stopCamera() {
  try {
    if (video) {
      video.pause();
      video.src = "";
    }
  } catch (_) {}
}

function wsClose() {
  try {
    if (ws) ws.close();
  } catch (_) {}
  ws = null;
}

async function startSurvey() {
  clearError();
  if (isRunning) return;

  setStatus("running", "Starting...");
  startBtn.disabled = true;
  stopBtn.disabled = false;
  resetBtn.disabled = false;

  const cameraType = "file";

  let startResp = null;
  try {
    const resp = await fetch("/api/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cameraType: cameraType, cameraName: "FrontCam" }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.detail || "Failed to start session");
    }
    startResp = data;
  } catch (e) {
    startBtn.disabled = false;
    stopBtn.disabled = true;
    resetBtn.disabled = true;
    setStatus("idle", "Idle");
    showError("Failed to start session on server.");
    return;
  }

  sessionId = startResp.sessionId;
  procWidth = startResp.procWidth;
  procHeight = startResp.procHeight;
  zones = startResp.zones || [];
  cameraMetaEl.textContent = `Session: ${sessionId.substring(0, 8)} • Camera: ${startResp.cameraName}`;

  // Resize overlay to match the preview size.
  lastCentroidsByTrack = {};
  lastLoggedEvent = null;
  directionByTrack = {};
  activeCountsByType = {};
  loggedCountsByDirection = {};

  captureCanvas.width = procWidth;
  captureCanvas.height = procHeight;
  resizeOverlayToVideo(procWidth, procHeight);
  window.addEventListener("resize", () => resizeOverlayToVideo(procWidth, procHeight));

  // Load video file instead of webcam
  video.src = "/video.mp4";
  video.loop = true;
  video.addEventListener('error', (e) => {
    showError("Video file not found or failed to load. Please ensure video.mp4 is placed in the project root.");
    startBtn.disabled = false;
    stopBtn.disabled = true;
    resetBtn.disabled = true;
    setStatus("error", "Video error");
  });
  await video.play().catch((e) => {
    showError("Failed to play video. Please check the video file.");
    startBtn.disabled = false;
    stopBtn.disabled = true;
    resetBtn.disabled = true;
    setStatus("error", "Video error");
  });

  // WebSocket for frame processing.
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${window.location.host}/ws?sessionId=${sessionId}`);

  ws.onopen = () => {
    isRunning = true;
    setStatus("running", "Survey running");

    const targetSendFps = 8; // conservative for laptop stability
    const intervalMs = Math.round(1000 / targetSendFps);
    frameNumber = 0;

    sendTimer = setInterval(async () => {
      if (!isRunning) return;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;

      // Draw current frame into capture canvas at proc resolution.
      captureCtx.drawImage(video, 0, 0, procWidth, procHeight);
      const jpegDataUrl = captureCanvas.toDataURL("image/jpeg", 0.62);

      const payload = {
        type: "frame",
        imageData: jpegDataUrl,
        frameNumber: frameNumber,
      };
      frameNumber += 1;

      try {
        ws.send(JSON.stringify(payload));
      } catch (e) {
        // ignore transient send errors; will recover on next interval if still open
      }
    }, intervalMs);
  };

  ws.onmessage = (evt) => {
    let msg = null;
    try {
      msg = JSON.parse(evt.data);
    } catch (_) {
      return;
    }
    if (msg.type === "update") {
      fpsValue.textContent = (msg.fps ? msg.fps.toFixed(1) : "--");
      activeCountsByType = msg.activeCountsByType || {};
      loggedCountsByDirection = msg.loggedCountsByDirection || {};
      directionByTrack = msg.directionByTrack || {};
      lastLoggedEvent = msg.lastLoggedEvent || null;
      renderCounts();
      renderLastEvent();
      renderOverlay(msg);
      setStatus("running", msg.session?.status === "running" ? "Survey running" : "Survey running");
    } else if (msg.type === "error") {
      showError(msg.message || "Backend error");
    }
  };

  ws.onerror = () => {
    showError("WebSocket error. Check backend logs and network availability.");
  };

  ws.onclose = () => {
    // If we didn't stop intentionally, keep UI consistent.
  };
}

async function stopSurvey() {
  if (!isRunning && !sessionId) return;
  isRunning = false;
  stopFrameSend();
  wsClose();
  await stopCamera();

  if (sessionId) {
    try {
      const resp = await fetch("/api/session/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId: sessionId }),
      });
      const summary = await resp.json().catch(() => ({}));

      cameraMetaEl.textContent = `Stopped. Total logged: ${summary.totalLogged || 0}`;
    } catch (_) {
      cameraMetaEl.textContent = "Stopped.";
    }
  }

  sessionId = null;
  startBtn.disabled = false;
  stopBtn.disabled = true;
  resetBtn.disabled = true;
  setStatus("stopped", "Survey stopped");
}

async function resetSurvey() {
  await stopSurvey();
  setTimeout(() => startSurvey(), 300);
}

startBtn.addEventListener("click", startSurvey);
stopBtn.addEventListener("click", stopSurvey);
resetBtn.addEventListener("click", resetSurvey);

// Initial UI
setStatus("idle", "Idle");
renderCounts();

