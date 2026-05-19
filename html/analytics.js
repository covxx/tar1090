"use strict";

let analyticsPeriod = "day";
let analyticsCategory = "highest_alt";
let analyticsInitialized = false;
let analyticsPathLayer = null;
let analyticsHistoryLayer = null;

function getAnalyticsSearchParams() {
    return new URLSearchParams(window.location.search);
}

function syncAnalyticsUrlState(options) {
    const params = getAnalyticsSearchParams();
    const open = options && options.open;

    if (open) {
        params.set("analytics", "1");
        params.set("aperiod", analyticsPeriod);
        params.set("acategory", analyticsCategory);
        const histInput = document.getElementById("hist_icao");
        if (histInput && histInput.value && histInput.value.trim().length === 6) {
            params.set("aicao", histInput.value.trim().toLowerCase());
        } else {
            params.delete("aicao");
        }
    } else if (options && options.open === false) {
        params.delete("analytics");
        params.delete("aperiod");
        params.delete("acategory");
        params.delete("aicao");
    }

    const query = params.toString();
    const next = window.location.pathname + (query ? "?" + query : "");
    window.history.replaceState({}, "", next);
}

function applyAnalyticsUrlState(root) {
    const params = getAnalyticsSearchParams();
    const period = params.get("aperiod");
    if (period && ["day", "week", "month"].indexOf(period) >= 0) {
        analyticsPeriod = period;
    }
    const category = params.get("acategory");
    if (category && ["highest_alt", "fastest_gs", "largest", "smallest"].indexOf(category) >= 0) {
        analyticsCategory = category;
    }

    const periodBtn = root.querySelector("[data-period='" + analyticsPeriod + "']");
    if (periodBtn) {
        root.querySelectorAll("[data-period]").forEach((b) => b.classList.remove("active"));
        periodBtn.classList.add("active");
    }
    const categoryBtn = root.querySelector("[data-category='" + analyticsCategory + "']");
    if (categoryBtn) {
        root.querySelectorAll("[data-category]").forEach((b) => b.classList.remove("active"));
        categoryBtn.classList.add("active");
    }

    const hist = params.get("aicao");
    if (hist && hist.length === 6) {
        const input = document.getElementById("hist_icao");
        if (input) {
            input.value = hist.toLowerCase();
        }
    }
}

function analyticsApiBase() {
    if (typeof getAnalyticsApiBase === "function") {
        return getAnalyticsApiBase();
    }
    if (typeof analyticsApiUrl !== "undefined" && analyticsApiUrl) {
        return analyticsApiUrl.replace(/\/$/, "");
    }
    return window.location.protocol + "//" + window.location.hostname + ":" + (typeof analyticsPort !== "undefined" ? analyticsPort : 9056);
}

async function analyticsApiGet(path) {
    const base = analyticsApiBase();
    if (!base) {
        throw new Error("Analytics API not configured");
    }
    const r = await fetch(base.replace(/\/$/, "") + path);
    if (!r.ok) {
        throw new Error(await r.text());
    }
    return r.json();
}

function setAnalyticsStatus(msg) {
    const el = document.getElementById("analytics_status");
    if (el) {
        el.textContent = msg;
    }
}

function formatAnalyticsAlt(ft) {
    if (ft == null) {
        return "n/a";
    }
    return Math.round(ft).toLocaleString() + " ft";
}

function analyticsPhotoUrl(icao) {
    return analyticsApiBase().replace(/\/$/, "") + "/photo/" + icao.toLowerCase();
}

function selectPlaneFromAnalytics(icao) {
    const hex = (icao || "").toLowerCase();
    if (hex.length !== 6 || typeof selectPlaneByHex !== "function") {
        return;
    }
    selectPlaneByHex(hex, { follow: true });
}

function ensureAnalyticsPathLayer() {
    if (analyticsPathLayer || typeof OLMap === "undefined" || !OLMap) {
        return;
    }
    analyticsPathLayer = new ol.layer.Vector({
        source: new ol.source.Vector(),
        zIndex: 175,
        properties: { name: "analyticsPaths" },
        style: function (feature) {
            const cnt = feature.get("cnt") || 1;
            const radius = Math.min(14, 4 + Math.log(cnt));
            return new ol.style.Style({
                image: new ol.style.Circle({
                    radius: radius,
                    fill: new ol.style.Fill({ color: "rgba(61, 154, 232, 0.45)" }),
                    stroke: new ol.style.Stroke({ color: "#3d9ae8", width: 1 }),
                }),
            });
        },
    });
    OLMap.addLayer(analyticsPathLayer);
}

function ensureAnalyticsHistoryLayer() {
    if (analyticsHistoryLayer || typeof OLMap === "undefined" || !OLMap) {
        return;
    }
    analyticsHistoryLayer = new ol.layer.Vector({
        source: new ol.source.Vector(),
        zIndex: 176,
        properties: { name: "analyticsHistory" },
        style: new ol.style.Style({
            stroke: new ol.style.Stroke({ color: "#3d9ae8", width: 3 }),
        }),
    });
    OLMap.addLayer(analyticsHistoryLayer);
}

