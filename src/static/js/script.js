let statusInterval;

// Function to determine the base URL
function getBaseURL() {
    return window.location.protocol + "//" + window.location.host;
}

// Insert the correct URLs dynamically into the documentation
function insertURLs() {
    const baseURL = getBaseURL();

    document.getElementById('start-url').innerText = baseURL + '/start';
    document.getElementById('cancel-url').innerText = baseURL + '/cancel';
    document.getElementById('status-url').innerText = baseURL + '/status';

    document.getElementById('start-example').innerText = `
curl -X POST ${baseURL}/start \\
-H "Content-Type: application/json" \\
-d '{
    "identifier": "door1",
    "past_minutes": 10,
    "future_minutes": 15,
    "cameras": ["662e82ab009dc003e400044c", "662e82ab0152c003e400044f"]
}'`;

    document.getElementById('cancel-example').innerText = `
curl -X POST ${baseURL}/cancel \\
-H "Content-Type: application/json" \\
-d '{
    "identifier": "door1"
}'`;

    document.getElementById('status-example').innerText = `
curl -X GET ${baseURL}/status?identifier=door1`;
}

document.addEventListener('DOMContentLoaded', insertURLs);

// Fetch and update the list of running events
async function fetchRunningEvents() {
    try {
        let response = await fetch('/status');
        let data = await response.json();

        const eventsList = document.getElementById('events-list');
        eventsList.innerHTML = ''; // Clear the table

        if (Object.keys(data.events).length === 0) {
            eventsList.innerHTML = '<tr><td colspan="6">No running events</td></tr>';
        } else {
            Object.keys(data.events).forEach((identifier) => {
                const event = data.events[identifier];
                const remainingMinutes = Math.floor(event.remaining_time_seconds / 60);
                const remainingSeconds = Math.floor(event.remaining_time_seconds % 60);
                const cameras = (!event.cameras || event.cameras.length === 0 || event.cameras.every(camera => !camera.trim())) 
                    ? "All Cameras" 
                    : event.cameras.join(', ');

                const row = document.createElement('tr');
                row.innerHTML = `
                    <td>${identifier}</td>
                    <td>${event.start_time}</td>
                    <td>${event.end_time}</td>
                    <td>${remainingMinutes} min ${remainingSeconds} sec</td>
                    <td>${cameras}</td>
                    <td>
                        <button onclick="cancelEvent('${identifier}')">Cancel</button>
                        <button onclick="extendEvent('${identifier}')">Extend</button>
                    </td>
                `;
                eventsList.appendChild(row);
            });
        }

        // Refresh every second
        if (!statusInterval) {
            statusInterval = setInterval(fetchRunningEvents, 1000);
        }
    } catch (error) {
        console.error('Error fetching running events:', error);
    }
}

// Extend an event using the "Future Minutes" from the input field
async function extendEvent(identifier) {
    const futureMinutes = document.getElementById('future_minutes').value || 5; // Use provided or default 5 minutes

    try {
        let response = await fetch('/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                identifier: identifier,
                past_minutes: 0,  // No need to change past minutes
                future_minutes: parseInt(futureMinutes)  // Use the value from the input field
            })
        });
        let data = await response.json();
        alert(data.message || data.error);
        fetchRunningEvents();  // Refresh running events
    } catch (error) {
        console.error('Error extending event:', error);
        alert('An error occurred while extending the event.');
    }
}

// Cancel an event
async function cancelEvent(identifier) {
    try {
        let response = await fetch('/cancel', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ identifier: identifier })
        });
        let data = await response.json();
        alert(data.status || data.error);
        fetchRunningEvents();  // Refresh running events
    } catch (error) {
        console.error('Error canceling event:', error);
        alert('An error occurred while canceling the event.');
    }
}

// Start a new event
async function startEvent() {
    const identifier = document.getElementById('identifier').value;
    const pastMinutes = document.getElementById('past_minutes').value;
    const futureMinutes = document.getElementById('future_minutes').value;
    const cameraIds = document.getElementById('camera_ids').value.split(',').map(id => id.trim());

    if (!identifier) {
        alert("Please provide an event identifier.");
        return;
    }

    try {
        let response = await fetch('/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                identifier: identifier,
                past_minutes: parseInt(pastMinutes),
                future_minutes: parseInt(futureMinutes),
                cameras: cameraIds.length > 0 ? cameraIds : null
            })
        });
        let data = await response.json();
        alert(data.message || data.error);
        fetchRunningEvents();  // Refresh running events
    } catch (error) {
        console.error('Error:', error);
        alert('An error occurred while starting/extending the event.');
    }
}

// Initial fetch of running events
document.addEventListener('DOMContentLoaded', fetchRunningEvents);
