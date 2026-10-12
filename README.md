# VISIORED

Plataforma que combina inteligencia artificial y datos estadísticos para anticipar el delito. Integra cámaras de vigilancia, historial de hurtos y datos del entorno para identificar patrones y apoyar la prevención.

## Visión

VISIORED busca pasar de reaccionar ante el delito a anticiparlo. El piloto se desarrolla en **Chapinero (Bogotá)**, con la meta de expandirse a **5 localidades para 2027**.

## Componentes

- **VISIORED · Centro de comando** (HTML): panel con resumen, cámaras, mapa operativo, analítica y patrullas.
- **Detección y mapa de calor** (Python): `heatmap_demo.py` usa YOLOv8 y webcam para detectar y generar un mapa de calor. Está en proceso de conexión con el panel VISIORED.

## Tecnologías

- Python
- YOLOv8
- OpenCV
- HTML / CSS / JavaScript

## Estado

Proyecto en fase de **prototipo**.

## Equipo

Estudiantes de primer semestre que combinan las áreas de Sistemas, Estadística, Inteligencia Artificial y Ciberseguridad:

- Sebastián
- Jeison
- Natalia
- Valentina
- Salomé

## Cómo ejecutar el demo

```bash
pip install ultralytics opencv-python
python heatmap_demo.py
```
