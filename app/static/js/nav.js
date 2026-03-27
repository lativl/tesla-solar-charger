// Fetch vehicle name and update the nav tab on all pages
(function() {
    const tab = document.getElementById('vehicle-tab');
    if (!tab) return;
    fetch('/api/tesla/status')
        .then(r => r.json())
        .then(data => {
            if (data.connected && data.vehicle && data.vehicle.name) {
                tab.textContent = data.vehicle.name;
            }
        })
        .catch(() => {});
})();

// Fetch app version and display under the title
(function() {
    const el = document.getElementById('app-version');
    if (!el) return;
    fetch('/api/version')
        .then(r => r.json())
        .then(data => { el.textContent = 'v' + data.version; })
        .catch(() => {});
})();
