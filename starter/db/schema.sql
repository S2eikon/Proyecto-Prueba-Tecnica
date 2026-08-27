-- db/schema.sql
-- Esquema definitivo para la nueva implementación.
-- PostgreSQL 16
--
-- El objetivo es garantizar:
-- - precisión monetaria
-- - idempotencia de pagos
-- - idempotencia de webhooks
-- - integridad referencial
-- - validación de estados y montos
-- - manejo correcto de fechas con zona horaria
-- - consultas eficientes
--
-- Compatible con starter/app/app.py


-- ============================================================================
-- CLIENTES
-- ============================================================================

CREATE TABLE clientes (
    id          SERIAL PRIMARY KEY,
    nombre      VARCHAR(100) NOT NULL,
    documento   VARCHAR(20) NOT NULL,
    email       VARCHAR(100),
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_clientes_documento
        UNIQUE (documento)
);


-- ============================================================================
-- POLIZAS
-- ============================================================================

CREATE TABLE polizas (
    id                SERIAL PRIMARY KEY,

    cliente_id        INTEGER NOT NULL,

    -- Los valores monetarios se almacenan con precisión exacta.
    -- NUMERIC evita los errores de representación de DOUBLE PRECISION.
    prima_total       NUMERIC(12, 2) NOT NULL,

    estado            VARCHAR(20) NOT NULL DEFAULT 'en_mora',

    fecha_emision     DATE NOT NULL,
    fecha_vencimiento DATE NOT NULL,

    CONSTRAINT fk_polizas_cliente
        FOREIGN KEY (cliente_id)
        REFERENCES clientes(id)
        ON DELETE RESTRICT,

    CONSTRAINT ck_polizas_prima_total
        CHECK (prima_total > 0),

    CONSTRAINT ck_polizas_estado
        CHECK (estado IN ('al_dia', 'en_mora', 'cancelada')),

    CONSTRAINT ck_polizas_fechas
        CHECK (fecha_vencimiento >= fecha_emision)
);


-- ============================================================================
-- PAGOS
-- ============================================================================

CREATE TABLE pagos (
    id               SERIAL PRIMARY KEY,

    poliza_id        INTEGER NOT NULL,

    -- NUMERIC(12,2) permite representar importes monetarios
    -- con precisión exacta hasta dos decimales.
    monto            NUMERIC(12, 2) NOT NULL,

    referencia       VARCHAR(50),

    estado           VARCHAR(20) NOT NULL DEFAULT 'pendiente',

    -- Llave enviada por el cliente para garantizar idempotencia.
    --
    -- La restricción UNIQUE es la garantía definitiva contra pagos
    -- duplicados cuando existen solicitudes concurrentes o múltiples
    -- réplicas de la aplicación.
    idempotency_key  VARCHAR(255) NOT NULL,

    -- TIMESTAMP WITH TIME ZONE permite representar correctamente
    -- el instante en que se registra el pago.
    fecha            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_pagos_poliza
        FOREIGN KEY (poliza_id)
        REFERENCES polizas(id)
        ON DELETE RESTRICT,

    CONSTRAINT ck_pagos_monto
        CHECK (monto > 0),

    CONSTRAINT ck_pagos_estado
        CHECK (estado IN ('pendiente', 'autorizado', 'rechazado')),

    CONSTRAINT uq_pagos_idempotency_key
        UNIQUE (idempotency_key)
);


-- ============================================================================
-- EVENTOS DE LA PASARELA
-- ============================================================================

-- La pasarela puede enviar varias veces el mismo webhook.
--
-- Guardamos cada event_id y lo hacemos UNIQUE para garantizar que
-- un mismo evento no pueda registrarse dos veces.
--
-- Esto permite utilizar:
--
--     ON CONFLICT (event_id) DO NOTHING
--
-- desde la aplicación.

