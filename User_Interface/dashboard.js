const drillData = {
    beginner: [
        { id: 1, name: "Forehand Counter" },
        { id: 2, name: "Backhand Counter" },
        { id: 3, name: "Basic Push" }
    ],
    intermediate: [
        { id: 4, name: "Topspin Drive" },
        { id: 5, name: "Active Block" },
        { id: 6, name: "Pivot Forehand" }
    ],
    advanced: [
        { id: 7, name: "Falkenberg Drill" },
        { id: 8, name: "Random Topspin" },
        { id: 9, name: "Short Touch" }
    ]
};

let ws;
let selectedDrill = null;

let totalShots = 0;
let successfulHits = 0;
let currentStreak = 0;
let maxStreak = 0;
let backendAccuracy = 0; 

let velocityLog = [];

const canvas = document.getElementById('tt-board');
const ctx = canvas.getContext('2d');
const scale = 0.4; 

async function syncStatsWithBackend() {
    try {
        const response = await fetch('/api/session');
        if (!response.ok) return; 
        
        const sessionData = await response.json();

        totalShots = sessionData.total_shots || 0;
        successfulHits = sessionData.hit_count || 0;
        currentStreak = sessionData.streak || 0;
        maxStreak = sessionData.best_streak || 0;
        backendAccuracy = sessionData.accuracy || 0; 

        if (sessionData.active && !document.getElementById('screen-active').classList.contains('active')) {
            showScreen('screen-active');
        }
    } catch (error) {
        console.error("Could not sync with backend:", error);
    }
}

function showScreen(screenId) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(screenId).classList.add('active');
}

function selectLevel(level) {
    const container = document.getElementById('sub-buttons-container');
    container.innerHTML = ""; 
    
    drillData[level].forEach(drill => {
        const btn = document.createElement('button');
        btn.className = 'cyber-btn mode-btn normal';
        btn.innerText = drill.name;
        btn.onclick = () => startDrill(drill.id);
        container.appendChild(btn);
    });

    showScreen('screen-sub');
}

function startDrill(drillId) {
    selectedDrill = drillId;
    velocityLog = [];
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Set Title
    let drillName = "DRILL";
    for (const category in drillData) {
        const found = drillData[category].find(d => d.id === drillId);
        if (found) drillName = found.name;
    }
    document.getElementById('active-drill-text').innerText = drillName;

    showScreen('screen-active');

    if(ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "start_drill", drill_id: drillId }));
    }
}

function endDrill() {
    if(ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "stop_drill" }));
    }

    const avgVel = velocityLog.length > 0 ? Math.round(velocityLog.reduce((a, b) => a + b, 0) / velocityLog.length) : 0;

    document.getElementById('sum-accuracy').innerText = `${backendAccuracy}%`;
    document.getElementById('sum-streak').innerText = maxStreak;
    document.getElementById('sum-vel').innerText = avgVel;
    document.getElementById('sum-total').innerText = totalShots;

    showScreen('screen-summary');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
}

function emergencyStop() {
    if(ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "stop_drill" })); 
    }
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    showScreen('screen-level');
}

function initWebSocket() {
    // Automatically switch between ws:// (local) and wss:// (internet/ngrok)
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
    
    ws.onopen = () => {
        document.getElementById('status').innerText = "WS: Connected";
        document.getElementById('status').style.color = "var(--neon-green)";
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        
        if (data.event === "shot_result" && document.getElementById('screen-active').classList.contains('active')) {
            drawImpact(data.impact_coords.y, data.impact_coords.z, data.success);
            if (data.velocity) velocityLog.push(data.velocity);
            syncStatsWithBackend();
        }
    };

    ws.onclose = () => {
        document.getElementById('status').innerText = "WS: Disconnected";
        document.getElementById('status').style.color = "var(--neon-red)";
        setTimeout(initWebSocket, 2000); 
    };
}

function drawImpact(y_mm, z_mm, success) {
    const x_px = (y_mm + 762.5) * scale;
    const y_px = canvas.height - (z_mm * scale);

    ctx.beginPath();
    ctx.arc(x_px, y_px, 6, 0, 2 * Math.PI);
    ctx.fillStyle = success ? 'var(--neon-green)' : 'var(--neon-red)';
    ctx.fill();
    
    ctx.shadowBlur = 10;
    ctx.shadowColor = ctx.fillStyle;
    ctx.fill();
    ctx.shadowBlur = 0; 
}

syncStatsWithBackend();
initWebSocket();
