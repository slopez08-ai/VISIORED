/* VISIORED — conexión del panel con vigia_server.py */
(() => {
  "use strict";

  const API = "";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  let ultimoEstado = null;
  let firmaAlertas = "";
  let conectado = false;

  const PRIORIDAD = {
    Alta: "#ff737e",
    Media: "#f2bd68",
    Baja: "#71d9ac"
  };

  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[ch]));
  }

  async function pedirEstado() {
    const response = await fetch(`${API}/api/estado`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Servidor respondió ${response.status}`);
    return response.json();
  }

  function ponerTexto(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function renderResumen(data) {
    const r = data.resumen || {};
    ponerTexto("kpi-personas", r.personas ?? 0);
    ponerTexto("kpi-detecciones", r.detecciones ?? 0);
    ponerTexto("kpi-alertas", r.alertas_pendientes ?? 0);
    ponerTexto("kpi-zonas", r.zonas_vigiladas ?? 0);
    ponerTexto("kpi-riesgo", `${r.nivel_riesgo ?? 0}%`);
    ponerTexto("kpi-fps", r.fps_video ? `${r.fps_video} fps` : "—");
    ponerTexto("video-frames", Number(r.fotogramas ?? 0).toLocaleString("es-CO"));
    ponerTexto("camera-people", r.personas ?? 0);

    const cam = data.camara || {};
    const dot = $("#camera-status-dot");
    const label = $("#camera-status-label");
    if (dot) dot.classList.toggle("offline", !cam.ok);
    if (label) label.textContent = cam.ok ? "VIDEO ACTIVO" : "VIDEO SIN CONEXIÓN";
    ponerTexto("camera-message", cam.mensaje || "Esperando información del servidor.");
    ponerTexto("last-update", data.actualizado ? new Date(data.actualizado).toLocaleTimeString("es-CO") : "—");

    const banner = $("#source-banner");
    if (banner) {
      banner.classList.toggle("offline", !cam.ok);
      banner.innerHTML = cam.ok
        ? '<span class="pulse"></span><strong>FUENTE ACTIVA · VIDEO DE PRUEBA</strong><span>Detecciones procesadas por YOLOv8</span>'
        : '<span class="status-dot offline"></span><strong>VIDEO NO DISPONIBLE</strong><span>Revisa video_demo.mp4 y la consola del servidor</span>';
    }
  }

  function renderAlerts(list) {
    const signature = list.map(a => `${a.id}:${a.estado}:${a.fuente}:${a.hace}`).join("|");
    if (signature === firmaAlertas) return;
    firmaAlertas = signature;

    const html = list.length ? list.map(a => {
      const isDemo = a.fuente === "simulado";
      const sourceText = isDemo ? "EVENTO SIMULADO" : "DETECCIÓN · VIDEO DE PRUEBA";
      const sourceClass = isDemo ? "source-demo" : "source-video";
      const stateClass = a.estado === "atendida" ? "resolved" : "";
      return `
        <article class="alert-row ${stateClass}">
          <div class="priority-mark" style="--priority:${PRIORIDAD[a.prioridad] || PRIORIDAD.Media}"></div>
          <div class="alert-copy">
            <div class="alert-title-line">
              <strong>${escapeHTML(a.titulo)}</strong>
              <span class="priority-label">${escapeHTML(a.prioridad || "Media")}</span>
            </div>
            <span class="alert-location">${escapeHTML(a.ubicacion)}</span>
            <div class="alert-tags">
              <span class="source-tag ${sourceClass}">${sourceText}</span>
              <span class="source-tag source-neutral">${escapeHTML(a.camara || "CAM-014")}</span>
            </div>
          </div>
          <div class="alert-side">
            <span class="alert-time">${escapeHTML(a.hace || "ahora")}</span>
            <button class="small-button ${a.estado === "atendida" ? "is-done" : ""}" data-alert-id="${a.id}">
              ${a.estado === "atendida" ? "Reabrir" : "Atender"}
            </button>
          </div>
        </article>`;
    }).join("") : `<div class="empty-state"><span>✓</span><strong>Sin alertas</strong><p>El sistema no tiene alertas registradas por ahora.</p></div>`;

    $$(".alerts-list").forEach(el => { el.innerHTML = html; });
  }

  function renderZones(zones) {
    const host = $("#zones-list");
    if (!host) return;
    host.innerHTML = (zones || []).map(z => `
      <div class="zone-row">
        <div class="zone-name"><span class="zone-dot" style="--zone:${z.riesgo >= 75 ? "#f07880" : z.riesgo >= 60 ? "#efbb66" : "#72d5a6"}"></span>
          <span>${escapeHTML(z.nombre)}</span><small>${escapeHTML(z.id)}</small></div>
        <div class="zone-meter"><span style="width:${Math.max(0, Math.min(100, z.riesgo))}%;--zone:${z.riesgo >= 75 ? "#f07880" : z.riesgo >= 60 ? "#efbb66" : "#72d5a6"}"></span></div>
        <strong>${Number(z.riesgo)}%</strong>
      </div>`).join("");
  }

  function renderPatrols(patrols) {
    const host = $("#patrol-list");
    if (!host) return;
    host.innerHTML = (patrols || []).map(p => `
      <div class="patrol-row">
        <span class="patrol-icon">✳</span>
        <div class="patrol-copy"><strong>${escapeHTML(p.nombre)}</strong><span>${escapeHTML(p.id)} · ${escapeHTML(p.zona)}</span></div>
        <span class="patrol-state ${p.estado === "Disponible" ? "available" : p.estado === "En atención" ? "busy" : "route"}">${escapeHTML(p.estado)}</span>
      </div>`).join("");
  }

  function renderAnalytics(analytics) {
    const host = $("#weekly-chart");
    if (host && analytics) {
      const values = analytics.incidentes || [];
      const labels = analytics.dias || [];
      const max = Math.max(...values, 1);
      host.innerHTML = values.map((v, i) => `
        <div class="chart-column">
          <span class="chart-value">${v}</span>
          <div class="chart-bar-wrap"><span class="chart-bar" style="height:${Math.max(5, v / max * 100)}%"></span></div>
          <small>${escapeHTML(labels[i] || "")}</small>
        </div>`).join("");
    }
    const typeHost = $("#type-chart");
    if (typeHost && analytics) {
      typeHost.innerHTML = (analytics.tipos || []).map(item => `
        <div class="type-row"><div><span>${escapeHTML(item.nombre)}</span><strong>${item.valor}%</strong></div>
          <div class="type-track"><span style="width:${Math.max(0, Math.min(100, item.valor))}%"></span></div></div>`).join("");
    }
    ponerTexto("analytics-label", analytics?.etiqueta || "DATOS DE DEMOSTRACIÓN");
  }

  async function atenderAlerta(id) {
    try {
      const response = await fetch(`${API}/api/alertas/${encodeURIComponent(id)}/atender`, {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "No se pudo actualizar la alerta.");
      await actualizar();
    } catch (error) {
      mostrarToast(`No se pudo actualizar: ${error.message}`, true);
    }
  }

  function mostrarToast(message, error = false) {
    const toast = $("#toast");
    if (!toast) return;
    toast.textContent = message;
    toast.classList.toggle("error", error);
    toast.classList.add("visible");
    clearTimeout(mostrarToast.timer);
    mostrarToast.timer = setTimeout(() => toast.classList.remove("visible"), 3200);
  }

  async function actualizar() {
    try {
      const data = await pedirEstado();
      ultimoEstado = data;
      conectado = true;
      $("#connection-pill")?.classList.remove("offline");
      ponerTexto("connection-text", "SERVIDOR CONECTADO");
      renderResumen(data);
      renderAlerts(data.alertas || []);
      renderZones(data.zonas || []);
      renderPatrols(data.patrullas || []);
      renderAnalytics(data.analitica || {});
    } catch (error) {
      conectado = false;
      $("#connection-pill")?.classList.add("offline");
      ponerTexto("connection-text", "SERVIDOR DESCONECTADO");
      const dot = $("#camera-status-dot");
      if (dot) dot.classList.add("offline");
      ponerTexto("camera-status-label", "SIN CONEXIÓN");
      ponerTexto("camera-message", "Ejecuta python vigia_server.py y abre el panel desde http://127.0.0.1:5000.");
    }
  }

  document.addEventListener("click", event => {
    const button = event.target.closest("[data-alert-id]");
    if (button) atenderAlerta(button.dataset.alertId);
    const reset = event.target.closest("#reset-counters");
    if (reset) {
      fetch(`${API}/api/reset`, { method: "POST" })
        .then(r => r.json())
        .then(data => { mostrarToast(data.mensaje || "Contadores reiniciados."); actualizar(); })
        .catch(() => mostrarToast("No se pudo reiniciar el contador.", true));
    }
    const nav = event.target.closest("[data-view]");
    if (nav) {
      const view = nav.dataset.view;
      $$("[data-view]").forEach(el => el.classList.toggle("active", el.dataset.view === view));
      $$(".page-view").forEach(el => el.classList.toggle("active", el.id === `view-${view}`));
      ponerTexto("page-title", nav.dataset.title || nav.textContent.trim());
    }
  });

  window.addEventListener("DOMContentLoaded", () => {
    const video = $("#live-video");
    const heat = $("#heatmap-video");
    if (video) video.src = `/video_feed?t=${Date.now()}`;
    if (heat) heat.src = `/heatmap_feed?t=${Date.now()}`;
    actualizar();
    setInterval(actualizar, 2000);
  });
})();
