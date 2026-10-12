/* VISIORED en vivo: conecta el panel con vigia_server.py
   Lo inyecta el servidor solo; no hay que editar el HTML. */
(() => {
  const API = (document.currentScript && new URL(document.currentScript.src).origin) || "http://127.0.0.1:5000";
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad = n => String(n).padStart(2, "0");
  const mmss = s => { const t = Math.round(s); return `${pad(Math.floor(t / 60))}:${pad(t % 60)}`; };
  const COLOR_PRIORIDAD = { "Crítica": "#ef6a73", "Alta": "#e5a446", "Media": "#d77a9a", "Baja": "#9d818a" };
  const COLORES_TIPO = ["#d85a7f", "#ac3659", "#7f2844", "#e59a49", "#603041"];

  let iniciado = false;
  let firmaAlertas = "";
  let ultimaSnap = 0;

  /* ---------- Configuración inicial (una sola vez) ---------- */
  function iniciar(est) {
    const c = est.camara;

    // La webcam real reemplaza a la cámara 0 del panel
    cameras[0].id = c.id;
    cameras[0].name = c.nombre;
    cameras[0].image = API + "/video_feed";
    $$('.camera-list [data-camera="0"]').forEach(b => {
      $("b", b).textContent = c.id;
      $("small", b).textContent = c.nombre;
    });
    selectCamera(0);

    // Mapa operativo: quitamos los puntos dibujados a mano; ahora los
    // dibuja el índice de anticipación de cada zona.
    $$(".map").forEach((mapa, i) => {
      $$(".heat", mapa).forEach(h => h.remove());
      est.zonas.forEach(z => {
        const blob = document.createElement("i");
        blob.className = "heat";
        blob.dataset.zona = z.zona;
        blob.style.left = z.pos[0] + "%";
        blob.style.top = z.pos[1] + "%";
        mapa.appendChild(blob);
      });
      // Recuadro con el mapa de calor real de la cámara
      const pip = document.createElement("div");
      pip.style.cssText = "position:absolute;z-index:4;top:12px;right:12px;width:176px;border:1px solid #542638;border-radius:4px;overflow:hidden;background:#12070b;box-shadow:0 6px 18px rgba(0,0,0,.4)";
      pip.innerHTML = `<img alt="Mapa de calor de la cámara" style="display:block;width:100%" src="${API}/heatmap_feed"><div style="padding:5px 7px;font-size:8px;color:#b99aa6">${esc(c.id)} · mapa de calor en vivo</div>`;
      mapa.appendChild(pip);
    });

    iniciado = true;
  }

  /* ---------- Pintado en cada actualización ---------- */
  function pintarResumen(est) {
    const r = est.resumen;
    const stat = $$(".stat");
    const set = (i, valor, sub) => {
      if (!stat[i]) return;
      $("strong", stat[i]).textContent = valor;
      if (sub !== undefined) $("small", stat[i]).textContent = sub;
    };
    set(0, r.alertas_activas, `+${r.alertas_ultima_hora} en la última hora`);
    set(1, r.camaras_operativas, `${r.disponibilidad}% de disponibilidad`);
    set(2, `${r.nivel_prevencion}%`);
    set(3, r.patrullas_servicio, `${r.patrullas_atendiendo} atendiendo eventos`);

    const badge = $(".notify i");
    if (badge) badge.textContent = r.alertas_activas;

    const sistema = $(".system small");
    if (sistema) sistema.textContent = est.camara.ok ? "Todos los servicios activos" : "Cámara web sin señal";
  }

  function pintarAlertas(lista) {
    const firma = lista.map(a => a.id + a.estado).join("|");
    if (firma !== firmaAlertas) {
      firmaAlertas = firma;
      const html = lista.length
        ? lista.map(a => `<article class="alert${a.estado === "atendida" ? " resolved" : ""}"><span class="alert-symbol" style="color:${COLOR_PRIORIDAD[a.prioridad] || COLOR_PRIORIDAD["Media"]}">!</span><div class="alert-main"><b>${esc(a.titulo)}</b><span>${esc(a.ubicacion)}</span></div><div class="alert-meta"><b>${esc(a.camara)}</b><span data-hace="${a.id}">${esc(a.hace)}</span></div><button data-id="${a.id}">${a.estado === "atendida" ? "Reabrir" : "Atender"}</button></article>`).join("")
        : `<article class="alert" style="grid-template-columns:1fr"><div class="alert-main"><b>Sin alertas por ahora</b><span>El sistema sigue monitoreando</span></div></article>`;
      $$(".alerts").forEach(el => (el.innerHTML = html));
    } else {
      lista.forEach(a => $$(`[data-hace="${a.id}"]`).forEach(el => (el.textContent = a.hace)));
    }
  }

  function pintarMapa(zonas) {
    zonas.forEach(z => {
      const lado = Math.round(60 + z.indice * 1.6);
      const opacidad = Math.min(1, 0.3 + z.indice / 100 * 0.7);
      $$(`.heat[data-zona="${z.zona}"]`).forEach(b => {
        b.style.width = b.style.height = lado + "px";
        b.style.opacity = opacidad;
        b.title = `${z.zona}: índice ${z.indice} (historial ${z.historial} · en vivo ${z.vivo} · entorno ${z.entorno})`;
      });
    });
  }

  function pintarGraficas(an) {
    // Tendencia de 7 días
    const v = an.tendencia, mn = Math.min(...v), mx = Math.max(...v);
    const puntos = v.map((x, i) => [Math.round(i * 340 / 6), Math.round(14 + (1 - (x - mn) / ((mx - mn) || 1)) * 86)]);
    const d = "M" + puntos.map(p => p.join(" ")).join(" L ");
    const linea = $(".chart-line"), area = $(".chart-area");
    if (linea) linea.setAttribute("d", d);
    if (area) area.setAttribute("d", d + " V120 H0Z");
    const cabezas = $$(".chart .panel-head small");
    if (cabezas[0]) {
      const baja = an.variacion_pct <= 0;
      cabezas[0].textContent = `${baja ? "−" : "+"}${Math.abs(an.variacion_pct).toFixed(1)}%`;
      cabezas[0].style.color = baja ? "var(--green)" : "var(--amber)";
    }

    // Alertas por hora
    const barras = $(".bars");
    if (barras) {
      const max = Math.max(...an.horas.map(h => h.valor));
      barras.innerHTML = an.horas.map(h => {
        const pico = h.hora === an.pico_hora;
        return `<i title="${pad(h.hora)}:00 · ${h.valor} alertas" style="height:${Math.round(h.valor / max * 95)}%${pico ? ";background:var(--amber)" : ""}"></i>`;
      }).join("");
    }
    if (cabezas[1]) cabezas[1].textContent = `Pico ${pad(an.pico_hora)}:00`;
  }

  function pintarAnalitica(est) {
    const an = est.analitica;
    const kpi = $$("#analitica > .report-kpis article b");
    if (kpi.length >= 4) {
      kpi[0].textContent = an.incidentes_total;
      kpi[1].textContent = mmss(an.tiempo_medio);
      kpi[2].textContent = an.resueltos_pct + "%";
      kpi[3].textContent = an.detecciones_ia.toLocaleString("en-US");
    }

    // Incidentes por tipo (dona + lista)
    const total = an.tipos.reduce((s, t) => s + t.valor, 0);
    let acc = 0;
    const tramos = an.tipos.map((t, i) => {
      const desde = acc;
      acc += t.valor / total * 100;
      return `${COLORES_TIPO[i]} ${desde.toFixed(1)}% ${acc.toFixed(1)}%`;
    }).join(",");
    const dona = $("#analitica .donut");
    if (dona) {
      dona.style.background = `conic-gradient(${tramos})`;
      $("b", dona).textContent = total;
    }
    $$("#analitica .type-list > div").forEach((fila, i) => {
      const t = an.tipos[i];
      if (!t) return;
      $("b", fila).textContent = t.valor;
      $("em", fila).textContent = Math.round(t.valor / total * 100) + "%";
    });
    const cabTipos = $("#analitica .report .panel-head small");
    if (cabTipos) cabTipos.textContent = `${total} total`;

    // Índice de riesgo por zona (índice de anticipación)
    const zonas = [...est.zonas].sort((a, b) => b.indice - a.indice);
    const cont = $("#analitica .zones");
    if (cont) {
      cont.innerHTML = zonas.map(z => `<div title="Historial ${z.historial} · en vivo ${z.vivo} · entorno ${z.entorno}"><span>${esc(z.zona)}</span><span class="track"><i style="width:${Math.min(100, z.indice)}%"></i></span><strong>${z.indice}</strong></div>`).join("");
    }

    // Respuesta por prioridad
    $$("#analitica .response > div").forEach((fila, i) => {
      const r = an.respuesta[i];
      if (!r) return;
      $(".track i", fila).style.width = Math.min(100, r.segundos / 8.4) + "%";
      $("b", fila).textContent = mmss(r.segundos);
    });

    // Infraestructura
    const infra = $$("#analitica .report")[3];
    if (infra) {
      const nums = $$(".report-kpis article b", infra);
      if (nums[0]) nums[0].textContent = an.camaras.operativas;
      const pct = $(".panel-head b", infra);
      if (pct) pct.textContent = est.resumen.disponibilidad + "%";
    }
  }

  function pintarPatrullas(patrullas) {
    $$("#patrol-grid .patrol").forEach((tarjeta, i) => {
      const p = patrullas[i];
      if (!p) return;
      const estado = $(".status", tarjeta);
      estado.textContent = p.estado;
      estado.classList.toggle("alerting", p.estado === "Atendiendo alerta");
    });
  }

  function refrescarMiniaturas(est) {
    const ahora = Date.now();
    if (!est.camara.ok || ahora - ultimaSnap < 2000) return;
    ultimaSnap = ahora;
    $$('.camera-thumb[data-camera="0"] img').forEach(img => (img.src = `${API}/snapshot?t=${ahora}`));
  }

  function marcarConexion(ok) {
    const vivo = $(".live");
    if (vivo && vivo.lastChild) vivo.lastChild.textContent = ok ? " Actualización en vivo" : " Sin conexión con VIGÍA";
  }

  /* ---------- Ciclo principal ---------- */
  async function actualizar() {
    try {
      const r = await fetch(API + "/api/estado", { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const est = await r.json();
      if (!iniciado) iniciar(est);
      pintarResumen(est);
      pintarAlertas(est.alertas);
      pintarMapa(est.zonas);
      pintarGraficas(est.analitica);
      pintarAnalitica(est);
      pintarPatrullas(est.patrullas);
      refrescarMiniaturas(est);
      marcarConexion(true);
    } catch (e) {
      marcarConexion(false);
    }
  }

  // Botón Atender / Reabrir: el servidor guarda el cambio y mide el tiempo de respuesta
  document.addEventListener("click", e => {
    const boton = e.target.closest(".alert button[data-id]");
    if (!boton) return;
    firmaAlertas = ""; // fuerza a repintar con lo que diga el servidor
    fetch(`${API}/api/alertas/${boton.dataset.id}/atender`, { method: "POST" }).then(actualizar).catch(() => {});
  });

  actualizar();
  setInterval(actualizar, 1000);
})();
