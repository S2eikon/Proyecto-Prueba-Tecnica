import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Flask, jsonify, request

from config import Config
from db import get_connection


app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pagos")


ESTADOS_PAGO = {"pendiente", "autorizado", "rechazado"}
ESTADOS_WEBHOOK = {"autorizado", "rechazado"}


# ============================================================================
# UTILIDADES
# ============================================================================

def error_response(message, status_code):
    return jsonify({"error": message}), status_code


def get_json_body():
    if not request.is_json:
        return None

    try:
        return request.get_json()
    except Exception:
        return None


def parse_monto(value):
    """
    Convierte el monto a Decimal y valida que sea positivo
    y que tenga como máximo dos decimales.
    """
    try:
        monto = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None

    if not monto.is_finite():
        return None

    if monto <= Decimal("0"):
        return None

    if monto.as_tuple().exponent < -2:
        return None

    return monto.quantize(Decimal("0.01"))


def actualizar_estado_poliza(cur, poliza_id):
    """
    Actualiza el estado de la póliza utilizando exclusivamente
    pagos autorizados.

    La póliza se bloquea durante la transacción para evitar condiciones
    de carrera cuando existen múltiples réplicas.
    """

    cur.execute(
        """
        SELECT prima_total, estado
        FROM polizas
        WHERE id = %s
        FOR UPDATE
        """,
        (poliza_id,),
    )

    poliza = cur.fetchone()

    if poliza is None:
        return None

    prima_total = poliza[0]
    estado_actual = poliza[1]

    # Una póliza cancelada conserva su estado.
    if estado_actual == "cancelada":
        return {
            "estado": "cancelada",
            "saldo": prima_total,
        }

    cur.execute(
        """
        SELECT COALESCE(SUM(monto), 0)
        FROM pagos
        WHERE poliza_id = %s
          AND estado = 'autorizado'
        """,
        (poliza_id,),
    )

    total_autorizado = cur.fetchone()[0] or Decimal("0")

    saldo = prima_total - total_autorizado

    if saldo <= Decimal("0.00"):
        nuevo_estado = "al_dia"
    else:
        nuevo_estado = "en_mora"

    cur.execute(
        """
        UPDATE polizas
        SET estado = %s
        WHERE id = %s
        """,
        (nuevo_estado, poliza_id),
    )

    return {
        "estado": nuevo_estado,
        "saldo": saldo,
    }


# ============================================================================
# CREAR PAGO
# ============================================================================