CREATE TABLE webhook_eventos (
    id              SERIAL PRIMARY KEY,

    event_id        VARCHAR(255) NOT NULL,

    pago_id         INTEGER NOT NULL,

    estado          VARCHAR(20) NOT NULL,

    recibido_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_webhook_event_id
        UNIQUE (event_id),

    CONSTRAINT fk_webhook_pago
        FOREIGN KEY (pago_id)
        REFERENCES pagos(id)
        ON DELETE RESTRICT,

    CONSTRAINT ck_webhook_estado
        CHECK (estado IN ('autorizado', 'rechazado'))
);


-- ============================================================================
-- INDICES
-- ============================================================================

-- PostgreSQL NO crea automáticamente índices para las columnas que
-- participan como claves foráneas.
--
-- Este índice permite localizar rápidamente las pólizas de un cliente,
-- utilizado por el endpoint:
--
--     /clientes/<cliente_id>/resumen

CREATE INDEX idx_polizas_cliente_id
    ON polizas(cliente_id);


-- Este índice permite localizar rápidamente los pagos pertenecientes
-- a una póliza.

CREATE INDEX idx_pagos_poliza_id
    ON pagos(poliza_id);


-- Índice compuesto para las consultas que buscan pagos de una póliza
-- filtrando además por estado.
--
-- Es especialmente útil para calcular:
--
--     SUM(monto)
--     WHERE poliza_id = ?
--       AND estado = 'autorizado'
--
-- El índice original solamente sobre estado no resulta tan eficiente
-- porque aproximadamente el 90 % de los pagos están autorizados.
--
-- Tener poliza_id como primera columna permite localizar primero los
-- pagos de una póliza y después filtrar por estado.

CREATE INDEX idx_pagos_poliza_estado
    ON pagos(poliza_id, estado);


-- Permite localizar rápidamente los eventos asociados a un pago.

CREATE INDEX idx_webhook_eventos_pago_id
    ON webhook_eventos(pago_id);


-- ============================================================================
-- DECISIONES DEL MODELO
-- ============================================================================

-- ============================================================================
-- 1. DINERO - RN-4
-- ============================================================================
--
-- El esquema original utilizaba DOUBLE PRECISION para:
--
--     pagos.monto
--     polizas.saldo
--
-- DOUBLE PRECISION utiliza representación binaria de punto flotante.
-- Por lo tanto, determinados valores decimales no pueden representarse
-- exactamente.
--
-- En operaciones monetarias esto puede producir pequeños errores de
-- precisión acumulados, especialmente al sumar múltiples pagos.
--
-- Se utiliza NUMERIC(12,2) porque permite almacenar valores monetarios
-- con precisión decimal exacta y hasta dos posiciones decimales.
--
-- Además, polizas.saldo fue eliminado porque es un dato derivado.
--
-- ============================================================================


-- ============================================================================
-- 2. IDEMPOTENCIA DE PAGOS - RN-1
-- ============================================================================
--
-- Cada pago requiere una Idempotency-Key proporcionada por el cliente.
--
-- La llave se almacena en:
--
--     pagos.idempotency_key
--
-- y tiene una restricción UNIQUE.
--
-- La aplicación comprueba primero si la llave ya existe, pero la garantía
-- definitiva pertenece a la base de datos.
--
-- Esto es importante porque dos solicitudes pueden llegar simultáneamente
-- y ambas podrían comprobar que la llave todavía no existe.
--
-- La restricción UNIQUE evita que ambas transacciones creen dos pagos.
--
-- La aplicación utiliza además:
--
--     ON CONFLICT (idempotency_key) DO NOTHING
--
-- para resolver correctamente la carrera de concurrencia.
--
-- ============================================================================


-- ============================================================================
-- 3. IDEMPOTENCIA DE WEBHOOKS - RN-2
-- ============================================================================
--
-- Los webhooks de la pasarela pueden llegar más de una vez.
--
-- Para solucionarlo se crea la tabla webhook_eventos.
--
-- Cada webhook se identifica mediante:
--
--     event_id
--
-- event_id tiene una restricción UNIQUE.
--
-- Por lo tanto, el mismo evento no puede almacenarse dos veces.
--
-- La aplicación utiliza:
--
--     ON CONFLICT (event_id) DO NOTHING
--
-- Si el evento ya fue procesado, la aplicación devuelve:
--
--     {"ok": true, "duplicate": true}
--
-- sin volver a modificar el pago ni la póliza.
--
-- ============================================================================


