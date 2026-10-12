"""
VIGÍA - Motor de detección y anticipación para el panel VISIORED
=================================================================

VIGÍA combina tres fuentes para ANTICIPAR el delito (no solo reaccionar):

  1. CÁMARAS (en vivo)  -> YOLOv8 detecta personas, el tracking les da un ID
                           anónimo y de ahí salen eventos: afluencia,
                           permanencia prolongada y movimiento inusual.
  2. HISTORIAL DE HURTOS -> perfil de riesgo por zona, hora y día.
  3. DATOS DEL ENTORNO   -> franja nocturna y fin de semana.

Con esas tres fuentes se calcula un ÍNDICE DE ANTICIPACIÓN por zona:

    índice = 0.55 * historial + 0.30 * señal en vivo + 0.15 * entorno

Es una fórmula sencilla y explicable (ideal para el prototipo): los pesos
se pueden ajustar más adelante con datos reales de la Policía/Secretaría
de Seguridad.

QUÉ ES REAL Y QUÉ ES DEMOSTRATIVO (para la sustentación):
  - REAL: la webcam (CAM-014), el conteo de personas, el mapa de calor,
          los eventos que genera el tracking y los tiempos de respuesta
          cuando el operador pulsa "Atender".
  - DEMO: el historial de hurtos, las otras 4 cámaras, las patrullas y las
          alertas de otras zonas (se simulan con SIMULAR_OTRAS_ZONAS).

PRIVACIDAD POR DISEÑO: no se guardan imágenes, rostros ni identidades.
Solo conteos, posiciones y un ID numérico temporal por persona.

REQUISITOS:
    pip install ultralytics opencv-python numpy flask

USO:
    1. Pon en la misma carpeta:  vigia_server.py, visiored_live.js
       y tu HTML del panel guardado como  visiored.html
    2. python vigia_server.py
    3. Abre  http://127.0.0.1:5000  en el navegador.
       (El servidor le agrega el script al HTML solo; no hay que tocarlo.)

Para salir: Ctrl + C.
"""

import random
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, abort, jsonify, request, send_file
from ultralytics import YOLO

# ----------------------------------------------------------------------
# CONFIGURACIÓN
# ----------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "visiored.html"
JS_FILE = BASE_DIR / "visiored_live.js"

# Colombia no tiene horario de verano: UTC-5 fijo.
TZ = timezone(timedelta(hours=-5))

# --- Detección (igual que el demo original) ---
MODEL_NAME = "yolov8n.pt"
PERSON_CLASS_ID = 0
CAMERA_INDEX = 0
DECAY_RATE = 0.97
HEAT_INTENSITY = 40
HEATMAP_ALPHA = 0.5
JPEG_QUALITY = 75

# --- La webcam hace de esta cámara del panel ---
CAMARA_ID = "CAM-014"
CAMARA_UBICACION = "Carrera 7 con Calle 72"
CAMARA_ZONA = "Chapinero Central"

# --- Reglas de eventos (ajústalas según la demo) ---
UMBRAL_AFLUENCIA = 5        # personas a la vez -> "Concentración de personas"
PERMANENCIA_SEG = 30        # segundos quieto en el mismo sitio -> permanencia
RADIO_PERMANENCIA = 0.12    # "quieto" = se movió menos del 12% del ancho
VELOCIDAD_INUSUAL = 0.9     # anchos de imagen por segundo -> movimiento brusco
COOLDOWN_AFLUENCIA = 60     # segundos entre alertas de afluencia
COOLDOWN_VELOCIDAD = 20     # segundos entre alertas de movimiento por persona
CIERRE_ALERTA_VIVA = 600    # las alertas reales se cierran solas a los 10 min

# --- Servidor ---
HOST = "127.0.0.1"          # solo este computador. Cámbialo a 0.0.0.0 con cuidado.
PORT = 5000
SIMULAR_OTRAS_ZONAS = True  # alertas demo en las otras zonas de Chapinero

# ----------------------------------------------------------------------
# DATOS DEMOSTRATIVOS: zonas, historial y entorno
# ----------------------------------------------------------------------
# zona: (riesgo histórico base 0-1, posición en el mapa [x%, y%])
ZONAS = {
    "Chapinero Central": (0.86, (52, 47)),
    "Zona Rosa": (0.64, (36, 28)),
    "El Chicó": (0.48, (56, 22)),
    "Quinta Camacho": (0.40, (40, 62)),
    "Rosales": (0.29, (72, 40)),
    "Marly": (0.35, (61, 73)),
}

