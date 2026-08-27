# Prueba técnica — Ingeniero de Backend

Implementación de un módulo de pagos para una plataforma de seguros, utilizando **Python, Flask y PostgreSQL**.

El objetivo principal es resolver los problemas de duplicidad de pagos y procesamiento repetido de webhooks mediante mecanismos de idempotencia, además de garantizar consistencia de datos, validaciones y precisión monetaria.

---

## 1. Tecnologías utilizadas

- Python 3.12
- Flask
- PostgreSQL 16
- psycopg2
- pytest
- python-dotenv
- Git

La aplicación utiliza PostgreSQL como fuente persistente de información. No se almacena estado de negocio en memoria del proceso, permitiendo que la aplicación pueda ejecutarse en múltiples réplicas.

---

## 2. Estructura del proyecto

```text
starter/

├── app/
│   ├── app.py
│   ├── config.py
│   ├── db.py
│   └── requirements.txt
│
├── db/
│   └── schema.sql
│
├── docs/
│   ├── code_review.md
│   └── decisiones.md
│
├── tests/
│   └── test_pagos.py
│
├── .env.example
└── README.md
```

El archivo `.env` se utiliza únicamente de forma local y no debe incluirse en el repositorio.

---

## 3. Configuración

La aplicación utiliza variables de entorno para la configuración de PostgreSQL y del servidor.

Ejemplo basado en `.env.example`:

```env
PGHOST=db
PGPORT=5432
PGUSER=polizas_app
PGPASSWORD=REEMPLAZAR
PGDATABASE=polizas

APP_PORT=8000
APP_ENV=development
APP_TZ=America/Bogota
```

Para ejecución local, se debe crear un archivo `.env` a partir de `.env.example` y configurar las credenciales correspondientes.

No se deben almacenar credenciales reales dentro del repositorio.

El archivo `.env` debe permanecer fuera del control de versiones.

---

## 4. Instalación

Crear y activar el entorno virtual:

```bash
python -m venv .venv
```

En Git Bash:

```bash
source .venv/Scripts/activate
```

Instalar las dependencias:

```bash
pip install -r app/requirements.txt
```

---

## 5. Base de datos

La implementación utiliza PostgreSQL.

El esquema se encuentra en:

```text
db/schema.sql
```

El modelo incluye:

- clientes
- pólizas
- pagos
- eventos de webhook

Se utilizan restricciones y tipos de datos para proteger la integridad de la información.

Los valores monetarios utilizan:

```sql
NUMERIC(12,2)
```

La tabla `pagos` tiene una restricción `UNIQUE` sobre `idempotency_key`.

La tabla `webhook_eventos` tiene una restricción `UNIQUE` sobre `event_id`.

También existen claves foráneas, restricciones `CHECK` e índices para las consultas principales.

---

## 6. Ejecución de la aplicación

Con el entorno virtual activado:

```bash
python app/app.py
```

Por defecto, la aplicación utiliza el puerto:

```text
8000
```

El endpoint de comprobación está disponible en:

```text
GET /health
```

Ejemplo:

```bash
curl http://localhost:8000/health
```

Respuesta:

```json
{
  "status": "ok"
}
```

---

# 7. Endpoints implementados

## POST /polizas/{id}/pagos

Registra un nuevo pago.

Requiere el header:

```text
Idempotency-Key
```

Ejemplo:

```bash
curl -X POST http://localhost:8000/polizas/1/pagos \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: pago-prueba-001" \
  -d '{"monto":50000.00,"referencia":"REF-PRUEBA-001"}'
```

Un pago nuevo se registra inicialmente como:

```json
{
  "estado": "pendiente",
  "pago_id": 1
}
```

### Idempotencia

Si se repite la misma solicitud utilizando la misma `Idempotency-Key` y los mismos datos, no se crea otro pago.

La llave se almacena en PostgreSQL y tiene una restricción `UNIQUE`.

Si la misma llave se utiliza posteriormente con datos diferentes, la API devuelve:

```json
{
  "error": "El Idempotency-Key ya fue utilizada con datos diferentes"
}
```

con HTTP `409 Conflict`.

Esto permite que la protección contra duplicados funcione incluso cuando existen varias réplicas de la aplicación.

---

## POST /webhooks/pasarela

Procesa la respuesta de la pasarela de pagos.

Ejemplo:

