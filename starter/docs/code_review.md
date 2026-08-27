# Code Review — Módulo legacy de pagos

## Resumen

El módulo presenta varios problemas relacionados con seguridad, consistencia de datos, manejo de errores, concurrencia y cumplimiento de las reglas de negocio.

Los problemas de mayor impacto están relacionados con la falta de idempotencia en los pagos, el procesamiento de webhooks repetidos y el cálculo incorrecto del estado de las pólizas.

---

## 🔴 Problemas críticos

### 1. Falta de idempotencia en los pagos

El endpoint `POST /polizas/{id}/pagos` no utiliza el header `Idempotency-Key` y realiza un `INSERT` por cada solicitud.

Si la aplicación móvil reintenta una misma solicitud debido a un timeout o problema de conexión, se puede crear más de un registro para el mismo pago.

**Impacto:** el cliente puede terminar con un cobro duplicado.

**Corrección:** utilizar el `Idempotency-Key` enviado por el cliente, persistirlo en la base de datos y agregar una restricción `UNIQUE`. La garantía debe estar en la base de datos para funcionar correctamente incluso con múltiples réplicas de la aplicación.

### 2. Webhooks no idempotentes

El endpoint `/webhooks/pasarela` no utiliza ni almacena el `event_id` enviado por la pasarela.

Por lo tanto, si el mismo webhook llega varias veces, el sistema puede procesarlo nuevamente.

**Impacto:** un mismo evento puede procesarse múltiples veces y afectar incorrectamente el estado de un pago o de una póliza.

**Corrección:** almacenar cada `event_id` procesado y agregar una restricción `UNIQUE` para garantizar que el mismo evento no pueda registrarse dos veces.

### 3. Pagos pendientes contabilizados

En `actualizar_estado_poliza()` se utiliza una lógica equivalente a:

```python
if estado != "rechazado":
    total += float(monto)
```

Esto provoca que tanto los pagos `pendiente` como los `autorizado` puedan sumarse al total.

Según RN-3, únicamente los pagos `autorizado` deben afectar el saldo de la póliza.

**Impacto:** una póliza puede aparecer como `al_dia` aunque el pago todavía esté pendiente de confirmación por parte de la pasarela.

**Corrección:** contabilizar exclusivamente los pagos cuyo estado sea `autorizado`.

---

### 4. Credenciales de base de datos en el código

El módulo contiene credenciales directamente en el código fuente, por ejemplo:

```python
DB_USER = "admin"
DB_PASS = "Pr0d2024!"
```

Esto representa un riesgo de seguridad porque las credenciales pueden quedar expuestas en el repositorio, copias del código, logs o herramientas de desarrollo.

**Impacto:** una persona que obtenga acceso al código podría utilizar las credenciales para intentar acceder a la base de datos.

**Corrección:** utilizar variables de entorno o un gestor de secretos y evitar almacenar credenciales reales dentro del código fuente.

---

### 5. SQL Injection

Se utilizan consultas construidas mediante interpolación de strings, por ejemplo:

```python
cur.execute(
    "UPDATE pagos SET estado = '%s' WHERE id = %s" % (nuevo_estado, pago_id)
)
```

También se observa este patrón al insertar datos:

```python
"VALUES (%s, %s, '%s', 'pendiente', '%s')" % (
    poliza_id,
    monto,
    referencia,
    datetime.now()
)
```

Los valores provenientes de las peticiones no deberían incorporarse directamente a las consultas SQL.

**Impacto:** un atacante podría manipular determinados parámetros y ejecutar consultas SQL no autorizadas.

**Corrección:** utilizar siempre consultas parametrizadas mediante los parámetros de `psycopg2`.

---

### 6. Los errores del webhook se responden como exitosos

El webhook captura excepciones y responde con éxito:

```python
except Exception as e:
    log.warning("error en webhook: %s", e)

return jsonify({"ok": True}), 200
```

Aunque el procesamiento falle, el endpoint devuelve HTTP `200`.

**Impacto:** la pasarela puede interpretar que el evento fue procesado correctamente y no volver a enviarlo, dejando el pago sin actualizar.

**Corrección:** diferenciar errores de validación, errores transitorios y eventos ya procesados. Solo devolver éxito cuando el evento haya sido procesado correctamente o cuando ya haya sido procesado previamente.

---

## 🟠 Problemas importantes

### 7. No se valida correctamente el estado recibido por el webhook

Existe una lista de estados:

```python
ESTADOS = ["pendiente", "autorizado", "rechazado"]
```

pero esta variable no se utiliza para validar el estado recibido.

Además, para un webhook únicamente deberían aceptarse los estados definidos por RN-2: `autorizado` y `rechazado`.

**Impacto:** podrían recibirse valores de estado que no corresponden al flujo esperado.

