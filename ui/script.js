/**
 * script.js — Sauce Dispenser UI Logic  v3
 *
 * Changes from v2:
 *   - START button now calls POST /api/dispense instead of a fake timer
 *   - Polls GET /api/status/{order_id} every second until DONE or FAILED
 *   - Overlay stays up for exactly as long as the machine is running
 *   - Shows a real error message if something goes wrong on the Pi side
 */

/* -----------------------------------------------
   Configuration
----------------------------------------------- */

const API_BASE       = "http://localhost:8080";
const POLL_INTERVAL  = 1000;   // ms between status polls

/* -----------------------------------------------
   State
----------------------------------------------- */

let selectedQuantity = null;
let dispenseTimer    = null;
let pollInterval     = null;

/* -----------------------------------------------
   Element references
----------------------------------------------- */

const quantityButtons = document.querySelectorAll('.qty-btn');
const startButton     = document.getElementById('start-btn');
const startLabel      = document.getElementById('start-label');
const messageOverlay  = document.getElementById('message-overlay');
const messageText     = document.getElementById('message-text');
const progressFill    = document.getElementById('progress-fill');

/* -----------------------------------------------
   Quantity button logic  (unchanged from v2)
----------------------------------------------- */

function handleQuantitySelect(event) {
  const button = event.currentTarget;
  const value  = button.dataset.value;

  if (value === selectedQuantity) return;
  selectedQuantity = value;

  quantityButtons.forEach(btn => {
    const isSelected = btn.dataset.value === value;
    btn.classList.toggle('active',   isSelected);
    btn.classList.toggle('dimmed',  !isSelected);
    btn.setAttribute('aria-pressed', String(isSelected));
  });

  enableStartButton();
  console.log(`[SauceBot] Quantity selected: ${selectedQuantity}`);
}

quantityButtons.forEach(btn => btn.addEventListener('click', handleQuantitySelect));

/* -----------------------------------------------
   Serial helpers — fire-and-forget, never blocks main flow
----------------------------------------------- */

async function sendSerialCommand(endpoint) {
  try {
    const res = await fetch(`${API_BASE}${endpoint}`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    console.log(`[SauceBot] Serial ${endpoint}: ${data.arduino_response}`);
  } catch (err) {
    console.warn(`[SauceBot] Serial command ${endpoint} failed:`, err.message);
  }
}

/* -----------------------------------------------
   START button state
----------------------------------------------- */

function enableStartButton() {
  startButton.disabled = false;
  startButton.setAttribute('aria-disabled', 'false');
  startLabel.textContent = 'START';
}

function disableStartButton() {
  startButton.disabled = true;
  startButton.setAttribute('aria-disabled', 'true');
  startLabel.textContent = 'Select amount first';
}

/* -----------------------------------------------
   START button — now calls the real API
----------------------------------------------- */

startButton.addEventListener('click', async function () {
  if (!selectedQuantity) return;

  // Flash animation
  startButton.classList.add('flash');
  startButton.addEventListener('animationend', () => {
    startButton.classList.remove('flash');
  }, { once: true });

  // Lock controls
  setControlsEnabled(false);

  // Show overlay in "waiting" state while we contact the server
  showOverlay('Connecting...');

  try {
    // ── Step 1: Submit the order ──────────────────────────────
    const response = await fetch(`${API_BASE}/api/dispense`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ level: selectedQuantity.toLowerCase() }),
    });

    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || 'Server error');
    }

    const { order_id } = await response.json();
    console.log(`[SauceBot] Order submitted: ${order_id}`);

    // ── Step 2: Turn on Arduino LED, update overlay, start polling ──
    sendSerialCommand('/sauce/start');
    showOverlay(`Dispensing ${selectedQuantity} sauce...`);
    startPolling(order_id);

  } catch (err) {
    console.error('[SauceBot] Failed to submit order:', err);
    showError(`Could not reach the machine.\n${err.message}`);
  }
});

/* -----------------------------------------------
   Polling — checks order status every second
----------------------------------------------- */

