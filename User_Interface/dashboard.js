let ws;
const canvas = document.getElementById('tt-board');
const ctx = canvas ? canvas.getContext('2d') : null;
const shotHistory = [];
const activeAnimations = [];

// MODIFIED FOR UI TESTING: Default to true so buttons are clickable locally
let isLeader = true; 

let activeZone = 0;          // current target zone (1-8); 0 = none

const ZONE_COUNT = 8;
const COORD_WIDTH = 1920;    // coordinate-space width used by CV / server
const COORD_HEIGHT = 1080;

function getPersistentUID() {
    let uid = localStorage.getItem('t4_uid');
    if (!uid) {
        uid = Math.random().toString(36).substring(2, 11);
        localStorage.setItem('t4_uid', uid);
    }
    return uid;
}

function resizeCanvas() {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width;
    canvas.height = rect.height;
}
window.addEventListener('resize', resizeCanvas);

// ─── Zone helpers ────────────────────────────────────────────────────────────

/** Returns the zone number (1-8) for a given x in coordinate space. */
function getZoneFromX(x) {
    return Math.min(ZONE_COUNT, Math.floor(x / (COORD_WIDTH / ZONE_COUNT)) + 1);
}

/** Updates the zone indicator text element (if present in the DOM). */
function updateZoneIndicator(zone) {
    const el = document.getElementById('active-zone-num');
    if (el) el.innerText = zone > 0 ? zone : '—';
}

// ─── Canvas rendering ─────────────────────────────────────────────────────────

/**
 * Draws the 8-zone grid as the bottom layer of the canvas.
 * • All zones: faint dashed dividers + zone number
 * • Active zone: green tinted fill + solid glowing border
 */
function drawZones() {
    if (!ctx) return;
    const zoneW = canvas.width / ZONE_COUNT;
    const labelSize = Math.max(9, Math.floor(canvas.width * 0.013));

    for (let i = 0; i < ZONE_COUNT; i++) {
        const zoneNum = i + 1;
        const isActive = zoneNum === activeZone;
        const x = i * zoneW;

        // Background fill
        ctx.fillStyle = isActive
            ? 'rgba(0, 255, 80, 0.09)'
            : 'rgba(200, 232, 240, 0.02)';
        ctx.fillRect(x, 0, zoneW, canvas.height);

        // Dashed divider (skip first edge)
        if (i > 0) {
            ctx.save();
            ctx.beginPath();
            ctx.strokeStyle = 'rgba(200, 232, 240, 0.18)';
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 6]);
            ctx.moveTo(x, 0);
            ctx.lineTo(x, canvas.height);
            ctx.stroke();
            ctx.restore();
        }

        // Zone number label
        ctx.save();
        ctx.fillStyle = isActive
            ? 'rgba(0, 255, 80, 0.85)'
            : 'rgba(200, 232, 240, 0.28)';
        ctx.font = `700 ${labelSize}px 'Fira Code', monospace`;
        ctx.textAlign = 'center';
        ctx.fillText(`Z${zoneNum}`, x + zoneW / 2, labelSize + 4);
        ctx.restore();
    }

    // Active zone glowing border (drawn last so it sits on top of fills)
    if (activeZone > 0) {
        const ax = (activeZone - 1) * zoneW;
        ctx.save();
        ctx.strokeStyle = '#00FF50';
        ctx.lineWidth = 2;
        ctx.shadowColor = '#00FF50';
        ctx.shadowBlur = 12;
        ctx.strokeRect(ax + 1, 1, zoneW - 2, canvas.height - 2);
        // Second pass for a stronger inner glow
        ctx.shadowBlur = 4;
        ctx.lineWidth = 1;
        ctx.strokeRect(ax + 3, 3, zoneW - 6, canvas.height - 6);
        ctx.restore();
    }
}

function animate() {
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Layer 1 – zone grid
    drawZones();

    // Layer 2 – shot history dots
    shotHistory.forEach(s => {
        const px = (s.x / COORD_WIDTH) * canvas.width;
        const py = (s.y / COORD_HEIGHT) * canvas.height;
        const inZone = getZoneFromX(s.x) === activeZone;
        const color = s.success ? '#00FF50' : '#FF3333';

        ctx.save();
        ctx.beginPath();
        ctx.arc(px, py, 6, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.shadowColor = color;
        ctx.shadowBlur = 10;
        ctx.fill();
        ctx.restore();
    });

    // Layer 3 – impact animations (corner-bracket burst)
    const now = Date.now();
    for (let i = activeAnimations.length - 1; i >= 0; i--) {
        const a = activeAnimations[i];
        const progress = (now - a.startTime) / a.duration;
        if (progress >= 1) { activeAnimations.splice(i, 1); continue; }

        const px = (a.x / COORD_WIDTH) * canvas.width;
        const py = (a.y / COORD_HEIGHT) * canvas.height;
        const size = 80 * (1 - progress);
        const b = size * 0.4;

        ctx.save();
        ctx.strokeStyle = a.color;
        ctx.globalAlpha = 1 - progress;
        ctx.lineWidth = 2;
        ctx.shadowColor = a.color;
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.moveTo(px - size, py - b);  ctx.lineTo(px - size, py - size); ctx.lineTo(px - b, py - size);
        ctx.moveTo(px + b, py - size);  ctx.lineTo(px + size, py - size); ctx.lineTo(px + size, py - b);
        ctx.moveTo(px + size, py + b);  ctx.lineTo(px + size, py + size); ctx.lineTo(px + b, py + size);
        ctx.moveTo(px - b, py + size);  ctx.lineTo(px - size, py + size); ctx.lineTo(px - size, py + b);
        ctx.stroke();
        ctx.restore();
    }

    requestAnimationFrame(animate);
}

// ─── Drill controls (MODIFIED FOR LOCAL UI TESTING) ───────────────────────────

function handleStart(id) { 
    // Send to WS if connected
    if (isLeader && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'start_drill', drill_id: id })); 
    }
    // Force UI transition locally
    showScreen('screen-active'); 
}