```bash
curl -X POST http://localhost:8000/webhooks/pasarela \
  -H "Content-Type: application/json" \
  -d '{"event_id":"evt-prueba-001","pago_id":1,"estado":"autorizado"}'
```

Los estados permitidos son:

- `autorizado`
- `rechazado`

Cada `event_id` se almacena en `webhook_eventos` y tiene una restricción `UNIQUE`.

Si el mismo webhook vuelve a llegar, se identifica como duplicado y no se procesa nuevamente.

---

## GET /polizas/{id}/saldo

Consulta el estado financiero de una póliza.

El cálculo considera exclusivamente los pagos que se encuentran en estado:

```text
autorizado
```

Los pagos pendientes y rechazados no se contabilizan.

El saldo se calcula como:

```text
saldo = prima_total - total_autorizado
```

El saldo nunca se muestra como un valor negativo.

---

## GET /clientes/{id}/resumen

Obtiene la información del cliente y el resumen de sus pólizas.

El cálculo de los pagos autorizados se realiza mediante una consulta agregada con `JOIN` y `GROUP BY`.

Esto evita el problema N+1 existente en el módulo legacy.

Si el cliente no existe, se devuelve:

```text
404 Not Found
```

---

# 8. Validaciones implementadas

La API valida diferentes situaciones antes de modificar la base de datos.

Entre ellas:

- `Idempotency-Key` obligatoria.
- `Idempotency-Key` no vacía.
- Longitud máxima de `Idempotency-Key`.
- JSON válido.
- Cuerpo JSON de tipo objeto.
- Campo `monto` obligatorio.
- Campo `referencia` obligatorio.
- Monto positivo.
- Máximo dos decimales.
- Referencia de tipo texto.
- Referencia no vacía.
- Longitud máxima de referencia.
- `event_id` obligatorio en webhooks.
- `event_id` de tipo texto.
- Longitud máxima de `event_id`.
- `pago_id` entero positivo.
- Estados de webhook válidos.
- Existencia de la póliza.
- Existencia del pago.
- Existencia del cliente.
- Pólizas canceladas.
- Pólizas vencidas.

---

# 9. Manejo del dinero

Para evitar problemas de precisión de punto flotante se utiliza `Decimal` en Python.

Los valores monetarios de PostgreSQL se almacenan como:

```sql
NUMERIC(12,2)
```

De esta manera las operaciones monetarias se realizan con precisión exacta al centavo.

---

# 10. Manejo de fechas

Los timestamps utilizados por la aplicación se generan con zona horaria UTC:

```python
datetime.now(timezone.utc)
```

La configuración de la aplicación contempla:

```text
America/Bogota
```

Los timestamps persistidos utilizan:

```sql
TIMESTAMP WITH TIME ZONE
```

Esto permite conservar correctamente el instante del evento y realizar posteriormente la conversión correspondiente para presentación al usuario.

---

# 11. Concurrencia e idempotencia

La solución no utiliza memoria del proceso para controlar pagos duplicados o webhooks repetidos.

La protección se realiza principalmente en PostgreSQL mediante:

- `UNIQUE(idempotency_key)`
- `UNIQUE(event_id)`
- transacciones
- `FOR UPDATE`
- claves foráneas
- restricciones `CHECK`

Al actualizar el estado de una póliza se utiliza un bloqueo de fila para evitar condiciones de carrera durante el cálculo.

Esto permite que la solución sea compatible con un escenario de múltiples réplicas detrás de un balanceador.

---

# 12. Code Review

Se realizó una revisión del módulo legacy:

```text
code_review/legacy_pagos.py
```

El archivo original no fue modificado.

Los principales problemas identificados incluyen:

- falta de idempotencia en pagos
- webhooks no idempotentes
- pagos pendientes contabilizados
- credenciales hardcodeadas
- SQL Injection
- errores de webhook respondidos incorrectamente
- falta de validación de estados
- falta de validación de pólizas vencidas o canceladas
- uso de `float` para dinero
- manejo incorrecto de zonas horarias
- conexiones sin cierre garantizado
- problemas de concurrencia
- problema N+1
- validaciones insuficientes
- manejo incorrecto de errores

El análisis completo se encuentra en:

```text
docs/code_review.md
```

---

# 13. Decisiones técnicas

Las principales decisiones técnicas se documentaron en:

```text
docs/decisiones.md
```

Entre ellas se encuentran las decisiones relacionadas con:

