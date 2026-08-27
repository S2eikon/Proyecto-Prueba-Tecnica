-- db/schema.sql
-- Esquema propuesto para la nueva implementación.
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

-- ============================================================================
-- CLIENTES
-- ============================================================================

CREATE TABLE clientes (
    id          SERIAL PRIMARY KEY,
    nombre      VARCHAR(100) NOT NULL,
    documento   VARCHAR(20)NOT NULL,
    email       VARCHAR(100),
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_clientes_documento UNIQUE (documento)
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

    -- NUMERIC(12,2) permite representar exactamente los pesos y centavos.
    monto            NUMERIC(12, 2) NOT NULL,

    referencia       VARCHAR(50),

    estado           VARCHAR(20) NOT NULL DEFAULT 'pendiente',

    -- Idempotency-Key enviada por el cliente al crear el pago.
    --
    -- Se persiste en la base de datos porque la aplicación corre en
    -- múltiples réplicas. La restricción UNIQUE garantiza que dos
    -- solicitudes concurrentes con la misma llave no creen dos pagos.
    idempotency_key  VARCHAR(255) NOT NULL,

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

-- Los webhooks pueden llegar varias veces.
-- Guardamos cada event_id para poder garantizar idempotencia.
--El event_id es la clave de idempotencia del webhook

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

-- Las FK en PostgreSQL no crean automáticamente índices.
-- Este índice permite buscar rápidamente las pólizas de un cliente.

CREATE INDEX idx_polizas_cliente_id
    ON polizas(cliente_id);

-- Permite localizar rápidamente los pagos pertenecientes a una póliza,
-- especialmente para calcular el total autorizado y el saldo.

CREATE INDEX idx_pagos_poliza_id
    ON pagos(poliza_id);

-- Este índice compuesto facilita las consultas que buscan únicamente
-- los pagos autorizados de una póliza.
--
-- El índice original sobre estado no es tan útil por sí solo porque
-- aproximadamente el 90 % de los pagos están en estado 'autorizado'.
-- Un índice que separa por poliza_id y estado es más útil para nuestras
-- consultas principales.

CREATE INDEX idx_pagos_poliza_estado
    ON pagos(poliza_id, estado);

-- Permite localizar rápidamente los eventos asociados a un pago.

CREATE INDEX idx_webhook_eventos_pago_id
    ON webhook_eventos(pago_id);

-- ============================================================================
-- DECISIONES DEL MODELO
-- ============================================================================

-- 1. Dinero:
--    Se utiliza NUMERIC(12,2) en lugar de DOUBLE PRECISION porque los
--    cálculos monetarios deben ser exactos al centavo (RN-4).
--
-- 2. Idempotencia:
--    La Idempotency-Key se almacena en pagos y tiene UNIQUE.
--    La base de datos es la última garantía contra duplicados, incluso
--    cuando existen varias réplicas de la aplicación (RN-1).
--
-- 3. Webhooks:
--    event_id se almacena en webhook_eventos y tiene UNIQUE.
--    De esta forma el mismo evento no puede registrarse dos veces (RN-2).
--
-- 4. Integridad:
--    Las FK utilizan ON DELETE RESTRICT para impedir eliminar clientes,
--    pólizas o pagos que todavía tienen información relacionada.
--
-- 5. Zona horaria:
--    Los timestamps utilizan TIMESTAMP WITH TIME ZONE.
--    PostgreSQL almacena el instante correctamente y la aplicación puede
--    convertirlo a America/Bogota al mostrarlo al usuario (RN-5).
--
-- 6. Saldo:
--    No se almacena polizas.saldo.
--    Es un dato derivado que puede calcularse como:
--
--        saldo = prima_total - SUM(pagos autorizados)
--
--    De esta manera evitamos que el saldo almacenado quede desactualizado
--    respecto a los pagos reales.
--
-- 7. Estado de póliza:
--    El estado se mantiene porque forma parte del estado de negocio de la
--    póliza, pero su valor debe actualizarse basándose exclusivamente en
--    los pagos autorizados.