**Corrección:** validar explícitamente que el estado recibido sea `autorizado` o `rechazado` y reforzar la regla mediante una restricción `CHECK` en la base de datos.

### 8. No se validan las transiciones de estado

El código permite modificar directamente el estado del pago:

```sql
UPDATE pagos SET estado = ...
```

No existe una protección clara para evitar transiciones inconsistentes entre estados.

Por ejemplo, un pago que ya llegó a un estado final podría modificarse nuevamente sin validar la transición.

**Corrección:** definir las transiciones de estado permitidas y aplicar las reglas dentro de una transacción.

### 9. No se validan pólizas canceladas o vencidas

RN-6 establece que no se puede registrar un pago sobre una póliza `cancelada` ni sobre una póliza vencida.

El código legacy únicamente verifica que la póliza exista:

```python
if poliza is None:
    return jsonify({"error": "poliza no encontrada"}), 404
```

No se comprueba el estado de la póliza ni su fecha de vencimiento antes de registrar el pago.

**Impacto:** se pueden registrar pagos sobre pólizas que ya no deberían aceptar nuevos pagos.

**Corrección:** validar el estado y la fecha de vencimiento antes de crear el pago.

### 10. Uso de `float` para manejar dinero

El código convierte los valores monetarios a `float`:

```python
prima = float(cur.fetchone()[0])
```

y:

```python
total += float(monto)
```

Los números de punto flotante utilizan representación binaria y pueden introducir errores de precisión.

Esto puede afectar el requisito RN-4, que exige que las operaciones monetarias cuadren al centavo.

**Corrección:** utilizar `NUMERIC(12,2)` o una precisión equivalente en PostgreSQL y `Decimal` en Python.

### 11. Manejo incorrecto de fechas y zonas horarias

El código utiliza:

```python
datetime.now()
```

Esto genera un `datetime` sin información explícita de zona horaria.

RN-5 establece que la pasarela envía timestamps en UTC y que las fechas mostradas al usuario deben manejarse en `America/Bogota`.

**Corrección:** utilizar timestamps con zona horaria, almacenar los eventos en UTC y realizar la conversión a `America/Bogota` únicamente al presentar la información al usuario.

### 12. Validación insuficiente de datos de entrada

El código accede directamente a los campos recibidos:

```python
data = request.get_json()

monto = data["monto"]
referencia = data["referencia"]
```

No existe una validación completa para:

* JSON inválido.
* Campos faltantes.
* Tipos de datos incorrectos.
* Montos negativos.
* Monto igual a cero.
* Cantidad de decimales.
* Referencias inválidas o demasiado largas.

**Corrección:** validar explícitamente los datos de entrada y devolver códigos HTTP apropiados, principalmente `400 Bad Request` cuando los datos sean inválidos.

### 13. No se garantizan correctamente el cierre de conexiones y cursores

El código crea conexiones y cursores:

```python
conn = get_conn()
cur = conn.cursor()
```

pero no garantiza correctamente su cierre en todos los caminos de ejecución.

En una aplicación con múltiples solicitudes esto puede provocar agotamiento del número disponible de conexiones.

**Corrección:** utilizar context managers o un pool de conexiones para administrar correctamente los recursos de PostgreSQL.

### 14. Problemas de concurrencia al actualizar el estado de la póliza

El cálculo se realiza leyendo los pagos:

```sql
SELECT monto, estado
FROM pagos
WHERE poliza_id = %s
```

Después se calcula el total en Python y finalmente se actualiza el estado de la póliza.

Con dos réplicas procesando operaciones simultáneamente pueden producirse condiciones de carrera.

**Corrección:** utilizar transacciones, bloqueos apropiados y operaciones agregadas en la base de datos. Las garantías de consistencia no deben depender de memoria local de una réplica.

### 15. Problema N+1 en el resumen de clientes

En `resumen_cliente()` primero se consultan las pólizas:

```sql
SELECT id, prima_total
FROM polizas
WHERE cliente_id = %s
```

y posteriormente se realiza una consulta de pagos por cada póliza:

```python
for p in polizas:
    cur.execute(...)
```

Con muchos registros esto genera numerosas consultas innecesarias.

**Corrección:** utilizar una consulta agregada con `JOIN` y `GROUP BY` para obtener las pólizas y sus pagos autorizados en una cantidad menor de consultas.

### 16. No se comprueba correctamente que el cliente exista

Si la consulta del cliente no encuentra resultados:

```python
cliente = cur.fetchone()
```

puede devolver `None`.

Posteriormente el código puede intentar acceder a posiciones del resultado:

```python
cliente[0]
```

lo que puede provocar una excepción.

**Corrección:** comprobar explícitamente la existencia del cliente y devolver `404 Not Found`.

### 17. No se comprueba correctamente que la póliza exista en `/saldo`

Si:

```python
row = cur.fetchone()
```

devuelve `None`, posteriormente el código puede intentar acceder a:

