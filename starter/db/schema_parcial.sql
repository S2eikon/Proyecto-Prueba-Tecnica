-- db/schema_parcial.sql
-- Este es el esquema que corre hoy en producción, tal cual.
-- Volumen actual: clientes 60 K, polizas 140 K, pagos 900 K.
--
-- Entrega tu versión en db/schema.sql y responde los TODO del final.

CREATE TABLE clientes (
    id          SERIAL PRIMARY KEY,
    nombre      VARCHAR(100) NOT NULL,
    documento   VARCHAR(20),
    email       VARCHAR(100),
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE polizas (
    id                SERIAL PRIMARY KEY,
    cliente_id        INTEGER REFERENCES clientes(id),
    prima_total       NUMERIC(10, 2) NOT NULL,
    saldo             DOUBLE PRECISION,        -- lo actualiza la aplicación
    estado            VARCHAR(20) DEFAULT 'en_mora',
    fecha_emision     DATE NOT NULL,
    fecha_vencimiento DATE NOT NULL
);

CREATE TABLE pagos (
    id          SERIAL PRIMARY KEY,
    poliza_id   INTEGER REFERENCES polizas(id),
    monto       DOUBLE PRECISION NOT NULL,
    referencia  VARCHAR(50),
    estado      VARCHAR(20) DEFAULT 'pendiente',
    fecha       TIMESTAMP DEFAULT NOW()
);

-- Los eventos que manda la pasarela no se guardan en ninguna parte:
-- se procesan y se descartan.

CREATE INDEX idx_pagos_estado ON pagos(estado);


-- ===========================================================================
-- TODO (candidato)
-- ===========================================================================
--
-- 1. `pagos.monto` y `polizas.saldo` son DOUBLE PRECISION. Explica qué se rompe
--    concretamente frente a RN-4 y cuál es el tipo correcto.
--
-- 2. RN-1 pide idempotencia en el registro de pagos. ¿Cómo lo soportas desde el
--    esquema? ¿Quién garantiza que no haya duplicados: la aplicación o la base de
--    datos? Justifica tu respuesta.
--
-- 3. RN-2 dice que el mismo webhook puede llegar varias veces. Modela lo que haga
--    falta para que procesarlo dos veces no cambie nada.
--
-- 4. Faltan restricciones: UNIQUE, CHECK, NOT NULL y FK con ON DELETE.
--    Agrégalas y explica en un comentario por qué cada una.
--
-- 5. Índices: ¿el que existe sirve de algo? ¿Cuáles faltan? Ten en cuenta que en
--    PostgreSQL las llaves foráneas NO se indexan solas, y que el 90 % de los
--    pagos están en estado 'autorizado'.
--
-- 6. RN-5 habla de zona horaria. ¿Los TIMESTAMP de este esquema te sirven tal como
--    están? Si no, cámbialos y explica la diferencia.
--
-- 7. `polizas.saldo` es un dato derivado que la aplicación mantiene a mano.
--    ¿Lo conservas o lo calculas al vuelo? Toma una decisión y di qué ganas y qué
--    pierdes con ella.
