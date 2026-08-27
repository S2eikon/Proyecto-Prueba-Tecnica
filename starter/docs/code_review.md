## Resumen

El módulo presenta varios problemas de seguridad, consistencia de datos,
manejo de errores, concurrencia y cumplimiento de las reglas de negocio.

Los problemas más críticos están relacionados con la falta de idempotencia
en los pagos, el procesamiento de webhooks repetidos y el cálculo incorrecto
del estado de las pólizas.

## 🔴 Problemas críticos

### 1. Falta de idempotencia
El endpoint `POST /polizas/{id}/pagos` no utiliza el header
`Idempotency-Key` y siempre ejecuta un `INSERT` en la tabla `pagos`.

Si la aplicación móvil reintenta la misma solicitud, se puede crear más de
un registro para el mismo pago.

**Impacto:** el cliente puede terminar con un cobro duplicado.

**Corrección:** utilizar el `Idempotency-Key` enviado por el cliente y
persistirlo en la base de datos con una restricción `UNIQUE`. La base de
datos debe garantizar que dos réplicas de la aplicación no puedan registrar
el mismo pago.

---

### 2. Webhooks no idempotentes
El endpoint `/webhooks/pasarela` no utiliza ni almacena el `event_id`
enviado por la pasarela.

Por lo tanto, si el mismo webhook llega varias veces, el sistema intenta
procesarlo nuevamente.

**Impacto:** un mismo evento puede procesarse múltiples veces y afectar
incorrectamente el estado de un pago o de una póliza.

**Corrección:** almacenar cada `event_id` procesado y agregar una restricción
`UNIQUE` para garantizar que el mismo evento no pueda procesarse dos veces.
---

### 3. Pagos pendientes contabilizados
---
En `actualizar_estado_poliza()` se utiliza:

python
if estado != "rechazado":
    total += float(monto)

Esto significa que tanto los pagos pendiente como los autorizado se
suman al total.

Según RN-3, únicamente los pagos autorizado deben afectar el saldo.

Impacto: una póliza puede aparecer como al_dia aunque el pago todavía
esté pendiente o no haya sido autorizado por la pasarela.

Corrección: sumar únicamente pagos cuyo estado sea autorizado.


### 4. Credenciales hardcodeadas
El módulo contiene credenciales directamente en el código:

DB_USER = "admin"
DB_PASS = "Pr0d2024!"

Esto representa un riesgo de seguridad porque las credenciales pueden quedar
expuestas en el repositorio, copias del código, logs o herramientas de
desarrollo.

Impacto: una persona que obtenga acceso al código podría utilizar las
credenciales para acceder a la base de datos.

Corrección: utilizar variables de entorno o un gestor de secretos y
nunca almacenar credenciales reales dentro del código fuente.


### 5. SQL Injection

Se utiliza interpolación de strings para construir consultas SQL

cur.execute(
    "UPDATE pagos SET estado = '%s' WHERE id = %s" % (nuevo_estado, pago_id)
)

También ocurre con referencia:

"VALUES (%s, %s, '%s', 'pendiente', '%s')"
% (poliza_id, monto, referencia, datetime.now())

Los valores provenientes de las peticiones no deberían incorporarse
directamente a las consultas.

Impacto: un atacante podría manipular determinados parámetros y ejecutar
consultas SQL no autorizadas.

Corrección: utilizar siempre consultas parametrizadas mediante los
parámetros de psycopg2.

### 6. Los errores del webhook se responden como exitosos

webhook captura cualquier excepción:

except Exception as e:
    log.warning("error en webhook: %s", e)

return jsonify({"ok": True}), 200

Aunque el procesamiento falle, el endpoint devuelve HTTP 200.

Impacto: la pasarela puede interpretar que el evento fue procesado
correctamente y no volver a enviarlo, dejando el pago sin actualizar.

Corrección: diferenciar errores de validación, errores transitorios y
eventos ya procesados. Solo devolver éxito cuando el evento haya sido
procesado correctamente o cuando ya se haya procesado previamente.

## 🟠 Problemas importantes

### 7. No se valida el estado recibido del webhook

Aunque existe:

ESTADOS = ["pendiente", "autorizado", "rechazado"]

