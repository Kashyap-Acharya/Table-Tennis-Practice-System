
/**
 * Table Tennis Robot - Frontend API Client
 * Handles communication between the HTML UI and the Raspberry Pi FastAPI server.
 */

async function sendDrillCommand(drillId) {
    const statusBox = document.getElementById('status-box');
    const buttons = document.querySelectorAll('.drill-btn');
    
    // 1. UI Update & Global Anti-Spam
    // Lock all buttons immediately so the user can't rapid-fire commands
    buttons.forEach(btn => btn.disabled = true); 
    statusBox.innerText = `⚙️ Sending Command ${drillId}...`; 
    statusBox.style.color = "orange";

    // 2. The Type-Safe Payload
    // Forces the ID to be an integer so Python doesn't crash
    const payload = {
        drill_id: Number(drillId) 
    };

    // 3. The Network Timeout (5 seconds max)
    // Prevents the browser from hanging forever if the Wi-Fi drops
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);

    try {
        // 4. Send the POST request to the backend
        const response = await fetch('/api/command', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload),
            signal: controller.signal 
        });

        // 5. Handle Response & Browser Memory
        if (response.ok) {
            statusBox.innerText = `✅ Success: Executing Drill ${drillId}`; 
            statusBox.style.color = "#00ff00";

            // If it's the Stop command (99), clear memory. Otherwise, remember the drill is running.
            if (payload.drill_id === 99) {
                localStorage.removeItem("drillRunning"); 
            } else {
                localStorage.setItem("drillRunning", "true"); 
            }

        } else {
            statusBox.innerText = `❌ Server Error (Code: ${response.status})`; 
            statusBox.style.color = "red";
        }

    } catch (error) {
        // 6. Handle Network Drop or Timeout
        if (error.name === 'AbortError') {
            statusBox.innerText = `⏳ Request Timed Out. Pi took too long!`;
        } else {
            statusBox.innerText = `❌ Network Error: Could not connect to the Pi.`;
        }
        statusBox.style.color = "red";
        console.error("Fetch error:", error);
        
    } finally {
        // 7. Cleanup & Hardware Cooldown
        clearTimeout(timeoutId); 
        
        statusBox.innerText += " (Cooling down...)";
        
        // Wait 3 seconds before unlocking the UI to give physical motors time to adjust
        setTimeout(() => {
            buttons.forEach(btn => btn.disabled = false);
            
            // Reset the status text to ready
            if (statusBox.innerText.includes("Cooling down")) {
                statusBox.innerText = "System Ready";
                statusBox.style.color = "white"; // Change to match your UI's default text color
            }
        }, 3000);
    }
}