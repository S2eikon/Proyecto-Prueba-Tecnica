# Starter

Estructura sugerida, no obligatoria. Python o C#/.NET, tu decisión.

```
starter/
├── app/            # tu implementación
├── tests/          # mínimo 4 pruebas, al menos 1 de idempotencia
├── db/
│   ├── schema_parcial.sql   # el actual (no lo borres)
│   └── schema.sql           # el tuyo
├── docs/
│   ├── code_review.md       # Parte C
│   └── decisiones.md        # Parte D
└── code_review/             # NO MODIFICAR
```

## Sobre la prueba de idempotencia

Queremos ver dos llamadas con la misma `Idempotency-Key` y una aserción de que en la
base quedó **un solo** pago, además de que las dos respuestas son iguales. No basta con
verificar que el endpoint responde 200 las dos veces.

## Tip

Preferimos código simple y bien explicado a una arquitectura elaborada que no puedas
defender. Si usas una librería para resolver algo del enunciado, tienes que poder
explicar qué hace por dentro.
