"use strict";

function getAnalyticsApiBase() {
    if (typeof analyticsApiBase !== "undefined" && analyticsApiBase) {
        return analyticsApiBase.replace(/\/$/, "");
    }
    return window.location.protocol + "//" + window.location.hostname + ":8080";
}
const API_BASE = getAnalyticsApiBase();

let period = "day";
let pathMap = null;
let historyMap = null;

async function apiGet(path) {
    const url = API_BASE.replace(/\/$/, "") + path;
    const r = await fetch(url);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
}

function setStatus(msg) {
    document.getElementById("status").textContent = msg;
}

function formatAlt(ft) {
    if (ft == null) return "n/a";
    return Math.round(ft).toLocaleString() + " ft";
}

function mapLink(icao) {
    return "index.html?icao=" + icao.toUpperCase();
}

function photoUrl(icao) {
    return API_BASE.replace(/\/$/, "") + "/photo/" + icao.toLowerCase();
}

async function loadOverview() {
    try {
        const d = await apiGet("/stats/overview?period=" + period);
        document.getElementById("stat_seen").textContent = d.aircraft_seen ?? "—";
        document.getElementById("stat_military").textContent = d.military_aircraft ?? "—";
        document.getElementById("stat_highest").textContent = formatAlt(d.highest_alt);
        setStatus("Updated " + new Date().toLocaleTimeString());
    } catch (e) {
        setStatus("API unavailable: " + e.message);
    }
}

async function loadLeaderboard(category) {
    const tbody = document.getElementById("leaderboard_body");
    tbody.innerHTML = "<tr><td colspan='5'>Loading…</td></tr>";
    try {
        const d = await apiGet("/stats/leaderboard?category=" + category + "&period=" + period + "&limit=25");
        tbody.innerHTML = "";
        (d.items || []).forEach((row, i) => {
            const tr = document.createElement("tr");
            const val = category === "highest_alt" ? formatAlt(row.value)
                : category === "fastest_gs" ? Math.round(row.value) + " kt"
                : category === "largest" || category === "smallest" ? row.value.toFixed(1) + " m"
                : row.value;
            tr.innerHTML =
                "<td>" + (i + 1) + "</td>" +
                "<td><a class='icao-link' href='" + mapLink(row.icao) + "'>" + row.icao.toUpperCase() + "</a></td>" +
                "<td>" + (row.callsign || "—") + "</td>" +
                "<td>" + (row.icao_type || "—") + "</td>" +
                "<td>" + val + "</td>" +
                "<td><img class='thumb' src='" + photoUrl(row.icao) + "' onerror='this.style.display=\"none\"' alt=''/></td>";
            tbody.appendChild(tr);
        });
        if (!d.items || d.items.length === 0) {
            tbody.innerHTML = "<tr><td colspan='6'>No data yet — ensure ingest is running.</td></tr>";
        }
    } catch (e) {
        tbody.innerHTML = "<tr><td colspan='6'>Error: " + e.message + "</td></tr>";
    }
}

async function loadMilitary() {
    const tbody = document.getElementById("military_body");
    tbody.innerHTML = "<tr><td colspan='6'>Loading…</td></tr>";
    try {
        const d = await apiGet("/stats/military?limit=50");
        tbody.innerHTML = "";
        (d.items || []).forEach((row) => {
            const tr = document.createElement("tr");
            const t = row.time ? new Date(row.time).toLocaleString() : "—";
            tr.innerHTML =
                "<td>" + t + "</td>" +
                "<td><a class='icao-link' href='" + mapLink(row.icao) + "'>" + row.icao.toUpperCase() + "</a></td>" +
                "<td>" + (row.callsign || "—") + "</td>" +
                "<td>" + (row.icao_type || "—") + "</td>" +
                "<td>" + formatAlt(row.alt_baro) + "</td>" +
                "<td><img class='thumb' src='" + photoUrl(row.icao) + "' onerror='this.style.display=\"none\"' alt=''/></td>";
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = "<tr><td colspan='6'>Error: " + e.message + "</td></tr>";
    }
}

function initPathMap() {
    pathMap = L.map("pathMap").setView([51.5, 10], 6);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "© OpenStreetMap",
    }).addTo(pathMap);
}

async function loadPaths() {
    if (!pathMap) initPathMap();
    try {
        const geo = await apiGet("/stats/paths/top?period=" + period + "&limit=80");
        pathMap.eachLayer((l) => { if (l instanceof L.CircleMarker) pathMap.removeLayer(l); });
        const features = geo.features || [];
        features.forEach((f) => {
            const [lon, lat] = f.geometry.coordinates;
            const cnt = f.properties.crossing_count || 1;
            const r = Math.min(12, 3 + Math.log(cnt));
            L.circleMarker([lat, lon], {
                radius: r,
                color: "#3d9ae8",
                fillColor: "#3d9ae8",
                fillOpacity: 0.5,
                weight: 1,
            }).addTo(pathMap).bindPopup("Crossings: " + cnt);
        });
        if (features.length) {
            const bounds = L.geoJSON(geo).getBounds();
            if (bounds.isValid()) pathMap.fitBounds(bounds, { padding: [20, 20] });
        }
    } catch (e) {
        setStatus("Paths: " + e.message);
    }
}

function initHistoryMap() {
    historyMap = L.map("historyMap").setView([51.5, 10], 8);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "© OpenStreetMap",
    }).addTo(historyMap);
}

async function loadHistory() {
    const icao = document.getElementById("hist_icao").value.trim().toLowerCase();
    if (icao.length !== 6) {
        setStatus("Enter a 6-character ICAO hex");
        return;
    }
    if (!historyMap) initHistoryMap();
    const chartEl = document.getElementById("alt_chart");
    try {
        const d = await apiGet("/history/" + icao + "?period=" + period);
        const pts = d.points || [];
        historyMap.eachLayer((l) => { if (l instanceof L.Polyline || l instanceof L.CircleMarker) historyMap.removeLayer(l); });
        const latlngs = [];
        const alts = [];
        pts.forEach((p) => {
            if (p.lat != null && p.lon != null) latlngs.push([p.lat, p.lon]);
            if (p.alt_baro != null) alts.push(p.alt_baro);
        });
        if (latlngs.length) {
            L.polyline(latlngs, { color: "#3d9ae8", weight: 3 }).addTo(historyMap);
            historyMap.fitBounds(latlngs);
        }
        chartEl.textContent = alts.length
            ? "Max alt: " + formatAlt(Math.max(...alts)) + " · Points: " + pts.length
            : "No history for this aircraft in the selected period.";
        setStatus("History loaded for " + icao.toUpperCase());
    } catch (e) {
        chartEl.textContent = "Error: " + e.message;
    }
}

function setupTabs() {
    document.querySelectorAll("[data-period]").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("[data-period]").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            period = btn.dataset.period;
            refreshAll();
        });
    });
    document.querySelectorAll("[data-category]").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("[data-category]").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            loadLeaderboard(btn.dataset.category);
        });
    });
}

function refreshAll() {
    loadOverview();
    const active = document.querySelector("[data-category].active");
    loadLeaderboard(active ? active.dataset.category : "highest_alt");
    loadMilitary();
    loadPaths();
}

document.getElementById("hist_load").addEventListener("click", loadHistory);

setupTabs();
refreshAll();
setInterval(refreshAll, 60000);
