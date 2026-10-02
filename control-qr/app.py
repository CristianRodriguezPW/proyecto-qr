"""
Control QR - Backend en Flask
Proyecto: control de acceso mediante código QR
"""

import sqlite3
import uuid
import os
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, jsonify
import qrcode

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "control_qr.db")
QR_FOLDER = os.path.join(BASE_DIR, "static", "qr")

os.makedirs(QR_FOLDER, exist_ok=True)


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cedula TEXT UNIQUE NOT NULL,
            nombre TEXT NOT NULL,
            correo TEXT NOT NULL,
            telefono TEXT,
            marca_equipo TEXT NOT NULL,
            color_equipo TEXT NOT NULL,
            qr_uuid TEXT UNIQUE NOT NULL,
            fecha_registro TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bitacora (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,           -- 'entrada' o 'salida'
            fecha_hora TEXT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
        )
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def generar_qr(qr_uuid):
    """Genera la imagen del QR a partir del identificador único y la guarda."""
    img = qrcode.make(qr_uuid)
    ruta = os.path.join(QR_FOLDER, f"{qr_uuid}.png")
    img.save(ruta)
    return ruta


def ultimo_movimiento(conn, usuario_id):
    """Devuelve 'entrada' o 'salida' o None si nunca ha tenido movimientos."""
    fila = conn.execute(
        "SELECT tipo FROM bitacora WHERE usuario_id = ? ORDER BY id DESC LIMIT 1",
        (usuario_id,),
    ).fetchone()
    return fila["tipo"] if fila else None


# ---------------------------------------------------------------------------
# Vistas del VISITANTE (registro y consulta de su propio QR)
# ---------------------------------------------------------------------------
@app.route("/")
def inicio():
    return render_template("index.html")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "GET":
        return render_template("registro.html", error=None)

    # POST: procesar formulario
    cedula = request.form.get("cedula", "").strip()
    nombre = request.form.get("nombre", "").strip()
    correo = request.form.get("correo", "").strip()
    telefono = request.form.get("telefono", "").strip()
    marca = request.form.get("marca", "").strip()
    color = request.form.get("color", "").strip()

    if not all([cedula, nombre, correo, marca, color]):
        return render_template("registro.html", error="Faltan campos obligatorios.")

    conn = get_db()
    existente = conn.execute(
        "SELECT * FROM usuarios WHERE cedula = ?", (cedula,)
    ).fetchone()

    if existente:
        conn.close()
        # Ya existe: lo mandamos directo a ver su QR, no se duplica el registro
        return redirect(url_for("ver_qr", qr_uuid=existente["qr_uuid"]))

    nuevo_uuid = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO usuarios
           (cedula, nombre, correo, telefono, marca_equipo, color_equipo, qr_uuid, fecha_registro)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (cedula, nombre, correo, telefono, marca, color, nuevo_uuid,
         datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    generar_qr(nuevo_uuid)

    return redirect(url_for("ver_qr", qr_uuid=nuevo_uuid))


@app.route("/mi-qr", methods=["GET", "POST"])
def buscar_qr():
    """Permite al visitante recuperar su QR ya generado usando su cedula,
    sin exponerle nunca la pantalla de escaneo (esa es del guardia)."""
    if request.method == "GET":
        return render_template("buscar_qr.html", error=None)

    cedula = request.form.get("cedula", "").strip()
    conn = get_db()
    usuario = conn.execute(
        "SELECT * FROM usuarios WHERE cedula = ?", (cedula,)
    ).fetchone()
    conn.close()

    if not usuario:
        return render_template("buscar_qr.html", error="No encontramos un registro con esa cedula.")

    return redirect(url_for("ver_qr", qr_uuid=usuario["qr_uuid"]))


@app.route("/qr/<qr_uuid>")
def ver_qr(qr_uuid):
    conn = get_db()
    usuario = conn.execute(
        "SELECT * FROM usuarios WHERE qr_uuid = ?", (qr_uuid,)
    ).fetchone()
    conn.close()
    if not usuario:
        return "QR no encontrado", 404
    return render_template("qr.html", usuario=usuario)


# ---------------------------------------------------------------------------
# Vista del GUARDIA (escaneo) - ruta separada, no se enlaza desde el inicio
# del visitante para no mezclar los dos roles.
# ---------------------------------------------------------------------------
@app.route("/guardia")
def escaneo():
    return render_template("escaneo.html")


@app.route("/guardia/bitacora")
def bitacora():
    conn = get_db()
    movimientos = conn.execute("""
        SELECT b.tipo, b.fecha_hora, u.nombre, u.marca_equipo, u.color_equipo
        FROM bitacora b
        JOIN usuarios u ON u.id = b.usuario_id
        ORDER BY b.id DESC
    """).fetchall()
    conn.close()
    return render_template("bitacora.html", movimientos=movimientos)


# ---------------------------------------------------------------------------
# API (usada por el JavaScript del escaner del guardia)
# ---------------------------------------------------------------------------
@app.route("/api/escanear", methods=["POST"])
def api_escanear():
    data = request.get_json(silent=True) or {}
    qr_uuid = data.get("uuid", "").strip()

    if not qr_uuid:
        return jsonify({"ok": False, "mensaje": "No se recibio ningun codigo."}), 400

    conn = get_db()
    usuario = conn.execute(
        "SELECT * FROM usuarios WHERE qr_uuid = ?", (qr_uuid,)
    ).fetchone()

    if not usuario:
        conn.close()
        return jsonify({"ok": False, "mensaje": "Codigo QR no reconocido."}), 404

    ultimo = ultimo_movimiento(conn, usuario["id"])
    nuevo_tipo = "salida" if ultimo == "entrada" else "entrada"
    ahora = datetime.now()

    conn.execute(
        "INSERT INTO bitacora (usuario_id, tipo, fecha_hora) VALUES (?, ?, ?)",
        (usuario["id"], nuevo_tipo, ahora.isoformat()),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "tipo": nuevo_tipo,
        "nombre": usuario["nombre"],
        "equipo": f"{usuario['marca_equipo']} {usuario['color_equipo']}",
        "hora": ahora.strftime("%H:%M:%S"),
    })


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    # debug=False para la demo: evita mostrar el traceback completo del
    # servidor si algo falla durante la sustentacion.
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode)
