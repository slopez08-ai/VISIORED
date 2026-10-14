"""
VISIORED — servidor local de demostración.
Fuente de video: video_demo.mp4 junto a este archivo. No utiliza webcam.
Requisitos:
    pip install flask opencv-python numpy ultralytics
Ejecución:
    python vigia_server.py
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_file
from ultralytics import YOLO

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "visiored.html"
JS_FILE = BASE_DIR / "visiored_live.js"
VIDEO_DEMO_FILE = BASE_DIR / "video_demo.mp4"
MODEL_NAME = "yolov8n.pt"

HOST = "127.0.0.1"
PORT = 5000
TZ_LOCAL = timezone(timedelta(hours=-5))
CLASES_DETECTAR = [0, 1, 2, 3, 5, 7]

NOMBRES_CLASES = {
    0: "persona",
    1: "bicicleta",
    2: "automovil",
    3: "motocicleta",
    5: "autobus",
    7: "camion"
}
YOLO_CONF = 0.35
YOLO_IMGSZ = 640
REPETIR_VIDEO_DEMO = True
JPEG_QUALITY = 78
UMBRAL_AFLUENCIA = 5
COOLDOWN_ALERTA_SEG = 45
SIMULAR_OTRAS_ZONAS = True

app = Flask(__name__)
lock = threading.RLock()
video_lock = threading.RLock()

state: dict[str, Any] = {
    "camara_ok": False,
    "fuente": "video_demo",
    "nombre_fuente": "VIDEO DE PRUEBA",
    "personas": 0,
    "detecciones": 0,
    "fotogramas": 0,
    "fps_video": 0.0,
    "video_disponible": VIDEO_DEMO_FILE.is_file(),
    "mensaje_video": "Preparando el video de prueba…",
    "inicio": datetime.now(TZ_LOCAL).isoformat(),
    "ultima_actualizacion": datetime.now(TZ_LOCAL).isoformat(),
}
video_jpg: bytes | None = None
heat_jpg: bytes | None = None
heat_accum: np.ndarray | None = None
alertas: list[dict[str, Any]] = []
alert_seq = 0
ultima_alerta_afluencia = 0.0
ultimo_frame_time = 0.0
reset_heat = threading.Event()

ZONAS = [
    {"id": "Z-01", "nombre": "Chapinero Central", "riesgo": 82, "historico": 84, "ambiente": 73},
    {"id": "Z-02", "nombre": "Zona financiera", "riesgo": 69, "historico": 72, "ambiente": 62},
    {"id": "Z-03", "nombre": "Zona universitaria", "riesgo": 57, "historico": 60, "ambiente": 55},
    {"id": "Z-04", "nombre": "Corredor comercial", "riesgo": 76, "historico": 79, "ambiente": 68},
]
PATRULLAS = [
    {"id": "P-021", "nombre": "Unidad Alfa", "estado": "Disponible", "zona": "Chapinero Central"},
    {"id": "P-034", "nombre": "Unidad Bravo", "estado": "En recorrido", "zona": "Zona financiera"},
    {"id": "P-018", "nombre": "Unidad Charlie", "estado": "Disponible", "zona": "Zona universitaria"},
    {"id": "P-042", "nombre": "Unidad Delta", "estado": "En atención", "zona": "Corredor comercial"},
]
DEMO_ALERTAS = [
    ("Concentración de personas", "Carrera 7 con Calle 72", "Media", "Afluencia", "CAM-014"),
    ("Vehículo detenido", "Corredor comercial · registro de demostración", "Baja", "Movilidad", "CAM-032"),
    ("Revisión preventiva sugerida", "Zona financiera · registro de demostración", "Alta", "Prevención", "CAM-087"),
]


def ahora_iso() -> str:
    return datetime.now(TZ_LOCAL).isoformat(timespec="seconds")


def crear_alerta(titulo: str, ubicacion: str, prioridad: str, tipo: str,
                 camara: str = "CAM-014", fuente: str = "video_demo") -> dict[str, Any]:
    global alert_seq
    with lock:
        alert_seq += 1
        alerta = {
            "id": alert_seq,
            "titulo": titulo,
            "ubicacion": ubicacion,
            "prioridad": prioridad,
            "tipo": tipo,
            "camara": camara,
            "zona": "Chapinero Central",
            "fuente": fuente,  # "video_demo" o "simulado"
            "estado": "pendiente",
            "creada": ahora_iso(),
            "hace": "ahora",
            "respuesta_seg": None,
            "atendida_en": None,
        }
        alertas.insert(0, alerta)
        del alertas[100:]
        print(f"[ALERTA {alerta['id']}] {prioridad} · {titulo} · {camara} ({fuente})")
        return dict(alerta)


def sembrar_alertas() -> None:
    for titulo, ubicacion, prioridad, tipo, camara in DEMO_ALERTAS:
        crear_alerta(titulo, ubicacion, prioridad, tipo, camara, "simulado")


def tiempo_transcurrido(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        segundos = max(0, int((datetime.now(TZ_LOCAL) - dt).total_seconds()))
        if segundos < 60:
            return "ahora" if segundos < 5 else f"hace {segundos} s"
        if segundos < 3600:
            return f"hace {segundos // 60} min"
        return f"hace {segundos // 3600} h"
    except (ValueError, TypeError):
        return "—"


def construir_estado() -> dict[str, Any]:
    with lock:
        copia_alertas = [dict(a) for a in alertas]
        s = dict(state)
    for alerta in copia_alertas:
        alerta["hace"] = tiempo_transcurrido(alerta.get("creada", ""))
    pendientes = sum(a["estado"] == "pendiente" for a in copia_alertas)
    atendidas = sum(a["estado"] == "atendida" for a in copia_alertas)
    detecciones = int(s.get("detecciones", 0))
    personas = int(s.get("personas", 0))
    return {
        "ok": True,
        "actualizado": ahora_iso(),
        "fuente": "video_demo",
        "nombre_fuente": "VIDEO DE PRUEBA",
        "camara": {
            "id": "CAM-014",
            "ubicacion": "Carrera 7 con Calle 72",
            "zona": "Chapinero Central",
            "ok": bool(s.get("camara_ok")),
            "mensaje": s.get("mensaje_video", ""),
            "video_disponible": bool(s.get("video_disponible")),
        },
       
        "resumen": {
            "personas": personas,
            "bicicletas": int(s.get("bicicletas", 0)),
            "automoviles": int(s.get("automoviles", 0)),
            "motocicletas": int(s.get("motocicletas", 0)),
            "autobuses": int(s.get("autobuses", 0)),
            "camiones": int(s.get("camiones", 0)),
            "detecciones": detecciones,
            "alertas_pendientes": pendientes,
            "alertas_atendidas": atendidas,
            "zonas_vigiladas": len(ZONAS),
            "patrullas": len(PATRULLAS),
            "nivel_riesgo": round(sum(z["riesgo"] for z in ZONAS) / len(ZONAS)),
            "fps_video": round(float(s.get("fps_video", 0)), 1),
            "fotogramas": int(s.get("fotogramas", 0)),
        },
        "alertas": copia_alertas,
        "zonas": ZONAS,
        "patrullas": PATRULLAS,
        "analitica": {
            "etiqueta": "DATOS DE DEMOSTRACIÓN",
            "incidentes": [32, 27, 38, 30, 44, 35, 29],
            "dias": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
            "tipos": [
                {"nombre": "Hurto (referencia demo)", "valor": 42},
                {"nombre": "Afluencia", "valor": 28},
                {"nombre": "Movilidad", "valor": 18},
                {"nombre": "Otros", "valor": 12},
            ],
        },
        "nota": "Las detecciones YOLO proceden del video de prueba. Las alertas y métricas marcadas como simuladas son ilustrativas; no representan incidentes reales.",
    }


def encode_jpg(frame: np.ndarray) -> bytes | None:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return buf.tobytes() if ok else None


def detection_loop() -> None:
    """Procesa únicamente el archivo de video; nunca intenta abrir una webcam."""
    global video_jpg, heat_jpg, heat_accum, ultima_alerta_afluencia, ultimo_frame_time
    print(f"Buscando video de prueba: {VIDEO_DEMO_FILE}")
    if not VIDEO_DEMO_FILE.is_file():
        with lock:
            state["camara_ok"] = False
            state["video_disponible"] = False
            state["mensaje_video"] = "No se encuentra video_demo.mp4. Pon el archivo junto a vigia_server.py."
        print("ERROR: falta video_demo.mp4. El servidor web seguirá funcionando sin video.")
        return

    print(f"Cargando modelo YOLOv8 ({MODEL_NAME})…")
    try:
        model = YOLO(MODEL_NAME)
    except Exception as exc:
        with lock:
            state["camara_ok"] = False
            state["mensaje_video"] = f"No se pudo cargar YOLO: {exc}"
        print(f"ERROR al cargar YOLO: {exc}")
        return

    cap = cv2.VideoCapture(str(VIDEO_DEMO_FILE))
    if not cap.isOpened():
        with lock:
            state["camara_ok"] = False
            state["mensaje_video"] = "No se pudo abrir video_demo.mp4. Verifica que sea un video válido."
        print("ERROR: OpenCV no pudo abrir el video de prueba.")
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_delay = 1.0 / fps if fps and fps > 1 else 1.0 / 25.0
    with lock:
        state["camara_ok"] = True
        state["video_disponible"] = True
        state["mensaje_video"] = "Video de prueba reproduciéndose y analizado por YOLOv8."
        state["fps_video"] = fps if fps else 0
    print("Fuente activa: VIDEO DE PRUEBA (sin webcam).")

    while True:
        inicio_frame = time.monotonic()
        if reset_heat.is_set():
            heat_accum = None
            reset_heat.clear()

        ok, frame = cap.read()
        if not ok:
            if REPETIR_VIDEO_DEMO:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
                if not ok:
                    with lock:
                        state["camara_ok"] = False
                        state["mensaje_video"] = "No fue posible reiniciar el video al llegar al final."
                    time.sleep(1)
                    continue
            else:
                with lock:
                    state["camara_ok"] = False
                    state["mensaje_video"] = "El video llegó al final."
                break

        try:
            results = model.track(
                frame,
                persist=True,
                classes=CLASES_DETECTAR,
                conf=YOLO_CONF,
                imgsz=YOLO_IMGSZ,
                verbose=False,
            )
            result = results[0]
            annotated = result.plot()
            h, w = frame.shape[:2]
            if heat_accum is None or heat_accum.shape != (h, w):
                heat_accum = np.zeros((h, w), dtype=np.float32)



            conteos = {clase_id: 0 for clase_id in CLASES_DETECTAR}

            if result.boxes is not None and len(result.boxes) > 0:
                xyxy = result.boxes.xyxy.cpu().numpy().astype(int)
                confs = result.boxes.conf.cpu().numpy()
                clases = result.boxes.cls.cpu().numpy().astype(int)

                for (x1, y1, x2, y2), confidence, clase_id in zip(xyxy, confs, clases):
                    if clase_id not in NOMBRES_CLASES:
                        continue

                    conteos[clase_id] += 1

                    cx = max(0, min(w - 1, (x1 + x2) // 2))
                    cy = max(0, min(h - 1, (y1 + y2) // 2))

                    cv2.circle(
                        heat_accum, (cx, cy),
                        max(12, int(w * 0.025)), 1.0, -1
                    )

                    etiqueta = f"{NOMBRES_CLASES[clase_id]} {confidence:.2f}"
                    cv2.putText(
                        annotated, etiqueta, (x1, max(18, y1 - 7)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (255, 255, 255), 1, cv2.LINE_AA
                    )

            people = conteos[0]

            # Decaimiento gradual para que el mapa de calor refleje actividad reciente.
            heat_accum *= 0.985
            heat_norm = cv2.normalize(heat_accum, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            heat_color = cv2.applyColorMap(heat_norm, cv2.COLORMAP_JET)
            heat_overlay = cv2.addWeighted(frame, 0.52, heat_color, 0.48, 0)
            cv2.putText(annotated, "VISIORED | VIDEO DE PRUEBA", (16, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(heat_overlay, "MAPA DE CALOR | VIDEO DE PRUEBA", (16, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)

            encoded_video = encode_jpg(annotated)
            encoded_heat = encode_jpg(heat_overlay)
            with video_lock:
                if encoded_video:
                    video_jpg = encoded_video
                if encoded_heat:
                    heat_jpg = encoded_heat

            
            with lock:
                state["camara_ok"] = True
                state["personas"] = conteos[0]
                state["bicicletas"] = conteos[1]
                state["automoviles"] = conteos[2]
                state["motocicletas"] = conteos[3]
                state["autobuses"] = conteos[5]
                state["camiones"] = conteos[7]
                state["detecciones"] += sum(conteos.values())
                state["fotogramas"] += 1
                state["ultima_actualizacion"] = ahora_iso()
                state["mensaje_video"] = "Video de prueba reproduciéndose y analizado por YOLOv8."

            ahora = time.monotonic()
            if people >= UMBRAL_AFLUENCIA and ahora - ultima_alerta_afluencia >= COOLDOWN_ALERTA_SEG:
                crear_alerta(
                    f"Concentración de personas detectada ({people})",
                    "Carrera 7 con Calle 72 · video de prueba",
                    "Media",
                    "Afluencia detectada por visión artificial",
                    "CAM-014",
                    "video_demo",
                )
                ultima_alerta_afluencia = ahora

        except Exception as exc:
            print(f"Error procesando fotograma: {exc}")
            with lock:
                state["mensaje_video"] = f"Error al analizar un fotograma: {exc}"

        elapsed = time.monotonic() - inicio_frame
        if frame_delay > elapsed:
            time.sleep(frame_delay - elapsed)

    cap.release()


def video_stream(which: str):
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    while True:
        with video_lock:
            frame = video_jpg if which == "video" else heat_jpg
        if frame:
            yield boundary + frame + b"\r\n"
        else:
            # Imagen neutra mientras el modelo/video se inicializa o si falta el archivo.
            canvas = np.zeros((480, 854, 3), dtype=np.uint8)
            canvas[:] = (28, 22, 35)
            msg = "Esperando video_demo.mp4..." if which == "video" else "Mapa de calor esperando video"
            cv2.putText(canvas, msg, (35, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (230, 220, 235), 2, cv2.LINE_AA)
            blank = encode_jpg(canvas)
            if blank:
                yield boundary + blank + b"\r\n"
        time.sleep(0.08)


@app.get("/")
def index():
    if not HTML_FILE.is_file():
        return "No se encuentra visiored.html junto a vigia_server.py.", 404
    return send_file(HTML_FILE)


@app.get("/visiored_live.js")
def live_js():
    if not JS_FILE.is_file():
        return "No se encuentra visiored_live.js junto a vigia_server.py.", 404
    return send_file(JS_FILE, mimetype="application/javascript")


@app.get("/video_feed")
def video_feed():
    return Response(video_stream("video"), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@app.get("/heatmap_feed")
def heatmap_feed():
    return Response(video_stream("heat"), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@app.get("/api/estado")
def api_estado():
    return jsonify(construir_estado())


@app.get("/api/snapshot")
def api_snapshot():
    with video_lock:
        current = video_jpg
    if not current:
        return jsonify({"ok": False, "mensaje": "Aún no hay fotograma disponible."}), 503
    return Response(current, mimetype="image/jpeg")


@app.post("/api/alertas/<int:alerta_id>/atender")
def api_atender_alerta(alerta_id: int):
    with lock:
        alerta = next((a for a in alertas if a["id"] == alerta_id), None)
        if alerta is None:
            return jsonify({"ok": False, "error": "Alerta no encontrada."}), 404
        if alerta["estado"] == "pendiente":
            alerta["estado"] = "atendida"
            alerta["atendida_en"] = ahora_iso()
            creada = datetime.fromisoformat(alerta["creada"])
            alerta["respuesta_seg"] = max(0, int((datetime.now(TZ_LOCAL) - creada).total_seconds()))
        else:
            alerta["estado"] = "pendiente"
            alerta["atendida_en"] = None
            alerta["respuesta_seg"] = None
        return jsonify({"ok": True, "alerta": dict(alerta)})


@app.post("/api/reset")
def api_reset():
    reset_heat.set()
    with lock:
        state["detecciones"] = 0
        state["fotogramas"] = 0
    return jsonify({"ok": True, "mensaje": "Contadores reiniciados. El video continúa reproduciéndose."})


@app.get("/api/health")
def api_health():
    with lock:
        return jsonify({
            "ok": True,
            "video_disponible": VIDEO_DEMO_FILE.is_file(),
            "camara_ok": state["camara_ok"],
            "fuente": "video_demo",
        })


if __name__ == "__main__":
    sembrar_alertas()
    hilo = threading.Thread(target=detection_loop, name="yolo-video-demo", daemon=True)
    hilo.start()
    print(f"\nVISIORED disponible en http://{HOST}:{PORT}")
    print(f"Video esperado: {VIDEO_DEMO_FILE}")
    print("Este servidor no abre ni utiliza la webcam.\n")
    app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)