function clearAnalyticsMapLayers() {
    if (analyticsPathLayer) {
        analyticsPathLayer.getSource().clear();
    }
    if (analyticsHistoryLayer) {
        analyticsHistoryLayer.getSource().clear();
    }
}

function fitAnalyticsExtent(source) {
    if (!source || !OLMap) {
        return;
    }
    const extent = source.getExtent();
    if (extent && isFinite(extent[0])) {
        OLMap.getView().fit(extent, { padding: [50, 50, 50, 50], maxZoom: 11, duration: 400 });
    }
}

async function loadAnalyticsOverview() {
    try {
        const d = await analyticsApiGet("/stats/overview?period=" + analyticsPeriod);
        const seen = document.getElementById("stat_seen");
        const mil = document.getElementById("stat_military");
        const high = document.getElementById("stat_highest");
        if (seen) {
            seen.textContent = d.aircraft_seen ?? "—";
        }
        if (mil) {
            mil.textContent = d.military_aircraft ?? "—";
        }
        if (high) {
            high.textContent = formatAnalyticsAlt(d.highest_alt);
        }
        setAnalyticsStatus("Updated " + new Date().toLocaleTimeString());
    } catch (e) {
        setAnalyticsStatus("API unavailable: " + e.message);
    }
}

async function loadAnalyticsLeaderboard(category) {
    const tbody = document.getElementById("leaderboard_body");
    if (!tbody) {
        return;
    }
    tbody.innerHTML = "<tr><td colspan='6'>Loading…</td></tr>";
    try {
        const d = await analyticsApiGet("/stats/leaderboard?category=" + category + "&period=" + analyticsPeriod + "&limit=25");
        tbody.innerHTML = "";
        (d.items || []).forEach((row, i) => {
            const val = category === "highest_alt" ? formatAnalyticsAlt(row.value)
                : category === "fastest_gs" ? Math.round(row.value) + " kt"
                : category === "largest" || category === "smallest" ? row.value.toFixed(1) + " m"
                : row.value;
            const tr = document.createElement("tr");
            tr.innerHTML =
                "<td>" + (i + 1) + "</td>" +
                "<td><a class='icao-link' href='#' data-icao='" + row.icao.toLowerCase() + "'>" + row.icao.toUpperCase() + "</a></td>" +
                "<td>" + (row.callsign || "—") + "</td>" +
                "<td>" + (row.icao_type || "—") + "</td>" +
                "<td>" + val + "</td>" +
                "<td><img class='thumb' src='" + analyticsPhotoUrl(row.icao) + "' onerror='this.style.display=\"none\"' alt=''/></td>";
            tbody.appendChild(tr);
        });
        if (!d.items || d.items.length === 0) {
            tbody.innerHTML = "<tr><td colspan='6'>No data yet — ensure ingest is running.</td></tr>";
        }
    } catch (e) {
        tbody.innerHTML = "<tr><td colspan='6'>Error: " + e.message + "</td></tr>";
    }
}

async function loadAnalyticsMilitary() {
    const tbody = document.getElementById("military_body");
    if (!tbody) {
        return;
    }
    tbody.innerHTML = "<tr><td colspan='6'>Loading…</td></tr>";
    try {
        const d = await analyticsApiGet("/stats/military?limit=50");
        tbody.innerHTML = "";
        (d.items || []).forEach((row) => {
            const t = row.time ? new Date(row.time).toLocaleString() : "—";
            const tr = document.createElement("tr");
            tr.innerHTML =
                "<td>" + t + "</td>" +
                "<td><a class='icao-link' href='#' data-icao='" + row.icao.toLowerCase() + "'>" + row.icao.toUpperCase() + "</a></td>" +
                "<td>" + (row.callsign || "—") + "</td>" +
                "<td>" + (row.icao_type || "—") + "</td>" +
                "<td>" + formatAnalyticsAlt(row.alt_baro) + "</td>" +
                "<td><img class='thumb' src='" + analyticsPhotoUrl(row.icao) + "' onerror='this.style.display=\"none\"' alt=''/></td>";
            tbody.appendChild(tr);
        });
    } catch (e) {
        tbody.innerHTML = "<tr><td colspan='6'>Error: " + e.message + "</td></tr>";
    }
}

async function loadAnalyticsPaths() {
    ensureAnalyticsPathLayer();
    if (!analyticsPathLayer) {
        return;
    }
    const source = analyticsPathLayer.getSource();
    source.clear();
    try {
        const geo = await analyticsApiGet("/stats/paths/top?period=" + analyticsPeriod + "&limit=80");
        (geo.features || []).forEach((f) => {
            const [lon, lat] = f.geometry.coordinates;
            source.addFeature(new ol.Feature({
                geometry: new ol.geom.Point(ol.proj.fromLonLat([lon, lat])),
                cnt: f.properties.crossing_count || 1,
            }));
        });
        fitAnalyticsExtent(source);
    } catch (e) {
        setAnalyticsStatus("Paths: " + e.message);
    }
}