- uso de `Decimal` y `NUMERIC`
- idempotencia persistida en PostgreSQL
- manejo de webhooks mediante `event_id`
- consistencia y concurrencia
- manejo de fechas y zonas horarias

---

# 14. Pruebas automatizadas

Se implementaron pruebas automatizadas utilizando `pytest`.

Actualmente existen **9 pruebas automatizadas** que cubren:

1. Health check.
2. Pago sin `Idempotency-Key`.
3. Pago con monto negativo.
4. Pago con más de dos decimales.
5. Webhook sin `event_id`.
6. Webhook con estado inválido.
7. Webhook con pago inexistente.
8. Pago sobre póliza inexistente.
9. Idempotencia de pagos.

Las pruebas se ejecutan mediante:

```bash
pytest -v
```

Resultado obtenido:

```text
============================= test session starts =============================
platform win32 -- Python 3.12.5, pytest-8.4.1, pluggy-1.6.0
collected 9 items

tests/test_pagos.py::test_health PASSED
tests/test_pagos.py::test_pago_sin_idempotency_key PASSED
tests/test_pagos.py::test_pago_con_monto_negativo PASSED
tests/test_pagos.py::test_pago_con_mas_de_dos_decimales PASSED
tests/test_pagos.py::test_webhook_sin_event_id PASSED
tests/test_pagos.py::test_webhook_estado_invalido PASSED
tests/test_pagos.py::test_webhook_pago_inexistente PASSED
tests/test_pagos.py::test_poliza_inexistente PASSED
tests/test_pagos.py::test_pago_idempotente PASSED

============================== 9 passed in 0.26s ==============================
```

La prueba `test_pago_idempotente` verifica que una misma `Idempotency-Key` no genere registros de pago duplicados.

---

# 15. Pruebas manuales realizadas

También se realizaron pruebas mediante `curl`.

Se verificó, entre otros casos:

### Idempotencia de pagos

La misma `Idempotency-Key` evita crear un segundo pago.

También se comprobó que reutilizar una llave con datos diferentes devuelve `409 Conflict`.

### Webhooks

Se verificó:

- webhook autorizado
- webhook rechazado
- `event_id` obligatorio
- estado inválido
- pago inexistente

### Cálculo de póliza

Se comprobó que un pago rechazado no aumenta el valor autorizado de la póliza.

### Validaciones

Se probaron:

- montos negativos
- montos con más de dos decimales
- pólizas inexistentes
- ausencia de `Idempotency-Key`

---

# 16. Qué se dejó fuera

No se implementó frontend porque el enunciado indica explícitamente que no forma parte de la evaluación.

Tampoco se implementó:

- integración real con una pasarela externa
- autenticación/autorización de usuarios
- sistema de colas
- observabilidad externa
- despliegue productivo completo
- cobertura del 100 % de código

El objetivo fue concentrarse en las reglas de negocio y los problemas principales planteados en la prueba.

---

# 17. Estado actual

La implementación cuenta con:

- API REST funcional.
- PostgreSQL.
- Idempotencia de pagos.
- Idempotencia de webhooks.
- Validaciones de entrada.
- Precisión monetaria con `Decimal`.
- Restricciones de integridad en PostgreSQL.
- Manejo de concurrencia mediante transacciones y bloqueos.
- Consulta agregada para el resumen de clientes.
- Manejo de errores HTTP.
- Pruebas automatizadas.
- Documentación de decisiones técnicas.
- Code review del módulo legacy.

Las pruebas automatizadas actuales finalizan correctamente con:

```text
9 passed
```

El proyecto se encuentra preparado para su revisión y entrega.

---

# 18. Comandos principales

Activar entorno virtual:

```bash
source .venv/Scripts/activate
```

Instalar dependencias:

```bash
pip install -r app/requirements.txt
```

Ejecutar aplicación:

```bash
python app/app.py
```

Ejecutar pruebas:

```bash
pytest -v
```

Ver estado de Git:

```bash
git status
```

---

## 19. Entrega

Antes de realizar la entrega final se recomienda verificar:

```bash
git status
```

y ejecutar nuevamente:

```bash
pytest -v
```

El resultado esperado es:

```text
9 passed
```

El repositorio debe contener únicamente los archivos necesarios para la prueba y no debe incluir credenciales reales ni el archivo `.env`.

La implementación mantiene el módulo legacy original sin modificaciones y documenta sus hallazgos en `docs/code_review.md`.