function startPolling(order_id) {
  // Safety: clear any existing poll
  stopPolling();

  pollInterval = setInterval(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/status/${order_id}`);

      if (!response.ok) {
        throw new Error(`Status check failed (${response.status})`);
      }

      const data = await response.json();
      console.log(`[SauceBot] Status: ${data.status}`);

      if (data.status === 'DONE') {
        stopPolling();
        sendSerialCommand('/sauce/stop');
        // Snap bar to 100% before showing completion message
        progressFill.style.transition = 'width 0.3s ease';
        progressFill.style.width = '100%';
        messageText.textContent = 'Done! Enjoy your sandwich.';
        setTimeout(() => {
          hideOverlay();
          resetUI();
        }, 2000);
      }

      else if (data.status === 'FAILED') {
        stopPolling();
        sendSerialCommand('/sauce/stop');
        showError(data.error || 'Something went wrong on the machine.');
      }

      // QUEUED or PROCESSING — keep polling

    } catch (err) {
      console.error('[SauceBot] Polling error:', err);
      stopPolling();
      sendSerialCommand('/sauce/stop');
      showError(`Lost connection to machine.\n${err.message}`);
    }
  }, POLL_INTERVAL);
}

function stopPolling() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

/* -----------------------------------------------
   Overlay helpers
----------------------------------------------- */

function showOverlay(message) {
  messageText.textContent = message;
  messageOverlay.classList.add('visible');

  // Animate progress bar — indefinite pulse while running
  progressFill.style.transition = 'none';
  progressFill.style.width = '0%';
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      // Fill most of the bar over the expected dispense duration
      progressFill.style.transition = `width 10s linear`;
      progressFill.style.width = '95%';   // Never quite reaches 100% until DONE
    });
  });
}

function hideOverlay() {
  messageOverlay.classList.remove('visible');
  progressFill.style.transition = 'none';
  progressFill.style.width = '0%';
}

function showError(message) {
  // Replace spinner content with an error state
  messageText.textContent = `⚠ ${message}`;
  progressFill.style.transition = 'none';
  progressFill.style.width = '0%';

  // Auto-dismiss after 4 seconds then reset
  setTimeout(() => {
    hideOverlay();
    resetUI();
  }, 4000);
}

/* -----------------------------------------------
   UI helpers  (unchanged from v2)
----------------------------------------------- */

function setControlsEnabled(enabled) {
  quantityButtons.forEach(btn => { btn.disabled = !enabled; });
  if (enabled && selectedQuantity) {
    enableStartButton();
  } else if (!enabled) {
    disableStartButton();
  }
}

function resetUI() {
  selectedQuantity = null;

  quantityButtons.forEach(btn => {
    btn.classList.remove('active', 'dimmed');
    btn.setAttribute('aria-pressed', 'false');
    btn.disabled = false;
  });

  disableStartButton();
  console.log('[SauceBot] Ready for next order.');
}

/* -----------------------------------------------
   Log panel
----------------------------------------------- */

const gearBtn      = document.getElementById('gear-btn');
const logPanel     = document.getElementById('log-panel');
const logBackdrop  = document.getElementById('log-backdrop');
const logList      = document.getElementById('log-list');
const logRefreshBtn = document.getElementById('log-refresh-btn');
const logCloseBtn  = document.getElementById('log-close-btn');

function openLogPanel() {
  logPanel.hidden = false;
  logBackdrop.classList.add('open');
  fetchLogs();
}

function closeLogPanel() {
  logPanel.hidden = true;
  logBackdrop.classList.remove('open');
}

async function fetchLogs() {
  logList.innerHTML = '<li class="log-empty" style="grid-column:1/-1">Loading...</li>';
  try {
    const res = await fetch(`${API_BASE}/api/logs`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { logs } = await res.json();

    if (!logs || logs.length === 0) {
      logList.innerHTML = '<li class="log-empty" style="grid-column:1/-1">No log entries yet.</li>';
      return;
    }

    // Render newest-first (reversed ol handles the visual order)
    logList.innerHTML = logs.slice().reverse().map(entry => {
      const levelClass = `log-level-${entry.level}`;
      const safeMsg    = entry.message.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      return `<li>
        <span class="log-time">${entry.time}</span>
        <span class="log-level ${levelClass}">${entry.level}</span>
        <span class="log-msg">${safeMsg}</span>
      </li>`;
    }).join('');
  } catch (err) {
    logList.innerHTML = `<li class="log-empty" style="grid-column:1/-1">Could not load logs: ${err.message}</li>`;
  }
}

gearBtn.addEventListener('click', openLogPanel);
logCloseBtn.addEventListener('click', closeLogPanel);
logBackdrop.addEventListener('click', closeLogPanel);
logRefreshBtn.addEventListener('click', fetchLogs);

/* -----------------------------------------------
   Drag-to-scroll on the log list (mouse + touch)
----------------------------------------------- */

(function initDragScroll(el) {
  let startY    = 0;
  let startTop  = 0;
  let dragging  = false;

  // ── Mouse ──────────────────────────────────────
  el.addEventListener('mousedown', e => {
    dragging  = true;
    startY    = e.clientY;
    startTop  = el.scrollTop;
    el.classList.add('dragging');
    e.preventDefault();
  });

  window.addEventListener('mousemove', e => {
    if (!dragging) return;
    el.scrollTop = startTop - (e.clientY - startY);
  });

  window.addEventListener('mouseup', () => {
    dragging = false;
    el.classList.remove('dragging');
  });

  // ── Touch ──────────────────────────────────────
  el.addEventListener('touchstart', e => {
    startY   = e.touches[0].clientY;
    startTop = el.scrollTop;
  }, { passive: true });

  el.addEventListener('touchmove', e => {
    el.scrollTop = startTop - (e.touches[0].clientY - startY);
  }, { passive: true });

}(logList));

/* -----------------------------------------------
   Debug buttons + Manual controls modal
----------------------------------------------- */

const debugManualBtn   = document.getElementById('debug-manual-btn');
const debugRestartBtn  = document.getElementById('debug-restart-btn');
const manualModal      = document.getElementById('manual-modal');
const manualModalClose = document.getElementById('manual-modal-close');
const gantryModal      = document.getElementById('gantry-modal');
const gantryModalClose = document.getElementById('gantry-modal-close');
const gantryLocationGrid = document.getElementById('gantry-location-grid');

const debugHomeGrabberBtn  = document.getElementById('debug-home-grabber-btn');
const debugHomeExtruderBtn = document.getElementById('debug-home-extruder-btn');
const debugCloseGrabberBtn = document.getElementById('debug-close-grabber-btn');
const debugOpenGrabberBtn  = document.getElementById('debug-open-grabber-btn');
const debugOpenExtruderBtn = document.getElementById('debug-open-extruder-btn');
const debugMeetPlungerBtn  = document.getElementById('debug-meet-plunger-btn');
const debugMoveGantryBtn   = document.getElementById('debug-move-gantry-btn');

// Open / close manual controls modal
debugManualBtn.addEventListener('click', () => { manualModal.hidden = false; });
manualModalClose.addEventListener('click', () => { manualModal.hidden = true; });
manualModal.addEventListener('click', e => {
  if (e.target === manualModal) manualModal.hidden = true;
});

// Move Gantry — fetch positions then open location picker
debugMoveGantryBtn.addEventListener('click', async () => {
  try {
    const res = await fetch(`${API_BASE}/api/manual/gantry-positions`);
    const { positions } = await res.json();

    // Build a button for each location
    gantryLocationGrid.innerHTML = positions.map(loc =>
      `<button class="log-debug-btn" data-location="${loc}">${loc.charAt(0).toUpperCase() + loc.slice(1)}</button>`
    ).join('');

    gantryLocationGrid.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', () => {
        const loc = btn.dataset.location;
        gantryModal.hidden = true;
        debugAction(`/api/manual/move-gantry/${loc}`, btn, 'Moving...');
        fetchLogs();
      });
    });

    gantryModal.hidden = false;
  } catch (err) {
    console.warn('[SauceBot] Could not load gantry positions:', err.message);
  }
});

gantryModalClose.addEventListener('click', () => { gantryModal.hidden = true; });
gantryModal.addEventListener('click', e => {
  if (e.target === gantryModal) gantryModal.hidden = true;
});

async function debugAction(endpoint, btn, workingLabel) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = workingLabel;
  try {
    const res = await fetch(`${API_BASE}${endpoint}`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    btn.textContent = 'Done!';
  } catch (err) {
    btn.textContent = 'Error';
    console.warn(`[SauceBot] ${endpoint} failed:`, err.message);
  } finally {
    fetchLogs();
    setTimeout(() => {
      btn.textContent = original;
      btn.disabled = false;
    }, 2000);
  }
}

debugHomeGrabberBtn.addEventListener('click',  () => debugAction('/api/manual/home-grabber',  debugHomeGrabberBtn,  'Running...'));
debugHomeExtruderBtn.addEventListener('click', () => debugAction('/api/manual/home-extruder', debugHomeExtruderBtn, 'Running...'));
debugCloseGrabberBtn.addEventListener('click', () => debugAction('/api/manual/close-grabber', debugCloseGrabberBtn, 'Running...'));
debugOpenGrabberBtn.addEventListener('click',  () => debugAction('/api/manual/open-grabber',  debugOpenGrabberBtn,  'Running...'));
debugOpenExtruderBtn.addEventListener('click', () => debugAction('/api/manual/open-extruder', debugOpenExtruderBtn, 'Running...'));
debugMeetPlungerBtn.addEventListener('click',  () => debugAction('/api/manual/meet-plunger',  debugMeetPlungerBtn,  'Running...'));
debugRestartBtn.addEventListener('click',      () => debugAction('/api/debug/restart',         debugRestartBtn,      'Restarting...'));
