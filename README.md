# Prueba técnica — Ingeniero(a) de Backend (nivel intermedio)

**Tiempo estimado: 2 a 3 horas.** No es un cronómetro: es la referencia de esfuerzo
que esperamos. Tienes 4 días calendario para entregar.

Nos interesa más ver **cómo decides** que ver la solución perfecta. Si algo del
enunciado no te queda claro, escoge una interpretación razonable, escríbela en tu
README y sigue adelante.

---

## 1. Contexto

Somos una insurtech. Cobramos las primas de las pólizas a través de una pasarela de
recaudo externa. Hoy el módulo que registra esos pagos tiene dos problemas reales en
producción:

1. Cuando la app móvil reintenta un cobro, se registra **el pago dos veces** y el
   cliente queda doblemente debitado.
2. La pasarela nos notifica el resultado de cada pago por webhook, y **a veces envía
   el mismo webhook varias veces**. Eso deja pólizas con el saldo mal calculado.

Tu trabajo es rehacer este módulo bien.

---

## 2. Reglas de negocio

**RN-1.** Un pago se registra con `POST /polizas/{id}/pagos`. El request trae un header
`Idempotency-Key`. Si llega dos veces la misma llave con el mismo cuerpo, se registra
**un solo pago** y la segunda respuesta debe ser igual a la primera.

**RN-2.** Un pago nace en estado `pendiente`. La pasarela notifica después por webhook
si quedó `autorizado` o `rechazado`. Cada webhook trae un `event_id` único. **El mismo
`event_id` puede llegar varias veces**; procesarlo dos veces no puede cambiar nada.

**RN-3.** Una póliza está `al_dia` cuando la suma de sus pagos **autorizados** alcanza o
supera la prima total. Si no, está `en_mora`. Un pago `rechazado` nunca suma.

**RN-4.** Los montos son en pesos colombianos y la pasarela los envía con dos decimales
(`"120000.00"`). Las sumas tienen que cuadrar al centavo: nada de errores de redondeo.

**RN-5.** Las fechas que se le muestran al usuario van en `America/Bogota`. La pasarela
envía sus timestamps en UTC.

**RN-6.** Un pago no puede registrarse sobre una póliza `cancelada` ni sobre una póliza
vencida.

---

## 3. Entregables

### Parte A — Implementación (50 %)

Python o C#/.NET, tú eliges. PostgreSQL como base de datos.

| Método | Ruta | Notas |
|---|---|---|
| `POST` | `/polizas/{id}/pagos` | idempotente (RN-1) |
| `POST` | `/webhooks/pasarela` | tolerante a webhooks repetidos (RN-2) |
| `GET` | `/polizas/{id}/saldo` | prima total, pagado y saldo pendiente |
| `GET` | `/clientes/{id}/resumen` | pólizas del cliente con su estado y saldo |

Requisitos que sí evaluamos:
- Manejo correcto de errores y códigos HTTP.
- Validación de entrada (montos negativos, campos faltantes, tipos equivocados).
- **Mínimo 4 pruebas automatizadas.** Al menos una debe probar que la idempotencia
  funciona de verdad: dos requests con la misma llave dejan un solo pago en la base.
- El servicio corre en 2 réplicas detrás de un balanceador. No puedes guardar estado en
  memoria del proceso.

### Parte B — Modelo de datos (20 %)

En `db/schema_parcial.sql` está el modelo que existe hoy, con problemas. Entrega tu
versión en `db/schema.sql` y responde los TODO del final del archivo.

### Parte C — Code review (20 %)

`code_review/legacy_pagos.py` es el módulo que corre hoy en producción. **No lo
modifiques.** Entrega `docs/code_review.md` con:
- los problemas que encontraste, clasificados en críticos / importantes / menores,
- para los 3 más graves, qué le pasa al usuario final por culpa de ese bug (el síntoma,
  no la descripción del código),
- qué arreglarías primero si solo tuvieras media jornada.

### Parte D — Decisiones (10 %)

Un archivo corto, `docs/decisiones.md` (media página basta), con las 3 decisiones
técnicas que más te costó tomar: qué elegiste, qué alternativa descartaste y por qué.

---

## 4. Entrega

Repositorio Git privado o un ZIP que incluya la carpeta `.git`. Agrega tu propio
`README.md` con: cómo levantarlo, qué hiciste, qué dejaste fuera y por qué.

No incluyas credenciales reales de ningún sistema. Si encuentras alguna en el material
que te entregamos, repórtala: cuenta a favor.

## 5. Qué NO evaluamos

- Frontend: no hay.
- Cobertura de pruebas al 100 %.
- Que uses el framework de moda. Preferimos código simple que puedas explicar.
- Velocidad de entrega. Nadie gana puntos por entregar en una hora.

## 6. Después de la entrega

Agendamos minutos con el equipo técnico para que nos cuentes tus decisiones. Es una
conversación sobre lo que entregaste, no otra prueba: no tienes que preparar nada extra
ni escribir código en vivo. Puedes usar IA para resolver la prueba, pero tienes que
poder explicar lo que entregas.