function handlePause() { 
    if (isLeader && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'pause_drill' })); 
    }
    // Toggle pause button text locally
    const btn = document.getElementById('pause-btn');
    btn.innerText = btn.innerText === 'PAUSE' ? 'RESUME' : 'PAUSE';
}

function handleEnd() { 
    if (isLeader && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'stop_drill' })); 
    }
    // Force UI transition to summary locally
    showScreen('screen-summary'); 
}

function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    if (id === 'screen-active') setTimeout(resizeCanvas, 100);
}

// ─── WebSocket ────────────────────────────────────────────────────────────────

function initWS() {
    const uid = getPersistentUID();
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${protocol}://${location.host}/ws/${uid}`);

    ws.onmessage = (e) => {
        const data = JSON.parse(e.data);

        // ── Full state sync on (re)connect ──
        if (data.event === 'session_sync') {
            if (data.active) {
                shotHistory.length = 0;
                data.history.forEach(s => shotHistory.push(s));
                document.getElementById('live-accuracy').innerText = data.accuracy;
                document.getElementById('live-shots').innerText   = data.total_shots;
                document.getElementById('live-streak').innerText  = data.streak;
                document.getElementById('pause-btn').innerText    = data.paused ? 'RESUME' : 'PAUSE';
                activeZone = data.active_zone || 0;
                updateZoneIndicator(activeZone);
                showScreen('screen-active');
            }
        }

        // ── Queue / leader status ──
        if (data.event === 'queue_update') {
            isLeader = data.is_leader;
            document.getElementById('queue-status').innerText = isLeader ? 'ROLE: PLAYER' : `QUEUE: ${data.position}/${data.total}`;
            document.getElementById('controls-lock').style.opacity       = isLeader ? '1' : '0.3';
            document.getElementById('wait-msg-level').style.display      = isLeader ? 'none' : 'block';
            document.getElementById('wait-msg-active').style.display     = isLeader ? 'none' : 'block';
            document.getElementById('active-controls').style.opacity      = isLeader ? '1' : '0.3';
            document.getElementById('active-controls').style.pointerEvents = isLeader ? 'auto' : 'none';
        }

        // ── Drill lifecycle ──
        if (data.event === 'drill_started') {
            shotHistory.length = 0;
            activeZone = data.active_zone || 0;
            updateZoneIndicator(activeZone);
            showScreen('screen-active');
        }

        if (data.event === 'drill_paused') {
            document.getElementById('pause-btn').innerText = data.paused ? 'RESUME' : 'PAUSE';
        }

        if (data.event === 'drill_stopped') {
            activeAnimations.length = 0;
            activeZone = 0;
            updateZoneIndicator(0);
            showScreen('screen-summary');
        }

        // ── Live shot result ──
        if (data.event === 'shot_result') {
            const { x, y } = data.impact_coords;
            shotHistory.push({ x, y, success: data.success });

            // Keep active zone in sync with server (launcher may change it each shot)
            if (data.active_zone !== undefined) {
                activeZone = data.active_zone;
                updateZoneIndicator(activeZone);
            }

            const color = data.success ? '#00FF50' : '#FF3333';
            activeAnimations.push({
                x, y,
                startTime: Date.now(),
                duration: 350,
                color,
            });

            document.getElementById('live-accuracy').innerText = data.accuracy;
            document.getElementById('live-shots').innerText   = data.total_shots;
            document.getElementById('live-streak').innerText  = data.streak;
            document.getElementById('sum-accuracy').innerText = data.accuracy + '%';
            document.getElementById('sum-total').innerText    = data.total_shots;
        }

        // ── Launcher explicitly changed the target zone (between shots) ──
        if (data.event === 'zone_changed') {
            activeZone = data.active_zone;
            updateZoneIndicator(activeZone);
        }
    };

    ws.onopen  = () => (document.getElementById('conn-status').innerText = 'ONLINE');
    ws.onclose = () => setTimeout(initWS, 2000);
}

// ─── Initialization Override for UI Testing ───────────────────────────────────
document.getElementById('controls-lock').style.opacity = '1';
document.getElementById('queue-status').innerText = 'UI TESTING MODE';

initWS();
animate();