-- ============================================================================
-- 4. INTEGRIDAD REFERENCIAL Y RESTRICCIONES
-- ============================================================================
--
-- Se agregan NOT NULL donde el modelo necesita obligatoriamente el dato:
--
--     polizas.cliente_id
--     pagos.poliza_id
--     pagos.monto
--     pagos.idempotency_key
--     webhook_eventos.event_id
--     webhook_eventos.pago_id
--
-- Se utilizan CHECK para impedir estados inválidos:
--
--     polizas:
--         al_dia
--         en_mora
--         cancelada
--
--     pagos:
--         pendiente
--         autorizado
--         rechazado
--
--     webhook_eventos:
--         autorizado
--         rechazado
--
-- También se valida que:
--
--     prima_total > 0
--     monto > 0
--
-- Las claves foráneas utilizan ON DELETE RESTRICT.
--
-- Esto impide eliminar un cliente que todavía tenga pólizas,
-- una póliza que tenga pagos o un pago que tenga eventos registrados.
--
-- De esta manera se protege el historial financiero.
--
-- ============================================================================


-- ============================================================================
-- 5. INDICES
-- ============================================================================
--
-- Las claves foráneas de PostgreSQL NO crean automáticamente índices.
--
-- Por eso se crean índices para:
--
--     polizas(cliente_id)
--     pagos(poliza_id)
--     pagos(poliza_id, estado)
--     webhook_eventos(pago_id)
--
-- El índice únicamente sobre pagos.estado no resulta ideal porque
-- aproximadamente el 90 % de los pagos están en estado 'autorizado'.
--
-- El índice compuesto:
--
--     (poliza_id, estado)
--
-- permite primero localizar los pagos de una póliza y posteriormente
-- filtrar por estado.
--
-- ============================================================================


-- ============================================================================
-- 6. ZONA HORARIA - RN-5
-- ============================================================================
--
-- TIMESTAMP WITHOUT TIME ZONE no conserva información suficiente para
-- representar correctamente un instante global.
--
-- Se utiliza:
--
--     TIMESTAMP WITH TIME ZONE
--
-- PostgreSQL normaliza internamente el instante y permite mostrarlo
-- posteriormente en la zona horaria correspondiente.
--
-- La aplicación utiliza datetime.now(timezone.utc), por lo que el tipo
-- TIMESTAMP WITH TIME ZONE es el adecuado para:
--
--     pagos.fecha
--     webhook_eventos.recibido_at
--     clientes.created_at
--
-- Al presentar los datos al usuario se pueden convertir a la zona horaria
-- America/Bogota.
--
-- ============================================================================


-- ============================================================================
-- 7. SALDO DE LA POLIZA
-- ============================================================================
--
-- No se almacena:
--
--     polizas.saldo
--
-- porque es un dato derivado.
--
-- El saldo se obtiene mediante:
--
--     saldo = prima_total - SUM(pagos autorizados)
--
-- Esto evita que el saldo almacenado quede desactualizado cuando un pago
-- cambia de pendiente a autorizado o rechazado.
--
-- La aplicación recalcula el saldo utilizando exclusivamente los pagos
-- autorizados.
--
-- La principal ventaja es la consistencia entre los pagos reales y el
-- saldo calculado.
--
-- La desventaja es que el saldo requiere una consulta/agregación al
-- momento de obtenerlo. Los índices sobre pagos.poliza_id y
-- pagos(poliza_id, estado) ayudan a mantener este cálculo eficiente.
--
-- ============================================================================


-- ============================================================================
-- 8. ESTADO DE LA POLIZA
-- ============================================================================
--
-- El estado de la póliza sí se conserva porque representa un estado
-- de negocio:
--
--     al_dia
--     en_mora
--     cancelada
--
-- La aplicación recalcula el estado utilizando exclusivamente pagos
-- autorizados.
--
-- Una póliza cancelada conserva su estado y no permite registrar nuevos
-- pagos.
--
-- ============================================================================