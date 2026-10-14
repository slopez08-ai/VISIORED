"""
VIGÍA - Demo de COMPORTAMIENTO: brazos levantados
===================================================

    cámara/video -> YOLOv8-pose (esqueleto) -> regla -> alerta -> panel

REGLA (explicable, no es un modelo entrenado para "delitos"):
  - Una persona "tiene los brazos arriba" si su muñeca está por encima de la
    nariz. MODO_BRAZOS = "uno"  -> basta con UN brazo
                         "ambos" -> hacen falta los dos.
  - Se genera la alerta cuando MIN_PERSONAS o más personas lo hacen a la vez
    durante SEG_MANTENER segundos (por defecto 2 personas).

Tiene su propia página: abre  http://127.0.0.1:5001
Si vigia_server.py está corriendo, la alerta también aparece en el panel
VISIORED (http://127.0.0.1:5000). Si no, esta demo funciona igual.

PRIVACIDAD: solo se usan los puntos del esqueleto; no se identifica a nadie
ni se guarda ninguna imagen.

REQUISITOS:   pip install ultralytics opencv-python numpy flask
USO:          python demo_comportamiento.py
  - Usa video_brazos.mp4 (junto a este archivo) si existe; si no, la webcam.
  - Pon comportamiento.html en la misma carpeta.
"""

import json
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_file
from ultralytics import YOLO

# ----------------------------------------------------------------------
# CONFIGURACIÓN
# ----------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "comportamiento.html"
VIDEO_BRAZOS = BASE_DIR / "video_brazos.mp4"
CAMERA_INDEX = 1                  # solo si no existe video_brazos.mp4
MODEL_NAME = "yolov8n-pose.pt"

HOST, PORT = "127.0.0.1", 5001
SERVIDOR_PANEL = "http://127.0.0.1:5000"
CAMARA_ID = "CAM-014"
UBICACION = "Carrera 7 con Calle 72 · demo de comportamiento"

MIN_PERSONAS = 2                  # personas a la vez para generar la alerta
MODO_BRAZOS = "uno"               # "uno" o "ambos"
SEG_MANTENER = 1.0                # segundos sostenido antes de alertar
GRACIA_SEG = 0.5                  # tolerancia si el detector parpadea un instante
COOLDOWN_ALERTA = 15
SEG_BANNER = 4.0

CONF_PUNTO = 0.5
MARGEN_REL = 0.25                 # la muñeca debe estar sobre la nariz por este
                                  # margen (relativo al ancho de los hombros)
STREAM_ANCHO = 960
JPEG_QUALITY = 78

NARIZ, HOMBRO_I, HOMBRO_D, MUNECA_I, MUNECA_D = 0, 5, 6, 9, 10

# ----------------------------------------------------------------------
# LA REGLA
# ----------------------------------------------------------------------
def brazos_arriba(puntos, confianza):
    """
    puntos (17, 2) y confianza (17,) de UNA persona.
    Devuelve (izquierdo_arriba, derecho_arriba).
    En una imagen, "arriba" es una coordenada y MÁS PEQUEÑA.
    """
    if any(confianza[i] < CONF_PUNTO for i in (NARIZ, HOMBRO_I, HOMBRO_D)):
        return False, False
    margen = MARGEN_REL * abs(float(puntos[HOMBRO_I][0] - puntos[HOMBRO_D][0]))
    limite = float(puntos[NARIZ][1]) - margen
    izq = bool(confianza[MUNECA_I] >= CONF_PUNTO and puntos[MUNECA_I][1] < limite)
    der = bool(confianza[MUNECA_D] >= CONF_PUNTO and puntos[MUNECA_D][1] < limite)
    return izq, der


def cuenta_persona(izq, der, modo):
    return (izq and der) if modo == "ambos" else (izq or der)


def actualizar_grupo(g, cumple, ahora):
    """
    Estado del grupo: g = {"desde": None, "ultimo_ok": 0.0}.
    Devuelve los segundos que lleva sostenida la condición (0 si no se cumple).
    """
    if cumple:
        g["ultimo_ok"] = ahora
        if g["desde"] is None:
            g["desde"] = ahora
    elif g["desde"] is not None and ahora - g["ultimo_ok"] > GRACIA_SEG:
        g["desde"] = None
    return (ahora - g["desde"]) if g["desde"] is not None else 0.0


# ----------------------------------------------------------------------
# ESTADO COMPARTIDO
# ----------------------------------------------------------------------
lock = threading.RLock()
config = {"min_personas": MIN_PERSONAS, "modo": MODO_BRAZOS}
state = {"ok": False, "mensaje": "Cargando modelo…", "fuente": "", "personas": 0,
         "con_brazos": 0, "sosteniendo": 0.0, "banner_hasta": 0.0,
         "panel": "sin enviar", "eventos": []}
video_jpg = None
app = Flask(__name__)


def evento(texto):
    with lock:
        state["eventos"].insert(0, {"hora": datetime.now().strftime("%H:%M:%S"), "texto": texto})
        del state["eventos"][8:]
    print(f"[{datetime.now():%H:%M:%S}] {texto}")


