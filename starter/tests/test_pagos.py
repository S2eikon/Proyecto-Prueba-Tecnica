import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from app import app
from db import get_connection


@pytest.fixture
def client():
    app.config["TESTING"] = True

    with app.test_client() as client:
        yield client


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_pago_sin_idempotency_key(client):
    response = client.post(
        "/polizas/1/pagos",
        json={
            "monto": 50000.00,
            "referencia": "REF-SIN-KEY",
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "El header Idempotency-Key es obligatorio"
    }


def test_pago_con_monto_negativo(client):
    response = client.post(
        "/polizas/1/pagos",
        headers={
            "Idempotency-Key": "test-monto-negativo",
        },
        json={
            "monto": -50000.00,
            "referencia": "REF-MONTO-NEGATIVO",
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "El monto debe ser un número positivo con máximo dos decimales"
    }


def test_pago_con_mas_de_dos_decimales(client):
    response = client.post(
        "/polizas/1/pagos",
        headers={
            "Idempotency-Key": "test-mas-decimales",
        },
        json={
            "monto": 50000.123,
            "referencia": "REF-MAS-DECIMALES",
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "El monto debe ser un número positivo con máximo dos decimales"
    }


def test_webhook_sin_event_id(client):
    response = client.post(
        "/webhooks/pasarela",
        json={
            "pago_id": 2,
            "estado": "autorizado",
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "event_id es obligatorio"
    }


def test_webhook_estado_invalido(client):
    response = client.post(
        "/webhooks/pasarela",
        json={
            "event_id": "test-evento-invalido",
            "pago_id": 2,
            "estado": "pendiente",
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "estado debe ser autorizado o rechazado"
    }


def test_webhook_pago_inexistente(client):
    response = client.post(
        "/webhooks/pasarela",
        json={
            "event_id": "test-pago-inexistente",
            "pago_id": 999999,
            "estado": "autorizado",
        },
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "pago no encontrado"
    }


def test_poliza_inexistente(client):
    response = client.post(
        "/polizas/999999/pagos",
        headers={
            "Idempotency-Key": "test-poliza-inexistente",
        },
        json={
            "monto": 50000.00,
            "referencia": "REF-NO-EXISTE",
        },
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "poliza no encontrada"
    }


def test_pago_idempotente(client):
    """
    Verifica que dos requests con la misma Idempotency-Key
    y el mismo cuerpo crean un solo pago en PostgreSQL.
    """

    idempotency_key = "test-idempotencia-real-001"

    payload = {
        "monto": 1000.00,
        "referencia": "REF-IDEMPOTENCIA-001",
    }

    try:
        # Primera solicitud
        response_1 = client.post(
            "/polizas/1/pagos",
            headers={
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response_1.status_code == 201

        pago_1 = response_1.get_json()

        assert "pago_id" in pago_1
        assert pago_1["estado"] == "pendiente"

        # Segunda solicitud con la misma llave y los mismos datos
        response_2 = client.post(
            "/polizas/1/pagos",
            headers={
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )

        assert response_2.status_code == 200

        pago_2 = response_2.get_json()

        assert pago_2["pago_id"] == pago_1["pago_id"]
        assert pago_2["estado"] == "pendiente"

        # Verificar directamente en PostgreSQL que existe un solo pago
        conn = get_connection()

        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM pagos
                        WHERE idempotency_key = %s
                        """,
                        (idempotency_key,),
                    )

                    cantidad = cur.fetchone()[0]

                    assert cantidad == 1

        finally:
            conn.close()

    finally:
        # Limpiar el pago creado por la prueba
        conn = get_connection()

        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        DELETE FROM pagos
                        WHERE idempotency_key = %s
                        """,
                        (idempotency_key,),
                    )
        finally:
            conn.close()