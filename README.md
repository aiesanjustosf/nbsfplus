# IA Resumen Bancario – Banco Santa Fe PLUS

App Streamlit para procesar PDF de movimientos históricos de Nuevo Banco de Santa Fe PLUS.

## Funcionalidades

- Lectura de PDF descargado del home banking.
- Detección de movimientos históricos.
- Totalización de débitos y créditos.
- Conciliación bancaria.
- Resumen Operativo para Registración Módulo IVA.
- Detalle completo de movimientos.
- Descarga Excel.
- Descarga PDF del Resumen Operativo.

## Archivo principal

`streamlit_app.py`

## Deploy en Streamlit Cloud

Main file path:

```text
streamlit_app.py
```

## Nota sobre conciliación

El PDF usado de Banco Santa Fe PLUS no contiene columna de saldo. Si el PDF no informa saldo anterior/final, la app solo puede calcular el movimiento neto del período. Para calcular automáticamente el saldo anterior necesita que el PDF informe al menos el saldo final, o que el usuario lo ingrese.
