// ==========================================================================
// Fly Brain - Fullscreen Spectator Connectome Visualizer Controller
// ==========================================================================

const socket = io();
const sceneContainer = document.getElementById("scene");
const tooltip = document.getElementById("node-tooltip");
const tooltipTitle = document.getElementById("tooltip-title");
const tooltipMeta = document.getElementById("tooltip-meta");
const tooltipActivity = document.getElementById("tooltip-activity");
const stateBadge = document.getElementById("spectator-state-badge");
const pulseTriggerBtn = document.getElementById("spectator-pulse-trigger");
const viewButtons = document.querySelectorAll("[data-view]");

let visualizer = null;

window.addEventListener("DOMContentLoaded", () => {
  if (sceneContainer && window.FlyBrainVisualizer) {
    visualizer = new FlyBrainVisualizer(sceneContainer, {
      autoRotate: true,
      autoRotateSpeed: 0.0016,
      cameraDistance: 2.2,
      fov: 55,
      interactive: true,
      showEdges: true
    });

    // Handle interactive hover tooltip
    visualizer.onNodeHover = (nodeData, screenPos) => {
      if (!nodeData) {
        tooltip.style.display = "none";
        return;
      }
      tooltipTitle.textContent = `${nodeData.type} (#${nodeData.id})`;
      tooltipMeta.textContent = `Role: ${nodeData.role.toUpperCase()} | NT: ${nodeData.nt || "Unknown"}`;
      tooltipActivity.textContent = `Activation: ${(nodeData.currentActivation || 0).toFixed(2)}`;
      tooltip.style.left = `${screenPos.x}px`;
      tooltip.style.top = `${screenPos.y}px`;
      tooltip.style.display = "block";
    };

    window.spectatorVisualizer = visualizer;
  }
});

// Camera View Presets
viewButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    viewButtons.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    if (visualizer) {
      visualizer.setCameraView(btn.dataset.view);
    }
  });
});

// Manual Synaptic Pulse Firing
if (pulseTriggerBtn) {
  pulseTriggerBtn.addEventListener("click", () => {
    if (visualizer) {
      visualizer.triggerSynapticFiring(1.8);
    }
  });
}

// Regional Equalizer Bars Map
const regionFills = {};
document.querySelectorAll(".region-item").forEach((el) => {
  const reg = el.dataset.region;
  const fill = el.querySelector(".region-bar-fill");
  if (reg && fill) {
    regionFills[reg] = fill;
  }
});

// Socket Events
socket.on("connect", () => {
  socket.emit("spectate");
});

socket.on("brain_activity", ({ node_activity: nodeActivity }) => {
  if (visualizer) {
    visualizer.updateActivity(nodeActivity);
  }
});

socket.on("new_message", () => {
  if (visualizer) {
    visualizer.triggerSynapticFiring(1.5);
  }
});

socket.on("fly_update", (data) => {
  if (data.state && stateBadge) {
    stateBadge.dataset.state = data.state;
    stateBadge.textContent = data.state.toUpperCase();
  }
  if (data.state === "verdict" && visualizer) {
    visualizer.triggerSynapticFiring(2.5);
  }
});

socket.on("region_update", (data) => {
  if (!data || !data.regions) return;
  Object.entries(data.regions).forEach(([region, value]) => {
    const fill = regionFills[region];
    if (!fill) return;
    const t = Math.max(0.1, Math.min(1.0, value));
    fill.style.width = `${Math.round(t * 100)}%`;
  });
});
