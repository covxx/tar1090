"use strict";

let analyticsPeriod = "day";
let analyticsCategory = "highest_alt";
let analyticsSubview = "overview";
let analyticsPatternType = "";
let analyticsInitialized = false;
let analyticsPollTimer = null;
let analyticsPathLayer = null;
let analyticsHistoryLayer = null;
let analyticsPatternLayer = null;
let analyticsCharts = {};

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
    if (analyticsPatternLayer) {
        analyticsPatternLayer.getSource().clear();
    }
}

function isAnalyticsTabActive() {
    const panel = document.getElementById("tab-analytics");
    return panel && panel.classList.contains("ui-tabs-panel") && !panel.classList.contains("ui-tabs-hide");
}

function setAnalyticsSubview(view) {
    analyticsSubview = view;
    document.querySelectorAll("#tab-analytics .analytics-subtabs [data-aview]").forEach((b) => {
        b.classList.toggle("active", b.dataset.aview === view);
    });
    document.querySelectorAll("#tab-analytics .analytics-view").forEach((el) => {
        el.classList.toggle("active", el.id === "aview-" + view);
    });
    refreshAnalyticsDashboard();
}

function destroyChart(id) {
    if (analyticsCharts[id]) {
        analyticsCharts[id].destroy();
        delete analyticsCharts[id];
    }
}

