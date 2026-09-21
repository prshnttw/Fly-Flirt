// ==========================================================================
// Fly Chat - Client Controller with Integrated 3D Connectome Engine
// Syncs both Desktop (cockpit column) and Mobile (inline HUD) views.
// ==========================================================================

const socket = io();
const role = document.body.dataset.role || "male";

// UI Elements — Desktop cockpit
const log = document.getElementById("chat-log");
const form = document.getElementById("message-form");
const input = document.getElementById("message-input");
const flyPanel = document.getElementById("fly-panel");
const flyState = document.getElementById("fly-state");
const flyDiary = document.getElementById("fly-diary");
const judgeRegion = document.getElementById("judge-region");
const judgePlain = document.getElementById("judge-plain");
const judgeTechnical = document.getElementById("judge-technical");
const meterFill = document.getElementById("meter-fill");
const meterPercentLabel = document.getElementById("meter-percent-label");
const metricWarmth = document.getElementById("metric-warmth");
const metricHumor = document.getElementById("metric-humor");
const metricReciprocity = document.getElementById("metric-reciprocity");
const triggerPulseBtn = document.getElementById("trigger-pulse-btn");
const lensCaption = document.getElementById("lens-caption");
const lensButtons = document.querySelectorAll("[data-lens-choice]");
const verdictBtn = document.getElementById("verdict-btn");
const verdictBtnProgress = document.getElementById("verdict-btn-progress");
const minMessagesForVerdict = verdictBtn ? parseInt(verdictBtn.dataset.min, 10) : 3;

// UI Elements — Mobile inline HUD
const mobileFlyState = document.getElementById("mobile-fly-state");
const mobileFlyDiary = document.getElementById("mobile-fly-diary");
const mobileJudgeRegion = document.getElementById("mobile-judge-region");
const mobileJudgePlain = document.getElementById("mobile-judge-plain");
const mobileMeterFill = document.getElementById("mobile-meter-fill");
const mobileMeterPercent = document.getElementById("mobile-meter-percent");

// ===== 3D Brain Visualizers =====
let dishVisualizer = null;     // Desktop embedded specimen dish
let mobileBrainViz = null;     // Mobile compact brain

window.addEventListener("DOMContentLoaded", () => {
  // Desktop: Initialize 3D brain in specimen-dish-viewport
  const desktopContainer = document.getElementById("specimen-dish-viewport");
  if (desktopContainer && window.FlyBrainVisualizer) {
    dishVisualizer = new FlyBrainVisualizer(desktopContainer, {
      autoRotate: true,
      autoRotateSpeed: 0.002,
      cameraDistance: 1.6,
      fov: 52,
      isMini: true,
      showEdges: true
    });
    window.dishVisualizer = dishVisualizer;
  }

  // Mobile: Initialize compact 3D brain in mobile-brain-viewport
  const mobileContainer = document.getElementById("mobile-brain-viewport");
  if (mobileContainer && window.FlyBrainVisualizer) {
    mobileBrainViz = new FlyBrainVisualizer(mobileContainer, {
      autoRotate: true,
      autoRotateSpeed: 0.003,
      cameraDistance: 2.6,
      fov: 58,
      isMini: true,
      showEdges: true
    });
    window.mobileBrainViz = mobileBrainViz;
  }
});

// Manual trigger button (desktop)
if (triggerPulseBtn) {
  triggerPulseBtn.addEventListener("click", () => {
    fireBothBrains(1.5);
  });
}

// Fire synaptic pulses on both desktop and mobile 3D brains
function fireBothBrains(intensity) {
  if (dishVisualizer) {
    dishVisualizer.triggerSynapticFiring(intensity);
  }
  if (mobileBrainViz) {
    mobileBrainViz.triggerSynapticFiring(intensity);
  }
}

// Append System Status Line
function appendStatus(text) {
  const el = document.createElement("div");
  el.className = "status-bubble";
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}

// Append Chat Message
function appendMessage(sender, text) {
  const isMine = sender === role;
  const wrapper = document.createElement("div");
  wrapper.className = `msg-wrapper ${isMine ? "mine" : "theirs"} role-${sender}`;

  const meta = document.createElement("div");
  meta.className = "msg-meta";
  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  meta.innerHTML = `<span>${sender.toUpperCase()} SPECIMEN</span> • <span>${now}</span>`;

  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";
  bubble.textContent = text;

  wrapper.appendChild(meta);
  wrapper.appendChild(bubble);
  log.appendChild(wrapper);
  log.scrollTop = log.scrollHeight;

  // Trigger synaptic firing burst on message arrival
  fireBothBrains(1.3);
}

