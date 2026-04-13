/**
 * Table Tennis Robot — WebSocket Command Helpers
 *
 * These functions wrap raw WebSocket sends with UI feedback.
 * `ws` is declared in index.html and lives in the shared global scope.
 *
 * Element IDs used here must match index.html:
 *   #status      — connection / command status label in the header
 *   .giant-btn   — all tappable drill buttons
 *
 * Action names must match server.py handle_message():
 *   "start_drill" { drill_id: int }  — maps int 1-9 to "BEG/INT/ADV_0N" server-side
 *   "stop_drill"                     — stops drill; server broadcasts drill_stopped
 *   "get_state"                      — server replies with sync_state event
 */

// ─── Internal helper ────────────────────────────────────────────────────────

/**
 * Set the header status label (#status) text and colour.
 */
function _setStatus(text, colour) {
    const el = document.getElementById('status');
    if (!el) return;
    const colourMap = {
        green:  'var(--neon-green)',
        orange: 'orange',
        red:    'var(--neon-red)',
        white:  'var(--text-main)',
    };
    el.innerText = text;
    el.style.color = colourMap[colour] || colour;
}

/**
 * Guard: returns true and shows an error if the WebSocket is not ready.
 */
function _wsNotReady() {
    if (typeof ws === 'undefined' || ws.readyState !== WebSocket.OPEN) {
        _setStatus('WS: Not Connected', 'red');
        return true;
    }
    return false;
}


// ─── Public API ──────────────────────────────────────────────────────────────

/**
 * Send a drill start or stop command over the WebSocket.
 *
 * For drill_id 99 (legacy emergency stop path) it sends stop_drill.
 * For all other IDs it sends start_drill so the server state machine resets.
 *
 * @param {number|string} drillId  — integer 1-9, or 99 for emergency stop
 */
function sendDrillCommand(drillId) {
    const drillIdInt = Number(drillId);
    const buttons    = document.querySelectorAll('.giant-btn');

    // 1. Lock all buttons immediately — prevent rapid-fire taps
    buttons.forEach(btn => btn.disabled = true);
    _setStatus(`⚙️ Sending Command ${drillIdInt}...`, 'orange');

    // 2. Abort if socket is not ready
    if (_wsNotReady()) {
        buttons.forEach(btn => btn.disabled = false);
        return;
    }

    try {
        // 3. Route to the correct server action
        if (drillIdInt === 99) {
            ws.send(JSON.stringify({ action: "stop_drill" }));
        } else {
            // start_drill: server maps int -> "BEG_01" etc. and resets session state
            ws.send(JSON.stringify({ action: "start_drill", drill_id: drillIdInt }));
        }

        _setStatus(`✅ Command Sent: Drill ${drillIdInt}`, 'green');

    } catch (err) {
        _setStatus(`❌ Send Failed: ${err.message}`, 'red');
        console.error("WS send error:", err);
    } finally {
        // 4. Hardware cooldown — give motors time to respond before next command
        const currentText = document.getElementById('status').innerText;
        _setStatus(currentText + ' (Cooling down...)', 'orange');

        setTimeout(() => {
            buttons.forEach(btn => btn.disabled = false);
            const el = document.getElementById('status');
            if (el && el.innerText.includes('Cooling down')) {
                _setStatus('WS: Connected', 'green');
            }
        }, 3000);
    }
}

/**
 * Request a full state sync from the server.
 * The server replies with a "sync_state" event handled in index.html onmessage.
 */
function requestStateSync() {
    if (_wsNotReady()) return;
    ws.send(JSON.stringify({ action: "get_state" }));
}
