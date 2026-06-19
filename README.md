# IA Conciliación Bancaria – Banco Santa Fe PLUS

App Streamlit para procesar PDFs de **Movimientos históricos** del Nuevo Banco de Santa Fe PLUS.

## Qué hace

- Lee el PDF descargado del home banking.
- Detecta movimientos con columnas:
  - Fecha
  - Código de concepto
  - Concepto
  - Nro. comprobante
  - Sucursal
  - Débito
  - Crédito
- Suma débitos y créditos.
- El usuario ingresa el **saldo final del banco**.
- La app calcula automáticamente el **saldo anterior**:

```text
Saldo anterior = Saldo final - Total créditos + Total débitos
```

- Permite cargar opcionalmente el saldo anterior informado para controlar diferencia.
- Exporta Excel con conciliación y detalle de movimientos.

## Archivos

- `streamlit_app.py`: app principal.
- `requirements.txt`: dependencias para Streamlit Cloud.
- `.gitignore`: exclusiones básicas.

## Deploy en Streamlit Cloud

1. Crear repo nuevo en GitHub.
2. Subir estos archivos a la raíz del repo.
3. En Streamlit Cloud, crear nueva app.
4. Seleccionar:
   - Repository: el repo nuevo.
   - Branch: `main`.
   - Main file path: `streamlit_app.py`.
5. Deploy.

## Nota

El PDF de Banco Santa Fe PLUS no trae columna de saldo por movimiento. Por eso la app no puede tomar saldo anterior directamente desde el PDF: lo calcula a partir del saldo final informado y el total neto de movimientos.