// Update Fly Panel & Telemetry — syncs BOTH desktop and mobile
function updateFlyPanel(data) {
  if (data.state) {
    // Desktop
    if (flyPanel) flyPanel.dataset.state = data.state;
    if (flyState) {
      flyState.dataset.state = data.state;
      flyState.textContent = data.state.toUpperCase();
    }
    // Mobile
    if (mobileFlyState) {
      mobileFlyState.dataset.state = data.state;
      mobileFlyState.textContent = data.state.toUpperCase();
    }
  }

  if (data.brainstorm) {
    const quote = `"${data.brainstorm}"`;
    if (flyDiary) flyDiary.textContent = quote;
    if (mobileFlyDiary) mobileFlyDiary.textContent = quote;
  }

  if (data.judge) {
    if (judgeRegion) judgeRegion.textContent = data.judge.region;
    if (judgePlain) judgePlain.textContent = data.judge.plain;
    if (judgeTechnical) judgeTechnical.textContent = data.judge.technical;
    if (mobileJudgeRegion) mobileJudgeRegion.textContent = data.judge.region;
    if (mobileJudgePlain) mobileJudgePlain.textContent = data.judge.plain;
  }

  if (typeof data.meter === "number") {
    const pct = Math.round(data.meter * 100);
    const pctStr = `${pct}%`;
    // Desktop
    if (meterFill) meterFill.style.width = pctStr;
    if (meterPercentLabel) meterPercentLabel.textContent = pctStr;
    // Mobile
    if (mobileMeterFill) mobileMeterFill.style.width = pctStr;
    if (mobileMeterPercent) mobileMeterPercent.textContent = pctStr;
  }

  if (data.scores) {
    if (metricWarmth) metricWarmth.textContent = data.scores.warmth.toFixed(2);
    if (metricHumor) metricHumor.textContent = data.scores.humor.toFixed(2);
    if (metricReciprocity) metricReciprocity.textContent = data.scores.reciprocity.toFixed(2);
  }

  if (data.message_counts) {
    updateVerdictButton(data.message_counts);
  }

  // If verdict reached, trigger big firing flurry
  if (data.state === "verdict") {
    fireBothBrains(2.2);
  }
}

// Enable the "Get Verdict" button once both sides have sent enough messages.
// Links to /circuit-card, which shows the real current reading — no forced
// or inflated result.
function updateVerdictButton(counts) {
  if (!verdictBtn) return;
  const lowest = Math.min(counts.male || 0, counts.female || 0);
  const ready = lowest >= minMessagesForVerdict;

  verdictBtn.classList.toggle("ready", ready);
  verdictBtn.setAttribute("aria-disabled", ready ? "false" : "true");
  verdictBtn.tabIndex = ready ? 0 : -1;

  if (ready) {
    verdictBtn.href = "/circuit-card";
    verdictBtn.target = "_blank";
    verdictBtn.rel = "noopener";
    if (verdictBtnProgress) verdictBtnProgress.textContent = "";
  } else if (verdictBtnProgress) {
    verdictBtnProgress.textContent = `(${lowest}/${minMessagesForVerdict} each)`;
  }
}

// Lens Captions
const LENS_CAPTIONS = {
  shared: "Shared lens — over 95% of a fly's brain wiring looks like this across every sex. Most of what drives this panel isn't sex-specific at all.",
  divergent: "Divergent lens — pC1 and pIP10, the two neuron types running this circuit, are sexually dimorphic hotspots in the fly connectome.",
  wildtype: "Wild-type lens — raw circuit: mAL → pC1 → pIP10, straight from MaleCNS v1.0."
};

function setLens(lens) {
  if (flyPanel) flyPanel.dataset.lens = lens;
  if (lensCaption) {
    lensCaption.textContent = LENS_CAPTIONS[lens] || "";
  }
  lensButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.lensChoice === lens);
  });
}

lensButtons.forEach((btn) => {
  btn.addEventListener("click", () => setLens(btn.dataset.lensChoice));
});
setLens("shared");

// Socket Events
socket.on("connect", () => {
  socket.emit("join");
  const statusPill = document.getElementById("chat-connection-status");
  if (statusPill) statusPill.textContent = "● Live Socket Active";
});

socket.on("status", (data) => appendStatus(data.text));
socket.on("new_message", (data) => appendMessage(data.sender, data.text));
socket.on("fly_update", updateFlyPanel);

socket.on("brain_activity", ({ node_activity: nodeActivity }) => {
  if (dishVisualizer) {
    dishVisualizer.updateActivity(nodeActivity);
  }
  if (mobileBrainViz) {
    mobileBrainViz.updateActivity(nodeActivity);
  }
});

// Message Form Submission
form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;

  socket.emit("message", { text });
  input.value = "";

  // Proactive action potential pulse on send
  fireBothBrains(1.4);
});