async function loadAnalyticsHistory() {
    const input = document.getElementById("hist_icao");
    const chartEl = document.getElementById("alt_chart");
    if (!input) {
        return;
    }
    const icao = input.value.trim().toLowerCase();
    if (icao.length !== 6) {
        setAnalyticsStatus("Enter a 6-character ICAO hex");
        return;
    }
    ensureAnalyticsHistoryLayer();
    if (!analyticsHistoryLayer) {
        return;
    }
    const source = analyticsHistoryLayer.getSource();
    source.clear();
    try {
        const d = await analyticsApiGet("/history/" + icao + "?period=" + analyticsPeriod);
        const pts = d.points || [];
        const coords = [];
        const alts = [];
        pts.forEach((p) => {
            if (p.lat != null && p.lon != null) {
                coords.push(ol.proj.fromLonLat([p.lon, p.lat]));
            }
            if (p.alt_baro != null) {
                alts.push(p.alt_baro);
            }
        });
        if (coords.length) {
            source.addFeature(new ol.Feature({
                geometry: new ol.geom.LineString(coords),
            }));
            fitAnalyticsExtent(source);
        }
        if (chartEl) {
            chartEl.textContent = alts.length
                ? "Max alt: " + formatAnalyticsAlt(Math.max(...alts)) + " · Points: " + pts.length
                : "No history for this aircraft in the selected period.";
        }
        setAnalyticsStatus("History loaded for " + icao.toUpperCase());
        selectPlaneFromAnalytics(icao);
    } catch (e) {
        if (chartEl) {
            chartEl.textContent = "Error: " + e.message;
        }
    }
}

function setupAnalyticsTabs() {
    const root = document.getElementById("tab-analytics");
    if (!root) {
        return;
    }
    root.querySelectorAll("[data-period]").forEach((btn) => {
        btn.addEventListener("click", () => {
            root.querySelectorAll("[data-period]").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            analyticsPeriod = btn.dataset.period;
            syncAnalyticsUrlState({ open: true });
            refreshAnalyticsDashboard();
        });
    });
    root.querySelectorAll("[data-category]").forEach((btn) => {
        btn.addEventListener("click", () => {
            root.querySelectorAll("[data-category]").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            analyticsCategory = btn.dataset.category;
            syncAnalyticsUrlState({ open: true });
            loadAnalyticsLeaderboard(btn.dataset.category);
        });
    });
    jQuery(root).on("click", "a.icao-link", function (e) {
        e.preventDefault();
        selectPlaneFromAnalytics(jQuery(this).data("icao") || jQuery(this).text());
    });
    const histBtn = document.getElementById("hist_load");
    if (histBtn) {
        histBtn.addEventListener("click", function () {
            syncAnalyticsUrlState({ open: true });
            loadAnalyticsHistory();
        });
    }
}

function refreshAnalyticsDashboard() {
    loadAnalyticsOverview();
    loadAnalyticsLeaderboard(analyticsCategory);
    loadAnalyticsMilitary();
    loadAnalyticsPaths();
}

function initAnalyticsUI() {
    if (analyticsInitialized || !document.getElementById("tab-analytics") || !analyticsApiBase()) {
        return;
    }
    const root = document.getElementById("tab-analytics");
    applyAnalyticsUrlState(root);
    analyticsInitialized = true;
    setupAnalyticsTabs();
    refreshAnalyticsDashboard();
    setInterval(refreshAnalyticsDashboard, 60000);
}

function openAnalyticsTab() {
    if (!analyticsApiBase()) {
        return;
    }
    const $link = jQuery('#tabs a[href="#tab-analytics"]');
    if (!$link.length) {
        return;
    }
    if (toggles && toggles.sidebar_visible && !toggles.sidebar_visible.state) {
        toggles.sidebar_visible.setState(true);
    }
    jQuery('#tabs').tabs('option', 'active', $link.parent().index());
    initAnalyticsUI();
    syncAnalyticsUrlState({ open: true });
    buttonActive('#A', true);
}

function toggleAnalytics() {
    if (!analyticsApiBase()) {
        return;
    }
    const $link = jQuery('#tabs a[href="#tab-analytics"]');
    if (!$link.length) {
        return;
    }
    const idx = $link.parent().index();
    const current = jQuery('#tabs').tabs('option', 'active');
    if (current === idx) {
        jQuery('#tabs').tabs('option', 'active', 0);
        buttonActive('#A', false);
        clearAnalyticsMapLayers();
        syncAnalyticsUrlState({ open: false });
        return;
    }
    openAnalyticsTab();
}

if (typeof initAnalyticsPoll === "function") {
    initAnalyticsPoll();
}
