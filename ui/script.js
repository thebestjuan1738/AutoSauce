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

    // ── Step 2: Update overlay and start polling ──────────────
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
        showOverlay('Done! Enjoy your sandwich.');
        setTimeout(() => {
          hideOverlay();
          resetUI();
        }, 2000);
      }

      else if (data.status === 'FAILED') {
        stopPolling();
        showError(data.error || 'Something went wrong on the machine.');
      }

      // QUEUED or PROCESSING — keep polling

    } catch (err) {
      console.error('[SauceBot] Polling error:', err);
      stopPolling();
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
      // Use a long duration so it fills slowly while polling
      progressFill.style.transition = `width 30s linear`;
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
