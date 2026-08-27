# Decisiones técnicas

## Decisión 1 — Idempotencia en pagos y webhooks

**Qué elegí:** Persistir la `Idempotency-Key` de los pagos y el `event_id` de los
webhooks en PostgreSQL, utilizando restricciones `UNIQUE` para garantizar que
una misma operación no pueda registrarse o procesarse dos veces.

**Qué descarté y por qué:** Descarté guardar las claves procesadas solamente
en memoria de la aplicación. La solución corre en 2 réplicas, por lo que cada
instancia podría tener un estado diferente y permitir duplicados.

**Qué pierdo con esto:** Se almacenan datos adicionales y se necesitan
restricciones e índices en la base de datos, pero a cambio se obtiene una
garantía de idempotencia compartida entre todas las réplicas.

## Decisión 2 — Manejo de dinero con NUMERIC y Decimal

**Qué elegí:** Utilizar `NUMERIC(12,2)` en PostgreSQL y `Decimal` en Python para
todos los valores monetarios.

**Qué descarté y por qué:** Descarté `DOUBLE PRECISION` y `float`, porque son
tipos de punto flotante y pueden introducir errores de representación en las
operaciones monetarias. La prueba exige que los valores cuadren exactamente
al centavo.

**Qué pierdo con esto:** Las operaciones pueden ser ligeramente más costosas
que utilizando tipos de punto flotante, pero se obtiene precisión exacta para
los valores monetarios.

## Decisión 3 — Calcular el saldo a partir de los pagos autorizados

**Qué elegí:** Calcular el saldo pendiente utilizando la prima total y la suma
de los pagos cuyo estado sea `autorizado`, en lugar de mantener `saldo` como
un valor que la aplicación actualiza manualmente.

**Qué descarté y por qué:** Descarté mantener `polizas.saldo` como fuente de
verdad porque es un dato derivado y puede quedar desactualizado si una
operación falla, si se procesa un webhook repetido o si existen operaciones
concurrentes.

**Qué pierdo con esto:** El cálculo puede requerir consultas o agregaciones
adicionales sobre los pagos, especialmente cuando una póliza tiene muchos
registros. A cambio, el saldo se obtiene a partir de la información que
realmente determina el estado de la póliza.