```python
row[0]
row[1]
```

lo que provocaría un error.

**Corrección:** comprobar la existencia de la póliza y devolver `404 Not Found` cuando no exista.

---

## 🟡 Problemas menores

### 18. Importación innecesaria

El módulo contiene:

```python
import os
```

pero no se utiliza.

**Corrección:** eliminar las importaciones que no sean necesarias para mantener el código limpio y reducir dependencias innecesarias.

### 19. Modo debug habilitado

La aplicación se inicia utilizando:

```python
app.run(
    host="0.0.0.0",
    port=5000,
    debug=True
)
```

El modo debug no debería estar habilitado en producción.

**Riesgo:** ante determinados errores puede exponerse información interna de la aplicación.

**Corrección:** controlar el modo debug mediante configuración de entorno y mantenerlo desactivado en producción.

### 20. Registro del payload completo del webhook

El código registra el payload completo del webhook:

```python
log.info("webhook: %s", json.dumps(payload))
```

Dependiendo de la información enviada por la pasarela, esto podría provocar que datos sensibles terminen almacenados en los logs.

**Corrección:** registrar únicamente la información necesaria para trazabilidad, como `event_id`, `pago_id` y resultado del procesamiento, evitando registrar información sensible.

---

## Impacto de los 3 problemas más graves

### 1. Falta de idempotencia en los pagos

**Problema:** el mismo request puede crear múltiples pagos.

**Síntoma para el usuario:** al reintentar un pago debido a un timeout o problema de conexión, el cliente puede terminar con el mismo cobro registrado más de una vez y ser debitado doblemente.

### 2. Pagos pendientes contabilizados como autorizados

**Problema:** el sistema suma cualquier pago que no esté rechazado.

**Síntoma para el usuario:** una póliza puede aparecer como `al_dia` aunque su pago todavía esté pendiente de confirmación por parte de la pasarela.

### 3. Webhooks repetidos y manejo incorrecto de errores

**Problema:** no se almacena el `event_id` para garantizar idempotencia y, adicionalmente, determinados errores pueden responderse como `200 OK`.

**Síntoma para el usuario:** el resultado de un pago puede no reflejarse correctamente en la póliza, mientras la pasarela considera que la notificación fue recibida correctamente.

---

## ¿Qué arreglaría primero con media jornada?

Priorizaría los problemas de acuerdo con el impacto financiero, la consistencia de los datos y la seguridad del sistema.

### 1. Implementar idempotencia en el POST de pagos

Implementaría `Idempotency-Key` persistida y una restricción `UNIQUE` en la base de datos para garantizar que una misma solicitud no genere pagos duplicados, incluso cuando existan dos réplicas de la aplicación.

### 2. Implementar idempotencia y manejo correcto de webhooks

Almacenaría el `event_id` de cada webhook y utilizaría una restricción `UNIQUE` para evitar procesar dos veces el mismo evento.

También corregiría el manejo de errores para que un webhook que no pudo procesarse no responda incorrectamente con `200 OK`.

### 3. Corregir el cálculo de pagos y el manejo del dinero

Contabilizaría exclusivamente los pagos en estado `autorizado` y utilizaría `NUMERIC` en PostgreSQL y `Decimal` en Python para evitar errores de precisión.

### 4. Corregir el manejo de errores y las validaciones de entrada

Validaría campos obligatorios, tipos de datos, montos negativos o iguales a cero, estados permitidos y las reglas de negocio relacionadas con pólizas canceladas o vencidas.

### 5. Eliminar credenciales hardcodeadas y corregir SQL Injection

Eliminaría las credenciales de base de datos del código fuente y utilizaría variables de entorno o un gestor de secretos.

Además, reemplazaría las consultas SQL construidas mediante interpolación por consultas parametrizadas.

### 6. Corregir fechas, concurrencia y administración de conexiones

Utilizaría timestamps con zona horaria, manejaría correctamente la concurrencia entre réplicas y garantizaría el cierre de conexiones y cursores de PostgreSQL.

### 7. Optimizar las consultas innecesarias

Corregiría el problema N+1 presente en el resumen de clientes utilizando consultas agregadas con `JOIN` y `GROUP BY`.

---

## Conclusión

El módulo legacy presenta riesgos importantes principalmente en **idempotencia, consistencia de pagos, procesamiento de webhooks, seguridad y manejo de errores**.

La prioridad debería ser garantizar primero que una misma operación no pueda generar cobros duplicados y que los eventos de la pasarela puedan procesarse de manera segura e idempotente. Posteriormente se deben corregir las reglas de negocio, precisión monetaria, validaciones y aspectos de seguridad.

La nueva implementación aborda estos puntos utilizando restricciones de base de datos, transacciones, consultas parametrizadas, `Decimal`, estados explícitos y almacenamiento persistente de las claves de idempotencia.