la variable nunca se utiliza para validar el estado recibido.

Actualmente podrían recibirse valores distintos de los permitidos.

Corrección: validar que el estado pertenezca al conjunto permitido y
reforzar esta regla también mediante una restricción CHECK en la base de
datos.

### 8. No se validan las transiciones de estado

El código permite modificar directamente el estado del pago:

UPDATE pagos SET estado = ...

No se controla si una transición de estado es válida.

Por ejemplo, no existe una protección para evitar cambios inconsistentes
entre estados finales.

Corrección: definir las transiciones permitidas y aplicarlas dentro de
una transacción.

### 9. No se validan pólizas canceladas o vencidas

RN-6 establece que no se puede registrar un pago sobre una póliza cancelada
ni sobre una póliza vencida.

El código únicamente verifica que la póliza exista:

if poliza is None:
    return jsonify({"error": "poliza no encontrada"}), 404

No se comprueba el estado ni la fecha de vencimiento.

Impacto: se pueden registrar pagos sobre pólizas que no deberían aceptar
nuevos pagos.

Corrección: validar el estado y la fecha de vencimiento antes de crear
el pago.

### 10. Uso de float para manejar dinero

El código convierte los valores monetarios a float:

prima = float(cur.fetchone()[0])

y:

total += float(monto)

Los números de punto flotante pueden introducir errores de representación
binaria.

Esto incumple el objetivo de RN-4 de que las operaciones cuadren al
centavo.

Corrección: utilizar NUMERIC(10,2) en PostgreSQL y Decimal en Python.

### 11. Uso incorrecto de fechas y zonas horarias

El código utiliza:

datetime.now()

Esto genera un timestamp sin información de zona horaria.

RN-5 establece que las fechas de la pasarela llegan en UTC y que las fechas
mostradas al usuario deben manejarse en America/Bogota.

Corrección: utilizar timestamps con zona horaria y almacenar los eventos
en UTC. La conversión a America/Bogota debe realizarse al presentar la
información al usuario.

### 12. No se validan correctamente los datos de entrada

Se accede directamente a los campos:

data = request.get_json()
monto = data["monto"]
referencia = data["referencia"]

No se valida:

JSON inválido.
Campos faltantes.
Tipos de datos incorrectos.
Montos negativos.
Monto igual a cero.
Cantidad de decimales.
Referencias inválidas o demasiado largas.

Corrección: utilizar validación explícita de entrada y devolver códigos
HTTP apropiados, principalmente 400 Bad Request cuando los datos sean
inválidos.

### 13. No se cierran conexiones ni cursores

El código crea conexiones:

conn = get_conn()
cur = conn.cursor()

pero no garantiza su cierre.

En una aplicación con múltiples solicitudes esto puede provocar agotamiento
de conexiones.

Corrección: utilizar context managers o un pool de conexiones.

### 14. Problemas de concurrencia al actualizar el saldo

El cálculo de la póliza se realiza leyendo todos sus pagos:

SELECT monto, estado
FROM pagos
WHERE poliza_id = %s

Después se calcula el total en Python y finalmente se actualiza la póliza.

Con dos réplicas procesando operaciones simultáneamente, pueden producirse
condiciones de carrera.

Corrección: utilizar transacciones, operaciones atómicas y restricciones
en la base de datos. El estado crítico no debe depender de memoria de una
réplica.

### 15. Problema N+1 en el resumen de clientes

En resumen_cliente() primero se consultan las pólizas:

SELECT id, prima_total
FROM polizas
WHERE cliente_id = %s

y posteriormente se realiza una consulta de pagos por cada póliza:

for p in polizas:
    cur.execute(...)

Con muchos registros esto genera numerosas consultas innecesarias.

Corrección: utilizar una consulta agregada o un JOIN para obtener la
información necesaria en una cantidad menor de consultas.

### 16. No se comprueba que el cliente exista

Si el cliente no existe:

cliente = cur.fetchone()

puede devolver None.

Posteriormente el código intenta acceder a:

cliente[0]

lo que puede producir un error.

Corrección: comprobar la existencia del cliente y devolver 404 Not Found.

### 17. No se comprueba que la póliza exista en /saldo

En:

