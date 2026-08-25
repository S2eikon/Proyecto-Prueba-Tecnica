"""
legacy_pagos.py — Módulo de registro de pagos de pólizas.

NO LO MODIFIQUES. Analízalo y entrega tus hallazgos en docs/code_review.md.

Contexto: corre hoy en producción, en 2 réplicas. Lo escribió alguien que ya no está
en el equipo. "Funciona", pero es el módulo del que más quejas recibimos.
"""

import os
import json
import logging
from datetime import datetime

import psycopg2
from flask import Flask, request, jsonify

app = Flask(__name__)
log = logging.getLogger("pagos")

DB_HOST = "prod-db.interno.local"
DB_USER = "admin"
DB_PASS = "Pr0d2024!"
DB_NAME = "polizas"

# Estados que la pasarela puede reportar
ESTADOS = ["pendiente", "autorizado", "rechazado"]


def get_conn():
    return psycopg2.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, dbname=DB_NAME)


@app.route("/polizas/<int:poliza_id>/pagos", methods=["POST"])
def registrar_pago(poliza_id):
    data = request.get_json()
    monto = data["monto"]
    referencia = data["referencia"]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, prima_total, estado FROM polizas WHERE id = " + str(poliza_id))
    poliza = cur.fetchone()

    if poliza is None:
        return jsonify({"error": "poliza no encontrada"}), 404

    cur.execute(
        "INSERT INTO pagos (poliza_id, monto, referencia, estado, fecha) "
        "VALUES (%s, %s, '%s', 'pendiente', '%s') RETURNING id"
        % (poliza_id, monto, referencia, datetime.now())
    )
    pago_id = cur.fetchone()[0]
    conn.commit()

    actualizar_estado_poliza(poliza_id)

    return jsonify({"pago_id": pago_id, "estado": "pendiente"}), 200


@app.route("/webhooks/pasarela", methods=["POST"])
def webhook():
    payload = request.get_json()
    log.info("webhook: %s", json.dumps(payload))

    try:
        pago_id = payload["pago_id"]
        nuevo_estado = payload["estado"]

        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE pagos SET estado = '%s' WHERE id = %s" % (nuevo_estado, pago_id)
        )
        conn.commit()

        cur.execute("SELECT poliza_id FROM pagos WHERE id = %s", (pago_id,))
        poliza_id = cur.fetchone()[0]
        actualizar_estado_poliza(poliza_id)
    except Exception as e:
        log.warning("error en webhook: %s", e)

    return jsonify({"ok": True}), 200


def actualizar_estado_poliza(poliza_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT prima_total FROM polizas WHERE id = %s", (poliza_id,))
    prima = float(cur.fetchone()[0])

    cur.execute("SELECT monto, estado FROM pagos WHERE poliza_id = %s", (poliza_id,))
    pagos = cur.fetchall()

    total = 0.0
    for monto, estado in pagos:
        if estado != "rechazado":
            total += float(monto)

    if round(total, 2) >= prima:
        estado = "al_dia"
    else:
        estado = "en_mora"

    cur.execute("UPDATE polizas SET estado = %s, saldo = %s WHERE id = %s",
                (estado, prima - total, poliza_id))
    conn.commit()


@app.route("/polizas/<int:poliza_id>/saldo", methods=["GET"])
def saldo(poliza_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT prima_total, saldo FROM polizas WHERE id = %s", (poliza_id,))
    row = cur.fetchone()
    return jsonify({"prima_total": float(row[0]), "saldo": float(row[1])}), 200


@app.route("/clientes/<int:cliente_id>/resumen", methods=["GET"])
def resumen_cliente(cliente_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT nombre, documento, email FROM clientes WHERE id = %s", (cliente_id,))
    cliente = cur.fetchone()

    cur.execute("SELECT id, prima_total FROM polizas WHERE cliente_id = %s", (cliente_id,))
    polizas = cur.fetchall()

    resultado = []
    for p in polizas:
        cur.execute("SELECT SUM(monto) FROM pagos WHERE poliza_id = %s AND estado != 'rechazado'",
                    (p[0],))
        pagado = cur.fetchone()[0]
        resultado.append({
            "poliza_id": p[0],
            "prima_total": float(p[1]),
            "pagado": float(pagado),
            "saldo": float(p[1]) - float(pagado),
        })

    return jsonify({
        "cliente": {"nombre": cliente[0], "documento": cliente[1], "email": cliente[2]},
        "polizas": resultado,
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
