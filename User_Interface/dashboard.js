let ws;
const canvas = document.getElementById('tt-board');
const ctx = canvas.getContext('2d');
const scale = 0.4; // 1525mm * 0.4 = 610px width

canvas.width = 1525 * scale;
canvas.height = 1000 * scale;

function initWS() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        const s = document.getElementById('status');
        s.innerText = "Online"; s.style.color = "var(--green)";
    };

    ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.event === "shot_result") {
            drawImpact(data.impact_coords.y, data.impact_coords.z, data.success);
            updateStats(data);
        } else if (data.event === "drill_started") {
            showScreen('screen-active');
        } else if (data.event === "drill_stopped") {
            showScreen('screen-summary');
        }
    };
}

function drawImpact(y_mm, z_mm, success) {
    // Convert table mm (center 0) to canvas px
    const x = (y_mm + 762.5) * scale;
    const y = canvas.height - (z_mm * scale);

    ctx.beginPath();
    ctx.arc(x, y, 6, 0, Math.PI * 2);
    ctx.fillStyle = success ? "#39ff14" : "#ff073a";
    ctx.fill();
}

function updateStats(data) {
    document.getElementById('live-accuracy').innerText = `Acc: ${data.accuracy}%`;
    document.getElementById('sum-accuracy').innerText = `${data.accuracy}%`;
    document.getElementById('sum-total').innerText = data.total_shots;
}

function startDrill(id) {
    ws.send(JSON.stringify({ action: "start_drill", drill_id: id }));
    ctx.clearRect(0, 0, canvas.width, canvas.height);
}

function endDrill() {
    ws.send(JSON.stringify({ action: "stop_drill" }));
}

function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
    document.getElementById(id).classList.add('active');
}

initWS();