row = cur.fetchone()

no se comprueba si row es None.

Si la póliza no existe, el código intenta acceder a:

row[0]
row[1]

Corrección: devolver 404 Not Found cuando la póliza no exista.
## 🟡 Problemas menores

18. Importación innecesaria de os

El módulo contiene:

import os

pero no utiliza esta librería.

Debe eliminarse si no se necesita.

## Impacto de los 3 problemas más graves

### 1. ...
ESTADOS está definido pero no se utiliza

Se declara:

ESTADOS = ["pendiente", "autorizado", "rechazado"]

pero no se utiliza para validar los datos recibidos.

Debe utilizarse para validación o eliminarse.

### 2. ...
20. debug=True

La aplicación se inicia con:

app.run(host="0.0.0.0", port=5000, debug=True)

El modo debug no debería estar habilitado en producción.

Riesgo: puede exponer información interna de la aplicación ante errores.

Corrección: controlar el modo debug mediante configuración de entorno y
mantenerlo desactivado en producción.

### 3. ...
Registro del payload completo del webhook

Se registra:

log.info("webhook: %s", json.dumps(payload))

Dependiendo de la información enviada por la pasarela, esto puede terminar
guardando información sensible en los logs.

Corrección: registrar únicamente los datos necesarios para trazabilidad,
evitando información sensible.

### Impacto de los 3 problemas más graves
1. Falta de idempotencia en los pagos

Problema: el mismo request puede crear múltiples pagos.

Síntoma para el usuario: al reintentar un pago debido a un timeout o
problema de conexión, el cliente puede terminar con el mismo cobro registrado
más de una vez y ser debitado doblemente.

2. Pagos pendientes contabilizados como autorizados

Problema: el sistema suma cualquier pago que no esté rechazado.

Síntoma para el usuario: una póliza puede aparecer como al_dia aunque
su pago todavía esté pendiente de confirmación por parte de la pasarela.

3. Webhooks repetidos y errores respondidos como HTTP 200

Problema: no se almacena el event_id y los errores del procesamiento
se responden como si el webhook hubiera sido procesado correctamente.

Síntoma para el usuario: el resultado de un pago puede no reflejarse en
la póliza, mientras la pasarela considera que la notificación fue recibida
correctamente.

## ¿Qué arreglaría primero con media jornada?

Priorizaría los problemas de acuerdo con el impacto financiero, la
consistencia de los datos y la seguridad del sistema:

### 1. Idempotencia del POST de pagos.

Implementaría `Idempotency-Key` persistida y una restricción `UNIQUE` en la
base de datos para garantizar que una misma solicitud no genere pagos
duplicados, incluso ejecutándose en 2 réplicas.

### 2. Idempotencia y manejo correcto de webhooks.

Almacenaría el `event_id` de cada webhook y utilizaría una restricción
`UNIQUE` para evitar procesar dos veces el mismo evento. También corregiría
el manejo de errores para que un webhook que no pudo procesarse no responda
incorrectamente con `200 OK`.

### 3. Corregir el cálculo de pagos y el manejo del dinero.

Contabilizaría exclusivamente los pagos en estado `autorizado` y utilizaría
`NUMERIC` en PostgreSQL y `Decimal` en Python para evitar errores de precisión
en los montos.

### 4. Corregir el manejo de errores y las validaciones de entrada.

Validaría campos obligatorios, tipos de datos, montos negativos o iguales a
cero, estados permitidos y las reglas de negocio relacionadas con pólizas
canceladas o vencidas.

### 5. Eliminar credenciales hardcodeadas y corregir SQL Injection.

Eliminaría las credenciales de base de datos del código fuente y utilizaría
variables de entorno o un gestor de secretos. Además, reemplazaría las
consultas SQL construidas mediante interpolación por consultas parametrizadas.

### 6. Corregir fechas, concurrencia y conexiones.

Utilizaría timestamps con zona horaria, manejaría correctamente la
concurrencia entre réplicas y garantizaría el cierre de conexiones y cursores
de PostgreSQL.

### 7. Optimizar consultas innecesarias.

Corregiría el problema N+1 presente en el resumen de clientes utilizando
consultas agregadas con `JOIN` y `GROUP BY`.