def enviar_alerta(n):
    datos = {
        "titulo": f"Brazos levantados detectados ({n} personas)",
        "ubicacion": UBICACION,
        "prioridad": "Alta",
        "tipo": "Comportamiento · posible solicitud de ayuda",
        "camara": CAMARA_ID,
    }
    req = urllib.request.Request(
        f"{SERVIDOR_PANEL}/api/alertas/externa", data=json.dumps(datos).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def reducir(img):
    h, w = img.shape[:2]
    if w <= STREAM_ANCHO:
        return img
    return cv2.resize(img, (STREAM_ANCHO, int(h * STREAM_ANCHO / w)), interpolation=cv2.INTER_AREA)


# ----------------------------------------------------------------------
# HILO DE DETECCIÓN
# ----------------------------------------------------------------------
def detection_loop():
    global video_jpg
    model = YOLO(MODEL_NAME)

    es_video = VIDEO_BRAZOS.is_file()
    cap = cv2.VideoCapture(str(VIDEO_BRAZOS) if es_video else CAMERA_INDEX)
    nombre = VIDEO_BRAZOS.name if es_video else "webcam"
    if not cap.isOpened():
        with lock:
            state["mensaje"] = f"No se pudo abrir la fuente ({nombre})."
        print("ERROR: no se pudo abrir la fuente de video.")
        return
    fps = cap.get(cv2.CAP_PROP_FPS)
    retardo = 1.0 / fps if es_video and fps and fps > 1 else 0.0
    with lock:
        state.update(ok=True, fuente=nombre, mensaje="Analizando con YOLOv8-pose.")
    print(f"Fuente: {nombre}. Abre http://{HOST}:{PORT}")

    grupo = {"desde": None, "ultimo_ok": 0.0}
    ultima_alerta = -1e9

    while True:
        inicio = time.monotonic()
        ok, frame = cap.read()
        if not ok and es_video:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok:
            with lock:
                state.update(ok=False, mensaje="Se perdió la señal de video.")
            break
        ahora = time.monotonic()

        with lock:
            modo, minimo = config["modo"], config["min_personas"]

        r = model.track(frame, persist=True, verbose=False)[0]
        anotado = r.plot()
        personas = con_brazos = 0

        if r.keypoints is not None and len(r.keypoints) > 0:
            xy = r.keypoints.xy.cpu().numpy()
            conf = (r.keypoints.conf.cpu().numpy() if r.keypoints.conf is not None
                    else np.ones(xy.shape[:2]))
            personas = len(xy)
            for puntos, c in zip(xy, conf):
                izq, der = brazos_arriba(puntos, c)
                if cuenta_persona(izq, der, modo):
                    con_brazos += 1
                if izq or der:
                    x, y = int(puntos[NARIZ][0]), int(puntos[NARIZ][1])
                    txt = "2 BRAZOS" if (izq and der) else "1 BRAZO"
                    cv2.putText(anotado, txt, (max(5, x - 50), max(30, y - 60)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 165, 255), 3, cv2.LINE_AA)

        seg = actualizar_grupo(grupo, con_brazos >= minimo, ahora)
        if seg >= SEG_MANTENER and ahora - ultima_alerta >= COOLDOWN_ALERTA:
            ultima_alerta = ahora
            llego = enviar_alerta(con_brazos)
            with lock:
                state["banner_hasta"] = ahora + SEG_BANNER
                state["panel"] = "enviada al panel" if llego else "panel no disponible"
            evento(f"ALERTA: {con_brazos} personas con brazos levantados"
                   + (" → enviada al panel" if llego else " (panel no disponible)"))

        if ahora < state["banner_hasta"]:
            cv2.rectangle(anotado, (0, 0), (anotado.shape[1], 70), (40, 40, 200), -1)
            cv2.putText(anotado, "ALERTA: brazos levantados", (20, 47),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3, cv2.LINE_AA)

        ok_jpg, buf = cv2.imencode(".jpg", reducir(anotado), [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        with lock:
            if ok_jpg:
                video_jpg = buf.tobytes()
            state.update(personas=personas, con_brazos=con_brazos, sosteniendo=seg)

        if retardo:
            time.sleep(max(0.0, retardo - (time.monotonic() - inicio)))
    cap.release()


# ----------------------------------------------------------------------
# SERVIDOR WEB
# ----------------------------------------------------------------------
ORIGENES = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}


@app.before_request
def proteger_escrituras():
    if request.method == "POST":
        origen = request.headers.get("Origin")
        if origen is not None and origen not in ORIGENES:
            return jsonify({"ok": False, "error": "Origen no permitido."}), 403


@app.get("/")
def inicio():
    if not HTML_FILE.is_file():
        return "Pon comportamiento.html junto a demo_comportamiento.py.", 404
    return send_file(HTML_FILE)


@app.get("/video_feed")
def video_feed():
    def stream():
        while True:
            with lock:
                jpg = video_jpg
            if jpg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            time.sleep(0.06)
    return Response(stream(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/estado")
def api_estado():
    with lock:
        s = dict(state)
        s["eventos"] = list(state["eventos"])
        c = dict(config)
    s["alerta_activa"] = time.monotonic() < s.pop("banner_hasta")
    s.update(min_personas=c["min_personas"], modo=c["modo"], seg_mantener=SEG_MANTENER,
             sosteniendo=round(s["sosteniendo"], 1))
    return jsonify(s)


@app.post("/api/config")
def api_config():
    d = request.get_json(silent=True) or {}
    with lock:
        if d.get("min_personas") in (1, 2, 3, 4, 5):
            config["min_personas"] = d["min_personas"]
        if d.get("modo") in ("uno", "ambos"):
            config["modo"] = d["modo"]
        c = dict(config)
    evento(f"Regla: {c['min_personas']} persona(s) · {'un brazo' if c['modo'] == 'uno' else 'ambos brazos'}")
    return jsonify({"ok": True, **c})


if __name__ == "__main__":
    threading.Thread(target=detection_loop, daemon=True).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)