@app.route("/polizas/<int:poliza_id>/pagos", methods=["POST"])
def registrar_pago(poliza_id):
    """
    Registra un pago de forma idempotente.

    Requiere:

        Idempotency-Key: <clave-unica>
    """

    idempotency_key = request.headers.get("Idempotency-Key")

    if not idempotency_key:
        return error_response(
            "El header Idempotency-Key es obligatorio",
            400,
        )

    idempotency_key = idempotency_key.strip()

    if not idempotency_key:
        return error_response(
            "El header Idempotency-Key no puede estar vacío",
            400,
        )

    if len(idempotency_key) > 255:
        return error_response(
            "El Idempotency-Key no puede superar 255 caracteres",
            400,
        )

    data = get_json_body()

    if data is None:
        return error_response(
            "El cuerpo debe ser un JSON válido",
            400,
        )

    if not isinstance(data, dict):
        return error_response(
            "El cuerpo debe ser un objeto JSON",
            400,
        )

    if "monto" not in data:
        return error_response(
            "El campo monto es obligatorio",
            400,
        )

    if "referencia" not in data:
        return error_response(
            "El campo referencia es obligatorio",
            400,
        )

    monto = parse_monto(data["monto"])

    if monto is None:
        return error_response(
            "El monto debe ser un número positivo con máximo dos decimales",
            400,
        )

    referencia = data["referencia"]

    if not isinstance(referencia, str):
        return error_response(
            "La referencia debe ser texto",
            400,
        )

    referencia = referencia.strip()

    if not referencia:
        return error_response(
            "La referencia no puede estar vacía",
            400,
        )

    if len(referencia) > 50:
        return error_response(
            "La referencia no puede superar 50 caracteres",
            400,
        )

    conn = None

    try:
        conn = get_connection()

        with conn:
            with conn.cursor() as cur:

                # ------------------------------------------------------------
                # Verificar si la Idempotency-Key ya fue utilizada
                # ------------------------------------------------------------

                cur.execute(
                    """
                    SELECT id, poliza_id, monto, referencia, estado
                    FROM pagos
                    WHERE idempotency_key = %s
                    FOR UPDATE
                    """,
                    (idempotency_key,),
                )

                pago_existente = cur.fetchone()

                if pago_existente is not None:
                    (
                        pago_id,
                        pago_poliza_id,
                        pago_monto,
                        pago_referencia,
                        pago_estado,
                    ) = pago_existente

                    # Una misma llave no debe utilizarse para otra operación.
                    if (
                        pago_poliza_id != poliza_id
                        or pago_monto != monto
                        or pago_referencia != referencia
                    ):
                        return error_response(
                            "El Idempotency-Key ya fue utilizada "
                            "con datos diferentes",
                            409,
                        )

                    return jsonify(
                        {
                            "pago_id": pago_id,
                            "estado": pago_estado,
                        }
                    ), 200

                # ------------------------------------------------------------
                # Verificar póliza
                # ------------------------------------------------------------

                cur.execute(
                    """
                    SELECT
                        id,
                        prima_total,
                        estado,
                        fecha_vencimiento
                    FROM polizas
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (poliza_id,),
                )

                poliza = cur.fetchone()

                if poliza is None:
                    return error_response(
                        "poliza no encontrada",
                        404,
                    )

                _, prima_total, estado_poliza, fecha_vencimiento = poliza

                # ------------------------------------------------------------
                # Reglas de negocio
                # ------------------------------------------------------------

                if estado_poliza == "cancelada":
                    return error_response(
                        "No se puede registrar un pago sobre una póliza cancelada",
                        409,
                    )

                fecha_actual = datetime.now(timezone.utc).date()

                if fecha_actual > fecha_vencimiento:
                    return error_response(
                        "No se puede registrar un pago sobre una póliza vencida",
                        409,
                    )

                # ------------------------------------------------------------
                # Registrar pago
                # ------------------------------------------------------------

                try:
                    cur.execute(
                        """
                        INSERT INTO pagos (
                            poliza_id,
                            monto,
                            referencia,
                            estado,
                            idempotency_key,
                            fecha
                        )
                        VALUES (%s, %s, %s, 'pendiente', %s, %s)
                        RETURNING id
                        """,
                        (
                            poliza_id,
                            monto,
                            referencia,
                            idempotency_key,
                            datetime.now(timezone.utc),
                        ),
                    )

                except Exception as exc:
                    # La restricción UNIQUE de la BD es la última garantía
                    # contra solicitudes concurrentes.
                    conn.rollback()

                    log.warning(
                        "Error al registrar pago: %s",
                        exc,
                    )

                    return error_response(
                        "No fue posible registrar el pago",
                        500,
                    )

                pago_id = cur.fetchone()[0]

                return jsonify(
                    {
                        "pago_id": pago_id,
                        "estado": "pendiente",
                    }
                ), 201

    except Exception as exc:
        log.exception(
            "Error registrando pago para póliza %s: %s",
            poliza_id,
            exc,
        )

        return error_response(
            "Error interno del servidor",
            500,
        )

    finally:
        if conn is not None:
            conn.close()


# ============================================================================
# WEBHOOK DE LA PASARELA
# ============================================================================

@app.route("/webhooks/pasarela", methods=["POST"])
def webhook():
    """
    Procesa webhooks de la pasarela de forma idempotente.

    La combinación de:

        webhook_eventos.event_id UNIQUE

    y una única transacción evita procesar dos veces el mismo evento.
    """

    payload = get_json_body()

    if payload is None:
        return error_response(
            "El cuerpo debe ser un JSON válido",
            400,
        )

    if not isinstance(payload, dict):
        return error_response(
            "El cuerpo debe ser un objeto JSON",
            400,
        )

    event_id = payload.get("event_id")
    pago_id = payload.get("pago_id")
    nuevo_estado = payload.get("estado")

    if not event_id:
        return error_response(
            "event_id es obligatorio",
            400,
        )

    if not isinstance(event_id, str):
        return error_response(
            "event_id debe ser texto",
            400,
        )

    event_id = event_id.strip()

    if not event_id:
        return error_response(
            "event_id no puede estar vacío",
            400,
        )

    if len(event_id) > 255:
        return error_response(
            "event_id no puede superar 255 caracteres",
            400,
        )

    if not isinstance(pago_id, int) or pago_id <= 0:
        return error_response(
            "pago_id debe ser un entero positivo",
            400,
        )

    if nuevo_estado not in ESTADOS_WEBHOOK:
        return error_response(
            "estado debe ser autorizado o rechazado",
            400,
        )

    log.info(
        "Webhook recibido: event_id=%s pago_id=%s estado=%s",
        event_id,
        pago_id,
        nuevo_estado,
    )

    conn = None

    try:
        conn = get_connection()

        with conn:
            with conn.cursor() as cur:

                # ------------------------------------------------------------
                # Verificar si el evento ya fue procesado
                # ------------------------------------------------------------

                cur.execute(
                    """
                    SELECT id
                    FROM webhook_eventos
                    WHERE event_id = %s
                    """,
                    (event_id,),
                )

                evento_existente = cur.fetchone()

                if evento_existente is not None:
                    return jsonify(
                        {
                            "ok": True,
                            "duplicate": True,
                        }
                    ), 200

                # ------------------------------------------------------------
                # Verificar pago
                # ------------------------------------------------------------

                cur.execute(
                    """
                    SELECT id, poliza_id, estado
                    FROM pagos
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (pago_id,),
                )

                pago = cur.fetchone()

                if pago is None:
                    return error_response(
                        "pago no encontrado",
                        404,
                    )

                _, poliza_id, estado_actual = pago

                # ------------------------------------------------------------
                # Registrar evento
                # ------------------------------------------------------------

                cur.execute(
                    """
                    INSERT INTO webhook_eventos (
                        event_id,
                        pago_id,
                        estado,
                        recibido_at
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        event_id,
                        pago_id,
                        nuevo_estado,
                        datetime.now(timezone.utc),
                    ),
                )

                # ------------------------------------------------------------
                # Actualizar pago
                # ------------------------------------------------------------

                cur.execute(
                    """
                    UPDATE pagos
                    SET estado = %s
                    WHERE id = %s
                    """,
                    (
                        nuevo_estado,
                        pago_id,
                    ),
                )

                # ------------------------------------------------------------
                # Actualizar estado de la póliza
                # ------------------------------------------------------------

                resultado = actualizar_estado_poliza(
                    cur,
                    poliza_id,
                )

                if resultado is None:
                    return error_response(
                        "poliza no encontrada",
                        404,
                    )

                return jsonify(
                    {
                        "ok": True,
                        "pago_id": pago_id,
                        "estado": nuevo_estado,
                    }
                ), 200

    except Exception as exc:
        log.exception(
            "Error procesando webhook: %s",
            exc,
        )

        return error_response(
            "Error procesando webhook",
            500,
        )

    finally:
        if conn is not None:
            conn.close()


# ============================================================================
# SALDO DE PÓLIZA
# ============================================================================

@app.route("/polizas/<int:poliza_id>/saldo", methods=["GET"])
def saldo(poliza_id):
    """
    Devuelve el saldo calculado a partir de los pagos autorizados.
    """

    conn = None

    try:
        conn = get_connection()

        with conn:
            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        p.prima_total,
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN pa.estado = 'autorizado'
                                    THEN pa.monto
                                    ELSE 0
                                END
                            ),
                            0
                        ) AS total_autorizado
                    FROM polizas p
                    LEFT JOIN pagos pa
                        ON pa.poliza_id = p.id
                    WHERE p.id = %s
                    GROUP BY p.id, p.prima_total
                    """,
                    (poliza_id,),
                )

                row = cur.fetchone()

                if row is None:
                    return error_response(
                        "poliza no encontrada",
                        404,
                    )

                prima_total = row[0]
                total_autorizado = row[1]

                saldo_actual = prima_total - total_autorizado

                if saldo_actual < Decimal("0.00"):
                    saldo_actual = Decimal("0.00")

                return jsonify(
                    {
                        "prima_total": str(prima_total),
                        "saldo": str(saldo_actual),
                    }
                ), 200

    except Exception as exc:
        log.exception(
            "Error consultando saldo de póliza %s: %s",
            poliza_id,
            exc,
        )

        return error_response(
            "Error interno del servidor",
            500,
        )

    finally:
        if conn is not None:
            conn.close()