# Qué tan peligrosa es cada hora (0-23) respecto a su pico a las 18:00.
PERFIL_HORA = [
    0.35, 0.25, 0.20, 0.15, 0.12, 0.15, 0.25, 0.35, 0.40, 0.45, 0.50, 0.55,
    0.60, 0.60, 0.62, 0.65, 0.72, 0.85, 1.00, 0.95, 0.85, 0.70, 0.55, 0.45,
]
# Lunes=0 ... domingo=6
FACTOR_DIA = [0.85, 0.85, 0.90, 0.95, 1.00, 1.00, 0.90]

# Totales de la última semana (demo) y su reparto por tipo.
TENDENCIA_BASE = [64, 60, 63, 58, 56, 53, 51]
TIPOS_BASE = [
    ("Hurto a personas", 154),
    ("Comportamiento sospechoso", 109),
    ("Alteración del orden", 77),
    ("Accidente vial", 41),
    ("Otros", 24),
]
HORAS_PANEL = list(range(12, 22))  # barras de "Alertas por hora": 12:00-21:00
HORAS_BASE = [4, 5, 7, 9, 12, 16, 18, 14, 11, 8]
RESP_BASE = {"Crítica": 194, "Alta": 308, "Media": 512, "Baja": 765}  # segundos
RESP_MEDIO_BASE = 402
RESUELTOS_BASE = 0.876
DETECCIONES_BASE = 1284
CAMARAS_TOTAL = 252

PATRULLAS = [
    ("VSR-P01", "Chapinero Central", "En recorrido"),
    ("VSR-P02", "Zona Rosa", "En recorrido"),
    ("VSR-P03", "El Chicó", "En recorrido"),
    ("VSR-P04", "Rosales", "Punto fijo"),
    ("VSR-P05", "Quinta Camacho", "Disponible"),
    ("VSR-P06", "Marly", "En recorrido"),
]

# Alertas demo: (título, ubicación, cámara, zona, prioridad, tipo de incidente)
PLANTILLAS_DEMO = [
    ("Movimiento inusual detectado", "Av. Caracas con Calle 63", "CAM-087",
     "Chapinero Central", "Alta", "Comportamiento sospechoso"),
    ("Concentración de personas", "Parque de la 93, acceso norte", "CAM-032",
     "El Chicó", "Media", "Alteración del orden"),
    ("Vehículo detenido", "Calle 63 con Carrera 13", "CAM-104",
     "Quinta Camacho", "Baja", "Accidente vial"),
    ("Posible hurto a transeúnte", "Zona G, Carrera 5", "CAM-119",
     "Zona Rosa", "Crítica", "Hurto a personas"),
    ("Comportamiento sospechoso", "Carrera 11 con Calle 82", "CAM-056",
     "Rosales", "Media", "Comportamiento sospechoso"),
    ("Riña en vía pública", "Calle 53 con Carrera 7", "CAM-071",
     "Marly", "Alta", "Alteración del orden"),
]

# ----------------------------------------------------------------------
# ESTADO COMPARTIDO (hilo de detección + hilo de mantenimiento + Flask)
# ----------------------------------------------------------------------
lock = threading.RLock()
state = {
    "video_jpg": None,
    "heat_jpg": None,
    "personas": 0,
    "pico": 0,
    "camara_ok": False,
    "detecciones": 0,  # personas distintas (IDs nuevos) vistas desde que arrancó
}
alertas = []
alert_seq = 0
reset_heat = threading.Event()

app = Flask(__name__)
ORIGENES = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}", "null"}


# ----------------------------------------------------------------------
# MODELO DE ANTICIPACIÓN
# ----------------------------------------------------------------------
def entorno(hora, dow):
    """Datos del entorno (0-100): franja nocturna y fin de semana."""
    noche = 1 if (hora >= 19 or hora < 6) else 0
    finde = 1 if dow >= 4 else 0  # viernes, sábado, domingo
    return 60 * noche + 40 * finde


