/**
 * script.js ï¿½ Sauce Dispenser UI Logic  v2
 *
 * Responsibilities:
 *  - Track selected sauce quantity
 *  - Drive button active / dimmed states
 *  - Animate status display on selection change
 *  - Manage START button enabled state and label
 *  - Show dispensing overlay with spinner + progress bar
 *  - Reset UI cleanly after dispense completes
 *
 * Raspberry Pi integration point:
 *  Search for "TODO (GPIO)" to find where to add your fetch() / WebSocket call.
 */

/* -----------------------------------------------
   Configuration
------------------------------------------------ */

/** Total time (ms) the dispensing overlay is shown before auto-reset. */
const DISPENSE_DURATION_MS = 2800;

/* -----------------------------------------------
   State
------------------------------------------------ */

/** Currently selected quantity label, or null if nothing chosen. */
let selectedQuantity = null;

/** Timer handle for hiding the overlay. */
let dispenseTimer = null;

/* -----------------------------------------------
   Element references
------------------------------------------------ */

const quantityButtons = document.querySelectorAll('.qty-btn');
const startButton     = document.getElementById('start-btn');
const startLabel      = document.getElementById('start-label');
const messageOverlay  = document.getElementById('message-overlay');
const messageText     = document.getElementById('message-text');
const progressFill    = document.getElementById('progress-fill');



/* -----------------------------------------------
   Quantity button logic
------------------------------------------------ */

/**
 * Handle a tap on a quantity button.
 * Updates state, button visuals, status box, and START button.
 */
function handleQuantitySelect(event) {
  const button = event.currentTarget;
  const value  = button.dataset.value;  // 'Light' | 'Medium' | 'Heavy'

  // No-op if already selected (avoids unnecessary re-renders)
  if (value === selectedQuantity) return;

  selectedQuantity = value;

  // Update button visual states
  quantityButtons.forEach(btn => {
    const isSelected = btn.dataset.value === value;
    btn.classList.toggle('active',  isSelected);
    btn.classList.toggle('dimmed', !isSelected);
    btn.setAttribute('aria-pressed', String(isSelected));
  });

  // Enable START button
  enableStartButton();

  console.log(`[Sauce Dispenser] Quantity selected: ${selectedQuantity}`);
}

// Attach listeners
quantityButtons.forEach(btn => btn.addEventListener('click', handleQuantitySelect));

/* -----------------------------------------------
   START button state management
------------------------------------------------ */

/** Activate the START button -- called when a quantity is chosen. */
function enableStartButton() {
  startButton.disabled = false;
  startButton.setAttribute('aria-disabled', 'false');
  startLabel.textContent = 'START';
}

/** Deactivate the START button -- called on reset or during dispense. */
function disableStartButton() {
  startButton.disabled = true;
  startButton.setAttribute('aria-disabled', 'true');
  startLabel.textContent = 'Select amount first';
}

/* -----------------------------------------------
   START button press handler
------------------------------------------------ */

startButton.addEventListener('click', function () {
  if (!selectedQuantity) {
    console.warn('[Sauce Dispenser] START pressed with no quantity selected.');
    return;
  }

  const quantity = selectedQuantity;

  // Log to console
  // TODO (GPIO): Replace this log with your Raspberry Pi API call, e.g.:
  //   fetch('/api/dispense', { method: 'POST', body: JSON.stringify({ quantity }) });
  console.log('[Sauce Dispenser] Dispensing ' + quantity + ' sauce...');

  // Flash animation on the button
  startButton.classList.add('flash');
  startButton.addEventListener('animationend', () => {
    startButton.classList.remove('flash');
  }, { once: true });

  // Lock controls while dispensing
  setControlsEnabled(false);

  // Show overlay
  showDispensingOverlay(quantity);
});

/* -----------------------------------------------
   Dispensing overlay
------------------------------------------------ */

/**
 * Show the full-screen dispensing overlay with spinner + progress bar.
 * @param {string} quantity ï¿½ The quantity being dispensed
 */
function showDispensingOverlay(quantity) {
  // Clear any leftover timer from a previous cycle
  if (dispenseTimer) {
    clearTimeout(dispenseTimer);
    dispenseTimer = null;
  }

  // Set message text
  messageText.textContent = 'Dispensing ' + quantity + ' Sauce...';

  // Reset and animate the progress bar
  // Set width to 0 instantly (no transition), then animate to 100%
  progressFill.style.transition = 'none';
  progressFill.style.width = '0%';

  // Show the overlay
  messageOverlay.classList.add('visible');

  // Start progress bar animation on next frame (after overlay is visible)
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      progressFill.style.transition = `width ${DISPENSE_DURATION_MS}ms linear`;
      progressFill.style.width = '100%';
    });
  });

  // Auto-dismiss after the duration
  dispenseTimer = setTimeout(() => {
    hideDispensingOverlay();
    resetUI();
  }, DISPENSE_DURATION_MS);
}

/** Hide the dispensing overlay. */
function hideDispensingOverlay() {
  messageOverlay.classList.remove('visible');

  // Reset progress bar immediately (hidden, no flash)
  progressFill.style.transition = 'none';
  progressFill.style.width = '0%';

  dispenseTimer = null;
}

/* -----------------------------------------------
   UI helpers
------------------------------------------------ */

/**
 * Enable or disable quantity buttons and START button.
 * @param {boolean} enabled
 */
function setControlsEnabled(enabled) {
  quantityButtons.forEach(btn => { btn.disabled = !enabled; });
  if (enabled && selectedQuantity) {
    enableStartButton();
  } else if (!enabled) {
    disableStartButton();
  }
}

/**
 * Reset the entire UI to its initial idle state.
 * Called automatically after dispensing completes.
 */
function resetUI() {
  selectedQuantity = null;

  // Clear all button states
  quantityButtons.forEach(btn => {
    btn.classList.remove('active', 'dimmed');
    btn.setAttribute('aria-pressed', 'false');
    btn.disabled = false;
  });

  disableStartButton();

  console.log('[Sauce Dispenser] Ready for next dispense.');
}