# ============================================================================
# RESUMEN DEL CLIENTE
# ============================================================================

@app.route("/clientes/<int:cliente_id>/resumen", methods=["GET"])
def resumen_cliente(cliente_id):
    """
    Obtiene el resumen del cliente y sus pólizas utilizando una sola
    consulta agregada, evitando el problema N+1 del módulo legacy.
    """

    conn = None

    try:
        conn = get_connection()

        with conn:
            with conn.cursor() as cur:

                # ------------------------------------------------------------
                # Verificar cliente
                # ------------------------------------------------------------

                cur.execute(
                    """
                    SELECT nombre, documento, email
                    FROM clientes
                    WHERE id = %s
                    """,
                    (cliente_id,),
                )

                cliente = cur.fetchone()

                if cliente is None:
                    return error_response(
                        "cliente no encontrado",
                        404,
                    )

                # ------------------------------------------------------------
                # Obtener pólizas y pagos autorizados en una sola consulta
                # ------------------------------------------------------------

                cur.execute(
                    """
                    SELECT
                        p.id,
                        p.prima_total,
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN pa.estado = 'autorizado'
                                    THEN pa.monto
                                    ELSE 0
                                END
                            ),
                            0
                        ) AS pagado
                    FROM polizas p
                    LEFT JOIN pagos pa
                        ON pa.poliza_id = p.id
                    WHERE p.cliente_id = %s
                    GROUP BY p.id, p.prima_total
                    ORDER BY p.id
                    """,
                    (cliente_id,),
                )

                filas = cur.fetchall()

                resultado = []

                for fila in filas:
                    poliza_id = fila[0]
                    prima_total = fila[1]
                    pagado = fila[2]

                    saldo_actual = prima_total - pagado

                    if saldo_actual < Decimal("0.00"):
                        saldo_actual = Decimal("0.00")

                    resultado.append(
                        {
                            "poliza_id": poliza_id,
                            "prima_total": str(prima_total),
                            "pagado": str(pagado),
                            "saldo": str(saldo_actual),
                        }
                    )

                return jsonify(
                    {
                        "cliente": {
                            "nombre": cliente[0],
                            "documento": cliente[1],
                            "email": cliente[2],
                        },
                        "polizas": resultado,
                    }
                ), 200

    except Exception as exc:
        log.exception(
            "Error consultando resumen del cliente %s: %s",
            cliente_id,
            exc,
        )

        return error_response(
            "Error interno del servidor",
            500,
        )

    finally:
        if conn is not None:
            conn.close()


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route("/health", methods=["GET"])
def health():
    """
    Endpoint sencillo para verificar que la aplicación está funcionando.
    """

    return jsonify(
        {
            "status": "ok",
        }
    ), 200


# ============================================================================
# MANEJO GLOBAL DE ERRORES
# ============================================================================

@app.errorhandler(404)
def not_found(_error):
    return error_response(
        "recurso no encontrado",
        404,
    )


@app.errorhandler(405)
def method_not_allowed(_error):
    return error_response(
        "método HTTP no permitido",
        405,
    )


# ============================================================================
# EJECUCIÓN
# ============================================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=Config.APP_PORT,
        debug=(Config.APP_ENV == "development"),
    )