def calcular_zonas():
    ahora = time.time()
    dt = datetime.now(TZ)
    hora, dow = dt.hour, dt.weekday()
    ent = entorno(hora, dow)

    with lock:
        personas = state["personas"]
        recientes = [a for a in alertas if ahora - a["creada"] < 300]

    salida = []
    for zona, (base, pos) in ZONAS.items():
        hist = 100 * base * PERFIL_HORA[hora] * FACTOR_DIA[dow]
        n_rec = sum(1 for a in recientes if a["zona"] == zona)
        vivo = min(100, n_rec * 25)
        if zona == CAMARA_ZONA:
            vivo = min(100, vivo + 60 * min(1, personas / UMBRAL_AFLUENCIA))
        indice = 0.55 * hist + 0.30 * vivo + 0.15 * ent
        salida.append(
            {
                "zona": zona,
                "indice": round(indice),
                "historial": round(hist),
                "vivo": round(vivo),
                "entorno": round(ent),
                "pos": list(pos),
            }
        )
    return salida


# ----------------------------------------------------------------------
# ALERTAS
# ----------------------------------------------------------------------
def crear_alerta(titulo, ubicacion, camara, zona, prioridad, tipo_incidente,
                 fuente="vivo", antiguedad=0, cierra_en=None):
    global alert_seq
    ahora = time.time()
    with lock:
        alert_seq += 1
        a = {
            "id": alert_seq,
            "titulo": titulo,
            "ubicacion": ubicacion,
            "camara": camara,
            "zona": zona,
            "prioridad": prioridad,
            "tipo_incidente": tipo_incidente,
            "fuente": fuente,
            "creada": ahora - antiguedad,
            "estado": "activa",
            "resp": None,  # segundos hasta que alguien la atendió
            "cierra_en": cierra_en,
        }
        alertas.append(a)
        if len(alertas) > 300:
            del alertas[:50]
    print(f"[ALERTA {a['id']}] {prioridad} · {titulo} · {camara} ({fuente})")
    return a


def sembrar_alertas():
    """Tres alertas de ejemplo al arrancar, para que el panel no esté vacío."""
    if not SIMULAR_OTRAS_ZONAS:
        return
    ahora = time.time()
    for plantilla, antig in zip(PLANTILLAS_DEMO[:3], (840, 480, 120)):
        t, u, c, z, p, ti = plantilla
        crear_alerta(t, u, c, z, p, ti, fuente="demo", antiguedad=antig,
                     cierra_en=ahora + random.uniform(90, 300))


def generar_alerta_demo():
    with lock:
        activas = sum(1 for a in alertas if a["estado"] == "activa")
    if activas >= 8:
        return
    indices = {z["zona"]: z["indice"] for z in calcular_zonas()}
    # Las zonas con más riesgo reciben más alertas simuladas.
    pesos = [indices[p[3]] + 5 for p in PLANTILLAS_DEMO]
    t, u, c, z, p, ti = random.choices(PLANTILLAS_DEMO, weights=pesos)[0]
    crear_alerta(t, u, c, z, p, ti, fuente="demo",
                 cierra_en=time.time() + random.uniform(60, 240))


def mantenimiento_loop():
    proxima = time.time() + random.uniform(25, 60)
    while True:
        time.sleep(1)
        ahora = time.time()
        with lock:
            for a in alertas:
                if a["estado"] == "activa" and a["cierra_en"] and ahora >= a["cierra_en"]:
                    a["estado"] = "atendida"
                    # Solo cuenta como tiempo de respuesta si fue una atención
                    # (demo); las reales que expiran solas no cuentan.
                    a["resp"] = (ahora - a["creada"]) if a["fuente"] == "demo" else None
        if SIMULAR_OTRAS_ZONAS and ahora >= proxima:
            generar_alerta_demo()
            proxima = ahora + random.uniform(40, 90)


# ----------------------------------------------------------------------
# HILO DE DETECCIÓN (el demo original + tracking + eventos)
# ----------------------------------------------------------------------
def encode_jpg(img):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return buf.tobytes() if ok else None


