let ws;
let selectedDrill = null;
let totalShots = 0;
let backendAccuracy = 0; 

function showScreen(screenId) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(screenId).classList.add('active');
}

// --- WEBSOCKET ENGINE ---

function initWebSocket() {
    try {
        if (!window.location.host) throw new Error("No host found. Running locally?");
        
        ws = new WebSocket(`ws://${window.location.host}/ws`);
        const statusEl = document.getElementById('status');
        
        ws.onopen = () => {
            statusEl.innerText = "Sys: Online [120Hz]";
            statusEl.style.color = "var(--neon-green)";
            syncStatsWithBackend();
        };

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            
            // A shot happened! We don't draw it anymore, just sync the math.
            if (data.event === "shot_result") {
                syncStatsWithBackend();
            }
            
            // Handle stat synchronization payload
            if (data.event === "stats_update") {
                totalShots = data.total_shots || 0;
                backendAccuracy = data.accuracy || 0;
                
                // Recover active screen if refreshed
                if (data.active && !document.getElementById('screen-active').classList.contains('active')) {
                    showScreen('screen-active');
                }
            }
        };

        ws.onclose = () => {
            statusEl.innerText = "Sys: Connection Lost";
            statusEl.style.color = "var(--neon-red)";
            setTimeout(initWebSocket, 2000); 
        };
    } catch (error) {
        console.warn("WebSocket failed to initialize:", error.message);
        document.getElementById('status').innerText = "Sys: Offline (Local Mode)";
    }
}

// --- DRILL CONTROLS ---

function syncStatsWithBackend() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "sync_stats" }));
    }
}

function startDrill(drillId) {
    selectedDrill = drillId;
    
    // Update the UI text to show what is currently running
    const formattedName = drillId.replace('_', ' ').toUpperCase();
    document.getElementById('active-drill-text').innerText = formattedName;
    
    showScreen('screen-active');

    if(ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "start_drill", drill_id: drillId }));
    }
}

function endDrill() {
    if(ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "stop_drill" }));
    }
    // Populate the final math on the summary screen
    document.getElementById('sum-accuracy').innerHTML = `${backendAccuracy}<span style="font-size: 2rem">%</span>`;
    document.getElementById('sum-total').innerText = totalShots;
    showScreen('screen-summary');
}

function emergencyStop() {
    if(ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "emergency_stop" })); 
    }
    showScreen('screen-level');
}

// Boot up
initWebSocket();