function makeChart(id, type, labels, data, label) {
    destroyChart(id);
    const el = document.getElementById(id);
    if (!el || typeof Chart === "undefined") {
        return;
    }
    const opts = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: type === "doughnut" } },
    };
    if (type !== "doughnut") {
        opts.scales = {
            x: { ticks: { color: "#8b9cb3", maxTicksLimit: 12 } },
            y: { ticks: { color: "#8b9cb3" }, beginAtZero: true },
        };
    }
    analyticsCharts[id] = new Chart(el, {
        type: type,
        data: {
            labels: labels,
            datasets: [{
                label: label,
                data: data,
                borderColor: "#3d9ae8",
                backgroundColor: type === "doughnut"
                    ? ["#3d9ae8", "#e8a838", "#6bcb77", "#ff6b6b", "#9b59b6", "#95a5a6"]
                    : "rgba(61, 154, 232, 0.35)",
                fill: type === "line",
            }],
        },
        options: opts,
    });
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
        const d = await analyticsApiGet("/stats/military?period=" + analyticsPeriod + "&limit=50");
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
        const geo = await analyticsApiGet("/stats/paths/heatmap?period=" + analyticsPeriod + "&limit=200");
        (geo.features || []).forEach((f) => {
            const [lon, lat] = f.geometry.coordinates;
            source.addFeature(new ol.Feature({
                geometry: new ol.geom.Point(ol.proj.fromLonLat([lon, lat])),
                cnt: f.properties.crossing_count || 1,
            }));
        });
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
        }
        const canvas = document.getElementById("hist_alt_canvas");
        if (canvas && alts.length && typeof Chart !== "undefined") {
            const labels = pts.map((p, i) => (i % Math.max(1, Math.floor(pts.length / 8)) === 0 ? i : ""));
            makeChart("hist_alt_canvas", "line", labels, alts, "alt ft");
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

async function loadTrafficDashboard() {
    try {
        const trend = await analyticsApiGet("/stats/traffic/trends?granularity=hour&period=" + analyticsPeriod);
        const pts = trend.points || [];
        makeChart("chart_traffic_trend", "line", pts.map((p) => p.t.slice(5, 16)), pts.map((p) => p.distinct_icao), "aircraft");
        const peak = await analyticsApiGet("/stats/traffic/peak-hours?period=" + analyticsPeriod);
        const hours = peak.hours || [];
        makeChart("chart_peak_hours", "bar", hours.map((h) => h.hour + ":00"), hours.map((h) => h.count), "count");
        const alt = await analyticsApiGet("/stats/altitude/histogram?period=" + analyticsPeriod);
        const bins = alt.bins || [];
        makeChart("chart_altitude_hist", "bar", bins.map((b) => b.label), bins.map((b) => b.count), "count");
        const night = await analyticsApiGet("/stats/overnight");
        const tbody = document.getElementById("overnight_body");
        if (tbody) {
            tbody.innerHTML = "";
            (night.items || []).forEach((row) => {
                const tr = document.createElement("tr");
                tr.innerHTML = "<td><a class='icao-link' href='#' data-icao='" + row.icao + "'>" + row.icao.toUpperCase() + "</a></td>" +
                    "<td>" + (row.first_seen ? new Date(row.first_seen).toLocaleTimeString() : "—") + "</td>" +
                    "<td>" + (row.last_seen ? new Date(row.last_seen).toLocaleTimeString() : "—") + "</td>" +
                    "<td>" + (row.callsign || "—") + "</td>";
                tbody.appendChild(tr);
            });
        }
    } catch (e) {
        setAnalyticsStatus("Traffic: " + e.message);
    }
}

async function loadSpecialDashboard() {
    try {
        const roles = await analyticsApiGet("/stats/military/by-role?period=" + analyticsPeriod);
        const r = roles.roles || [];
        makeChart("chart_mil_role", "doughnut", r.map((x) => x.role), r.map((x) => x.count), "sightings");
        const priv = await analyticsApiGet("/stats/privacy?period=" + analyticsPeriod);
        const tbodyP = document.getElementById("privacy_body");
        if (tbodyP) {
            tbodyP.innerHTML = "";
            (priv.items || []).forEach((row) => {
                const tr = document.createElement("tr");
                tr.innerHTML = "<td>" + new Date(row.time).toLocaleString() + "</td><td><a class='icao-link' href='#' data-icao='" + row.icao + "'>" + row.icao.toUpperCase() + "</a></td><td>" + row.flag + "</td><td>" + (row.callsign || "—") + "</td>";
                tbodyP.appendChild(tr);
            });
        }
        const sq = await analyticsApiGet("/stats/alerts/squawk?period=" + analyticsPeriod);
        const tbodyS = document.getElementById("squawk_body");
        if (tbodyS) {
            tbodyS.innerHTML = "";
            (sq.items || []).forEach((row) => {
                const tr = document.createElement("tr");
                tr.innerHTML = "<td>" + row.squawk + "</td><td>" + new Date(row.started_at).toLocaleString() + "</td><td><a class='icao-link' href='#' data-icao='" + row.icao + "'>" + row.icao.toUpperCase() + "</a></td><td>" + (row.callsign || "—") + "</td>";
                tbodyS.appendChild(tr);
            });
        }
        const gov = await analyticsApiGet("/stats/government?period=" + analyticsPeriod);
        const tbodyG = document.getElementById("government_body");
        if (tbodyG) {
            tbodyG.innerHTML = "";
            (gov.items || []).forEach((row) => {
                const tr = document.createElement("tr");
                tr.innerHTML = "<td>" + new Date(row.time).toLocaleString() + "</td><td><a class='icao-link' href='#' data-icao='" + row.icao + "'>" + row.icao.toUpperCase() + "</a></td><td>" + (row.agency || row.country || "—") + "</td><td>" + (row.callsign || "—") + "</td>";
                tbodyG.appendChild(tr);
            });
        }
    } catch (e) {
        setAnalyticsStatus("Special: " + e.message);
    }
}

async function loadPatternsDashboard() {
    try {
        const sum = await analyticsApiGet("/stats/patterns/summary?period=" + analyticsPeriod);
        const cards = document.getElementById("pattern_summary_cards");
        if (cards) {
            cards.innerHTML = "";
            Object.keys(sum.summary || {}).forEach((k) => {
                const d = document.createElement("div");
                d.className = "card";
                d.innerHTML = "<div class='label'>" + k + "</div><div class='value'>" + sum.summary[k] + "</div>";
                cards.appendChild(d);
            });
        }
        const q = analyticsPatternType ? "&type=" + analyticsPatternType : "";
        const d = await analyticsApiGet("/stats/patterns?period=" + analyticsPeriod + q + "&limit=50");
        const tbody = document.getElementById("patterns_body");
        if (tbody) {
            tbody.innerHTML = "";
            (d.items || []).forEach((row) => {
                const tr = document.createElement("tr");
                tr.innerHTML = "<td>" + new Date(row.started_at).toLocaleString() + "</td><td>" + row.pattern_type + "</td><td><a class='icao-link' href='#' data-icao='" + row.icao + "'>" + row.icao.toUpperCase() + "</a></td><td>" + Math.round((row.confidence || 0) * 100) + "%</td><td><button type='button' class='analytics-btn pattern-map-btn' data-id='" + row.id + "'>Map</button></td>";
                tbody.appendChild(tr);
            });
        }
        const rep = await analyticsApiGet("/stats/patterns/repeat-visits?period=" + analyticsPeriod);
        const tbodyR = document.getElementById("repeat_body");
        if (tbodyR) {
            tbodyR.innerHTML = "";
            (rep.items || []).forEach((row) => {
                const tr = document.createElement("tr");
                tr.innerHTML = "<td><a class='icao-link' href='#' data-icao='" + row.icao + "'>" + row.icao.toUpperCase() + "</a></td><td>" + row.visit_count + "</td><td>" + row.dow + "</td><td>" + row.hour_bucket + "</td><td><button type='button' class='analytics-btn repeat-map-btn' data-icao='" + row.icao + "'>Map</button></td>";
                tbodyR.appendChild(tr);
            });
        }
    } catch (e) {
        setAnalyticsStatus("Patterns: " + e.message);
    }
}

function refreshAnalyticsDashboard() {
    if (!isAnalyticsTabActive()) {
        return;
    }
    if (analyticsSubview === "overview") {
        loadAnalyticsOverview();
        loadAnalyticsLeaderboard(analyticsCategory);
        loadAnalyticsMilitary();
        loadAnalyticsPaths();
    } else if (analyticsSubview === "traffic") {
        loadTrafficDashboard();
    } else if (analyticsSubview === "special") {
        loadSpecialDashboard();
    } else if (analyticsSubview === "patterns") {
        loadPatternsDashboard();
    }
}

function ensureAnalyticsPatternLayer() {
    if (analyticsPatternLayer || typeof OLMap === "undefined" || !OLMap) {
        return;
    }
    analyticsPatternLayer = new ol.layer.Vector({
        source: new ol.source.Vector(),
        zIndex: 177,
        properties: { name: "analyticsPattern" },
        style: new ol.style.Style({
            stroke: new ol.style.Stroke({ color: "#e8a838", width: 3 }),
        }),
    });
    OLMap.addLayer(analyticsPatternLayer);
}

async function loadPatternOnMap(eventId) {
    ensureAnalyticsPatternLayer();
    if (!analyticsPatternLayer) {
        return;
    }
    const source = analyticsPatternLayer.getSource();
    source.clear();
    try {
        const d = await analyticsApiGet("/stats/patterns/" + eventId);
        const coords = (d.track && d.track.geometry && d.track.geometry.coordinates) || [];
        if (coords.length) {
            source.addFeature(new ol.Feature({
                geometry: new ol.geom.LineString(coords.map((c) => ol.proj.fromLonLat([c[0], c[1]]))),
            }));
        }
        selectPlaneFromAnalytics(d.icao);
    } catch (e) {
        setAnalyticsStatus("Pattern: " + e.message);
    }
}

function initAnalyticsUI() {
    if (analyticsInitialized || !document.getElementById("tab-analytics") || !analyticsApiBase()) {
        return;
    }
    const root = document.getElementById("tab-analytics");
    applyAnalyticsUrlState(root);
    analyticsInitialized = true;
    setupAnalyticsTabs();
    root.querySelectorAll("[data-aview]").forEach((btn) => {
        btn.addEventListener("click", () => setAnalyticsSubview(btn.dataset.aview));
    });
    root.querySelectorAll("#pattern_type_tabs [data-ptype]").forEach((btn) => {
        btn.addEventListener("click", () => {
            root.querySelectorAll("#pattern_type_tabs [data-ptype]").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            analyticsPatternType = btn.dataset.ptype || "";
            loadPatternsDashboard();
        });
    });
    jQuery(root).on("click", ".pattern-map-btn", function () {
        loadPatternOnMap(jQuery(this).data("id"));
    });
    jQuery(root).on("click", ".repeat-map-btn", function () {
        const icao = jQuery(this).data("icao");
        document.getElementById("hist_icao").value = icao;
        setAnalyticsSubview("overview");
        loadAnalyticsHistory();
    });
    const heatLoad = document.getElementById("heatmap_load");
    if (heatLoad) {
        heatLoad.addEventListener("click", loadAnalyticsPaths);
    }
    const heatFit = document.getElementById("heatmap_fit");
    if (heatFit) {
        heatFit.addEventListener("click", () => {
            if (analyticsPathLayer) {
                fitAnalyticsExtent(analyticsPathLayer.getSource());
            }
        });
    }
    const histFit = document.getElementById("hist_fit_map");
    if (histFit) {
        histFit.addEventListener("click", () => {
            if (analyticsHistoryLayer) {
                fitAnalyticsExtent(analyticsHistoryLayer.getSource());
            }
        });
    }
    refreshAnalyticsDashboard();
    if (!analyticsPollTimer) {
        analyticsPollTimer = setInterval(refreshAnalyticsDashboard, 60000);
    }
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