def detection_loop():
    print("Cargando modelo YOLOv8... (la primera vez puede tardar un poco)")
    model = YOLO(MODEL_NAME)

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("AVISO: no se pudo abrir la cámara. El panel seguirá con datos demo.")
        return
    ret, frame = cap.read()
    if not ret:
        print("AVISO: no se pudo leer un frame de la cámara.")
        return

    height, width = frame.shape[:2]
    heat = np.zeros((height, width), dtype=np.float32)
    tracks = {}
    ultima_afluencia = 0.0

    with lock:
        state["camara_ok"] = True
    print(f"Cámara lista. Abre http://{HOST}:{PORT} en el navegador.")

    while True:
        ret, frame = cap.read()
        if not ret:
            with lock:
                state["camara_ok"] = False
            break
        ahora = time.time()

        if reset_heat.is_set():
            heat[:] = 0
            reset_heat.clear()
            print("Mapa de calor reiniciado.")

        # --- 1. Detección + tracking (cada persona recibe un ID temporal) ---
        results = model.track(frame, persist=True, classes=[PERSON_CLASS_ID],
                              verbose=False)
        boxes = results[0].boxes
        xyxy = boxes.xyxy.cpu().numpy() if len(boxes) else []
        ids = (boxes.id.int().cpu().tolist()
               if len(boxes) and boxes.id is not None else [None] * len(boxes))
        person_count = len(boxes)

        for (x1, y1, x2, y2), tid in zip(xyxy, ids):
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            cx, cy = (x1 + x2) // 2, y2  # los "pies" = posición en el piso
            color = (0, 255, 0)  # verde: normal
            etiqueta = ""

            if tid is not None:
                t = tracks.get(tid)
                if t is None:
                    t = tracks[tid] = {"primera": ahora, "origen": (cx, cy),
                                       "pos": (cx, cy), "t": ahora, "vel": 0.0,
                                       "perm": False, "vcool": 0.0}
                    with lock:
                        state["detecciones"] += 1
                else:
                    dt = ahora - t["t"]
                    if dt > 0:
                        v = np.hypot(cx - t["pos"][0], cy - t["pos"][1]) / width / dt
                        t["vel"] = 0.7 * t["vel"] + 0.3 * v  # suavizado
                    t["pos"], t["t"] = (cx, cy), ahora

                permanencia = ahora - t["primera"]
                desplaz = np.hypot(cx - t["origen"][0], cy - t["origen"][1]) / width
                etiqueta = f"#{tid} {int(permanencia)}s"

                # Evento: permanencia prolongada en el mismo punto
                if permanencia >= PERMANENCIA_SEG and desplaz <= RADIO_PERMANENCIA:
                    color = (0, 165, 255)  # ámbar
                    if not t["perm"]:
                        t["perm"] = True
                        crear_alerta("Permanencia prolongada", CAMARA_UBICACION,
                                     CAMARA_ID, CAMARA_ZONA, "Media",
                                     "Comportamiento sospechoso",
                                     cierra_en=ahora + CIERRE_ALERTA_VIVA)

                # Evento: movimiento brusco / carrera
                if t["vel"] >= VELOCIDAD_INUSUAL:
                    color = (0, 0, 255)  # rojo
                    if ahora - t["vcool"] > COOLDOWN_VELOCIDAD:
                        t["vcool"] = ahora
                        crear_alerta("Movimiento inusual detectado", CAMARA_UBICACION,
                                     CAMARA_ID, CAMARA_ZONA, "Alta",
                                     "Comportamiento sospechoso",
                                     cierra_en=ahora + CIERRE_ALERTA_VIVA)

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            if etiqueta:
                cv2.putText(frame, etiqueta, (x1, max(15, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
            cv2.circle(heat, (cx, cy), radius=30, color=HEAT_INTENSITY, thickness=-1)

        # Evento: afluencia (muchas personas a la vez)
        if person_count >= UMBRAL_AFLUENCIA and ahora - ultima_afluencia > COOLDOWN_AFLUENCIA:
            ultima_afluencia = ahora
            crear_alerta("Concentración de personas", CAMARA_UBICACION, CAMARA_ID,
                         CAMARA_ZONA, "Alta", "Alteración del orden",
                         cierra_en=ahora + CIERRE_ALERTA_VIVA)

        # Olvidamos a quien dejó de verse hace más de 3 s
        for k in [k for k, t in tracks.items() if ahora - t["t"] > 3]:
            del tracks[k]

        # --- 2. Mapa de calor (enfriamiento + color) ---
        heat *= DECAY_RATE
        heat_u8 = np.clip(heat, 0, 255).astype(np.uint8)
        heat_color = cv2.applyColorMap(heat_u8, cv2.COLORMAP_JET)
        combinado = cv2.addWeighted(frame, 1 - HEATMAP_ALPHA, heat_color, HEATMAP_ALPHA, 0)

        for img in (frame, combinado):
            cv2.putText(img, f"Personas: {person_count}", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

        video_jpg, heat_jpg = encode_jpg(frame), encode_jpg(combinado)
        with lock:
            state["video_jpg"], state["heat_jpg"] = video_jpg, heat_jpg
            state["personas"] = person_count
            state["pico"] = max(state["pico"], person_count)

    cap.release()


# ----------------------------------------------------------------------
# CONSTRUCCIÓN DEL ESTADO PARA EL PANEL
# ----------------------------------------------------------------------
def hace(seg):
    if seg < 60:
        return f"Hace {int(seg)} s"
    if seg < 3600:
        return f"Hace {int(seg // 60)} min"
    return f"Hace {int(seg // 3600)} h"


def construir_estado():
    ahora = time.time()
    zonas = calcular_zonas()
    dt = datetime.now(TZ)

    with lock:
        copia = [dict(a) for a in alertas]
        personas, pico = state["personas"], state["pico"]
        camara_ok, detecciones = state["camara_ok"], state["detecciones"]

    activas = [a for a in copia if a["estado"] == "activa"]
    cerradas = [a for a in copia if a["estado"] == "atendida"]
    activas.sort(key=lambda a: -a["creada"])
    cerradas.sort(key=lambda a: -a["creada"])
    visibles = (activas + cerradas)[:4]

    # --- Resumen (tarjetas superiores) ---
    media_riesgo = sum(z["indice"] for z in zonas) / len(zonas)
    operativas = (CAMARAS_TOTAL - 5) + (1 if camara_ok else 0)
    atendiendo_zonas = {a["zona"] for a in activas}
    patrullas = [
        {"codigo": c, "zona": z,
         "estado": "Atendiendo alerta" if z in atendiendo_zonas else base}
        for c, z, base in PATRULLAS
    ]
    n_atendiendo = sum(1 for p in patrullas if p["estado"] == "Atendiendo alerta")

    # --- Analítica ---
    total = sum(v for _, v in TIPOS_BASE) + len(copia)
    tipos = []
    for nombre, base in TIPOS_BASE:
        extra = sum(1 for a in copia if a["tipo_incidente"] == nombre)
        tipos.append({"nombre": nombre, "valor": base + extra})

    tendencia = list(TENDENCIA_BASE)
    tendencia[-1] += len(copia)
    mitad_a = sum(tendencia[:3]) / 3
    mitad_b = sum(tendencia[-3:]) / 3
    variacion = (mitad_b / mitad_a - 1) * 100

    horas = []
    for h, base in zip(HORAS_PANEL, HORAS_BASE):
        reales = sum(1 for a in copia if datetime.fromtimestamp(a["creada"], TZ).hour == h)
        horas.append({"hora": h, "valor": base + reales})
    pico_hora = max(horas, key=lambda x: x["valor"])["hora"]

    respuesta = []
    for prioridad, base in RESP_BASE.items():
        xs = [a["resp"] for a in cerradas if a["prioridad"] == prioridad and a["resp"] is not None]
        respuesta.append({"prioridad": prioridad,
                          "segundos": (base * 3 + sum(xs)) / (3 + len(xs))})
    todos = [a["resp"] for a in cerradas if a["resp"] is not None]
    tiempo_medio = (RESP_MEDIO_BASE * 10 + sum(todos)) / (10 + len(todos))
    resueltos = (RESUELTOS_BASE * 405 + len(cerradas)) / (405 + len(copia)) * 100

    return {
        "camara": {"id": CAMARA_ID, "nombre": CAMARA_UBICACION.replace(" con ", " / "),
                   "zona": CAMARA_ZONA, "ok": camara_ok, "personas": personas,
                   "pico": pico, "umbral": UMBRAL_AFLUENCIA},
        "resumen": {
            "alertas_activas": len(activas),
            "alertas_ultima_hora": sum(1 for a in copia if ahora - a["creada"] < 3600),
            "camaras_operativas": operativas,
            "disponibilidad": round(operativas / CAMARAS_TOTAL * 100, 1),
            "nivel_prevencion": round(100 - 0.8 * media_riesgo),
            "patrullas_servicio": 36,  # demo
            "patrullas_atendiendo": 10 + n_atendiendo,  # demo + las del modelo
        },
        "alertas": [
            {"id": a["id"], "titulo": a["titulo"], "ubicacion": a["ubicacion"],
             "camara": a["camara"], "prioridad": a["prioridad"],
             "estado": a["estado"], "hace": hace(ahora - a["creada"]),
             "fuente": a["fuente"]}
            for a in visibles
        ],
        "zonas": zonas,
        "analitica": {
            "incidentes_total": total,
            "tiempo_medio": tiempo_medio,
            "resueltos_pct": round(resueltos, 1),
            "detecciones_ia": DETECCIONES_BASE + detecciones,
            "tipos": tipos,
            "respuesta": respuesta,
            "tendencia": tendencia,
            "variacion_pct": round(variacion, 1),
            "horas": horas,
            "pico_hora": pico_hora,
            "camaras": {"operativas": operativas, "revision": 4, "sin_conexion": 2},
        },
        "patrullas": patrullas,
        "hora": dt.strftime("%H:%M:%S"),
    }


# ----------------------------------------------------------------------
# SERVIDOR WEB
# ----------------------------------------------------------------------
@app.before_request
def proteger_escrituras():
    # Anti-CSRF básico: una web ajena no puede "atender" alertas por nosotros.
    if request.method == "POST":
        origen = request.headers.get("Origin")
        if origen is not None and origen not in ORIGENES:
            abort(403)


@app.after_request
def cabeceras(resp):
    origen = request.headers.get("Origin")
    if origen in ORIGENES:
        resp.headers["Access-Control-Allow-Origin"] = origen
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/")
def inicio():
    if not HTML_FILE.exists():
        return ("Guarda tu HTML del panel como 'visiored.html' junto a "
                "vigia_server.py y recarga esta página.",
                200, {"Content-Type": "text/plain; charset=utf-8"})
    html = HTML_FILE.read_text(encoding="utf-8")
    if "visiored_live.js" not in html:
        tag = '<script src="/visiored_live.js"></script>'
        html = html.replace("</body>", tag + "\n</body>") if "</body>" in html else html + tag
    return Response(html, mimetype="text/html")


@app.route("/visiored_live.js")
def js_panel():
    if not JS_FILE.exists():
        abort(404)
    return send_file(JS_FILE, mimetype="application/javascript")


def mjpeg(clave):
    while True:
        with lock:
            jpg = state[clave]
        if jpg is not None:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        time.sleep(0.06)  # ~15 fps, suficiente y liviano


@app.route("/video_feed")
def video_feed():
    return Response(mjpeg("video_jpg"), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/heatmap_feed")
def heatmap_feed():
    return Response(mjpeg("heat_jpg"), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/snapshot")
def snapshot():
    with lock:
        jpg = state["video_jpg"]
    if jpg is None:
        abort(404)
    return Response(jpg, mimetype="image/jpeg")


@app.route("/api/estado")
def api_estado():
    return jsonify(construir_estado())


@app.route("/api/alertas/<int:aid>/atender", methods=["POST"])
def api_atender(aid):
    """Alterna una alerta entre activa y atendida (botón Atender/Reabrir)."""
    ahora = time.time()
    with lock:
        for a in alertas:
            if a["id"] == aid:
                if a["estado"] == "activa":
                    a["estado"] = "atendida"
                    a["resp"] = ahora - a["creada"]  # tiempo de respuesta real
                else:
                    a["estado"] = "activa"
                    a["resp"] = None
                    a["cierra_en"] = ahora + CIERRE_ALERTA_VIVA
                return jsonify({"ok": True, "estado": a["estado"]})
    abort(404)


@app.route("/api/reset", methods=["POST"])
def api_reset():
    reset_heat.set()
    with lock:
        state["pico"] = 0
    return jsonify({"ok": True})


if __name__ == "__main__":
    sembrar_alertas()
    threading.Thread(target=detection_loop, daemon=True).start()
    threading.Thread(target=mantenimiento_loop, daemon=True).start()
    app.run(host=HOST, port=PORT, threaded=True, debug=False)
