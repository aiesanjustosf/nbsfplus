# streamlit_app.py
# IA Conciliación Bancaria – Nuevo Banco de Santa Fe PLUS
# Herramienta para uso interno - AIE San Justo

import io
import re
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# ---------------- UI / assets ----------------
HERE = Path(__file__).parent
ASSETS = HERE / "assets"
LOGO = ASSETS / "logo_aie.png"
FAVICON = ASSETS / "favicon-aie.ico"

st.set_page_config(
    page_title="IA Conciliación Bancaria – Banco Santa Fe PLUS",
    page_icon=str(FAVICON) if FAVICON.exists() else None,
    layout="centered",
)

if LOGO.exists():
    st.image(str(LOGO), width=220)

st.title("IA Conciliación Bancaria – Banco Santa Fe PLUS")

st.markdown(
    """
    <style>
      .block-container { max-width: 980px; padding-top: 2rem; padding-bottom: 2rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------- deps diferidas ----------------
try:
    import pdfplumber
except Exception as e:
    st.error(f"No se pudo importar pdfplumber: {e}\nRevisá requirements.txt")
    st.stop()


# ---------------- regex ----------------
ROW_RE = re.compile(
    r"^(?P<fecha>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<cod>\d{1,4})\s+"
    r"(?P<body>.*?)\s+"
    r"(?P<comprobante>\d{1,12})\s+"
    r"(?P<sucursal>\d{1,5})\s+\$\s*"
    r"(?P<importe>-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2})\s*$"
)

# Caso partido típico al final de página:
# 02/03/2026 124 IMP. LEY 25413
# 0 545 $ -14.186,54
ROW_HEAD_RE = re.compile(
    r"^(?P<fecha>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<cod>\d{1,4})\s+"
    r"(?P<body>.+?)\s*$"
)

ROW_TAIL_RE = re.compile(
    r"^(?P<comprobante>\d{1,12})\s+"
    r"(?P<sucursal>\d{1,5})\s+\$\s*"
    r"(?P<importe>-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2})\s*$"
)

HEADER_SKIP_RE = re.compile(
    r"^(Nro\.|Cuenta:|CBU:|Titulares:|Fecha|Desde:|Hasta:|Referencia:|Movimientos históricos|"
    r"Fecha Cód\. concepto|Banco Santa Fe)$",
    re.IGNORECASE,
)


# ---------------- utils ----------------
def normalize_money(tok: str) -> float:
    """Normaliza importes argentinos: -1.234,56 o 1.234,56."""
    if not tok:
        return np.nan
    tok = tok.strip().replace("−", "-")
    neg = tok.startswith("-")
    tok = tok.strip("-").strip()
    if "," not in tok:
        return np.nan
    main, frac = tok.rsplit(",", 1)
    main = main.replace(".", "").replace(" ", "")
    try:
        val = float(f"{main}.{frac}")
        return -val if neg else val
    except Exception:
        return np.nan


def fmt_ar(n) -> str:
    if n is None:
        return "—"
    try:
        if pd.isna(n):
            return "—"
    except Exception:
        pass
    return f"{float(n):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")


def parse_ar_number(txt: str) -> float:
    """Convierte texto ingresado por usuario en número. Acepta 1.234,56 / -1.234,56 / 1234.56."""
    if txt is None:
        return np.nan
    s = str(txt).strip().replace("$", "").replace(" ", "")
    if not s:
        return np.nan

    # Formato argentino si hay coma decimal
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        # Formato tipo 1234.56
        s = s.replace(",", "")

    try:
        return float(s)
    except Exception:
        return np.nan


def extract_lines(file_like) -> list[str]:
    out: list[str] = []
    with pdfplumber.open(file_like) as pdf:
        for p in pdf.pages:
            txt = p.extract_text() or ""
            for raw in txt.splitlines():
                ln = " ".join(raw.split())
                if ln.strip():
                    out.append(ln)
    return out


def _parse_row(fecha, cod, body, comprobante, sucursal, importe, orden) -> dict:
    importe_f = normalize_money(importe)
    return {
        "orden_pdf": orden,
        "fecha": pd.to_datetime(fecha, format="%d/%m/%Y", errors="coerce"),
        "cod_concepto": str(cod).strip(),
        "concepto": " ".join(str(body).split()).strip(),
        "nro_comprobante": str(comprobante).strip(),
        "sucursal": str(sucursal).strip(),
        "importe": float(importe_f),
        "debito": float(-importe_f) if importe_f < 0 else 0.0,
        "credito": float(importe_f) if importe_f > 0 else 0.0,
    }


def parse_bsf_plus_movs(lines: list[str]) -> pd.DataFrame:
    """
    Parser para Nuevo Banco de Santa Fe PLUS.

    El PDF de movimientos históricos trae:
    Fecha | Cód. concepto | Concepto | Nro. comprobante | Sucursal | Débito | Crédito

    En el texto extraído suele aparecer un único importe al final de cada línea.
    La columna se determina por signo:
    - importe negativo => Débito
    - importe positivo => Crédito

    No hay columna Saldo en este PDF, por eso la conciliación usa saldo final informado
    y calcula el saldo anterior:
        saldo_anterior = saldo_final - total_creditos + total_debitos
    """
    rows = []
    orden = 0
    i = 0
    n = len(lines)

    while i < n:
        ln = lines[i].strip()

        if not ln or HEADER_SKIP_RE.search(ln):
            i += 1
            continue

        m = ROW_RE.match(ln)
        if m:
            orden += 1
            rows.append(_parse_row(
                m.group("fecha"),
                m.group("cod"),
                m.group("body"),
                m.group("comprobante"),
                m.group("sucursal"),
                m.group("importe"),
                orden,
            ))
            i += 1
            continue

        # Intento de fila partida en dos líneas
        mh = ROW_HEAD_RE.match(ln)
        if mh and i + 1 < n:
            mt = ROW_TAIL_RE.match(lines[i + 1].strip())
            if mt:
                orden += 1
                rows.append(_parse_row(
                    mh.group("fecha"),
                    mh.group("cod"),
                    mh.group("body"),
                    mt.group("comprobante"),
                    mt.group("sucursal"),
                    mt.group("importe"),
                    orden,
                ))
                i += 2
                continue

        i += 1

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha", "importe"]).reset_index(drop=True)

    # No reordenamos por fecha para preservar el orden del PDF.
    # Para descargar o ver, agregamos una fecha visible.
    return df


# ---------------- UI principal ----------------
uploaded = st.file_uploader("Subí un PDF de movimientos históricos (Banco Santa Fe PLUS)", type=["pdf"])
if uploaded is None:
    st.info("La app no almacena datos. Procesamiento local en memoria.")
    st.stop()

saldo_final_txt = st.text_input(
    "Saldo final del banco",
    value="",
    placeholder="Ejemplo: -9.568.027,63",
    help="Ingresá el saldo final que figura en el home banking o extracto. Con ese dato la app calcula el saldo anterior.",
)

saldo_anterior_control_txt = st.text_input(
    "Saldo anterior informado (opcional, para controlar diferencia)",
    value="",
    placeholder="Dejalo vacío si querés que la app solo lo calcule",
)

data = uploaded.read()

try:
    lines = extract_lines(io.BytesIO(data))
except Exception as e:
    st.error(f"No se pudo leer el PDF: {e}")
    st.stop()

df = parse_bsf_plus_movs(lines)

if df.empty:
    st.error("No se detectaron movimientos. Revisá que sea un PDF descargado del home banking y no una imagen escaneada.")
    st.stop()

# ---------------- Conciliación ----------------
st.subheader("Conciliación bancaria")

total_debitos = float(df["debito"].sum())
total_creditos = float(df["credito"].sum())
movimiento_neto = total_creditos - total_debitos

saldo_final = parse_ar_number(saldo_final_txt)

if pd.isna(saldo_final):
    saldo_anterior_calculado = np.nan
else:
    saldo_anterior_calculado = float(saldo_final - total_creditos + total_debitos)

saldo_final_calculado = (
    saldo_anterior_calculado + total_creditos - total_debitos
    if not pd.isna(saldo_anterior_calculado)
    else np.nan
)

diferencia_saldo_final = (
    saldo_final_calculado - saldo_final
    if not pd.isna(saldo_final_calculado) and not pd.isna(saldo_final)
    else np.nan
)

saldo_anterior_control = parse_ar_number(saldo_anterior_control_txt)
diferencia_saldo_anterior = (
    saldo_anterior_control - saldo_anterior_calculado
    if not pd.isna(saldo_anterior_control) and not pd.isna(saldo_anterior_calculado)
    else np.nan
)

r1c1, r1c2, r1c3 = st.columns(3)
with r1c1:
    st.metric("Total débitos (–)", f"$ {fmt_ar(total_debitos)}")
with r1c2:
    st.metric("Total créditos (+)", f"$ {fmt_ar(total_creditos)}")
with r1c3:
    st.metric("Movimiento neto", f"$ {fmt_ar(movimiento_neto)}")

r2c1, r2c2, r2c3 = st.columns(3)
with r2c1:
    st.metric("Saldo final informado", f"$ {fmt_ar(saldo_final)}")
with r2c2:
    st.metric("Saldo anterior calculado", f"$ {fmt_ar(saldo_anterior_calculado)}")
with r2c3:
    st.metric("Diferencia conciliación", f"$ {fmt_ar(diferencia_saldo_final)}")

if pd.isna(saldo_final):
    st.warning("Ingresá el saldo final del banco para calcular el saldo anterior.")
else:
    if abs(diferencia_saldo_final) < 0.01:
        st.success("Conciliado contra el saldo final informado.")
    else:
        st.error("No cuadra contra el saldo final informado.")

if not pd.isna(diferencia_saldo_anterior):
    st.caption(f"Diferencia contra saldo anterior informado: $ {fmt_ar(diferencia_saldo_anterior)}")
    if abs(diferencia_saldo_anterior) < 0.01:
        st.success("El saldo anterior informado coincide con el saldo anterior calculado.")
    else:
        st.error("El saldo anterior informado no coincide con el saldo anterior calculado.")

with st.expander("Ver fórmula"):
    st.write("Saldo anterior = Saldo final informado - Total créditos + Total débitos")
    st.code(
        f"Saldo anterior = {fmt_ar(saldo_final)} - {fmt_ar(total_creditos)} + {fmt_ar(total_debitos)} = {fmt_ar(saldo_anterior_calculado)}",
        language="text",
    )

# ---------------- Detalle de movimientos ----------------
st.subheader("Detalle de movimientos detectados")
st.caption(f"Movimientos detectados: {len(df)}")

df_view = df.copy()
df_view["fecha"] = df_view["fecha"].dt.strftime("%d/%m/%Y")
for c in ["importe", "debito", "credito"]:
    df_view[c] = df_view[c].map(fmt_ar)

st.dataframe(df_view, use_container_width=True, hide_index=True)

# ---------------- Descargas ----------------
st.subheader("Descargas")

first_date = df["fecha"].dropna().min()
last_date = df["fecha"].dropna().max()
date_suffix = ""
if pd.notna(first_date) and pd.notna(last_date):
    date_suffix = f"_{first_date.strftime('%Y%m%d')}_{last_date.strftime('%Y%m%d')}"

conciliacion = pd.DataFrame(
    [
        ["Total débitos", total_debitos],
        ["Total créditos", total_creditos],
        ["Movimiento neto", movimiento_neto],
        ["Saldo final informado", saldo_final],
        ["Saldo anterior calculado", saldo_anterior_calculado],
        ["Diferencia conciliación", diferencia_saldo_final],
        ["Saldo anterior informado", saldo_anterior_control],
        ["Diferencia saldo anterior informado", diferencia_saldo_anterior],
    ],
    columns=["Concepto", "Importe"],
)

try:
    import xlsxwriter  # noqa: F401

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        conciliacion.to_excel(writer, index=False, sheet_name="Conciliacion")
        df.to_excel(writer, index=False, sheet_name="Movimientos")

        wb = writer.book
        money_fmt = wb.add_format({"num_format": "#,##0.00"})
        date_fmt = wb.add_format({"num_format": "dd/mm/yyyy"})

        ws1 = writer.sheets["Conciliacion"]
        ws1.set_column(0, 0, 38)
        ws1.set_column(1, 1, 20, money_fmt)

        ws2 = writer.sheets["Movimientos"]
        for idx, col in enumerate(df.columns):
            width = min(max(len(str(col)), 12) + 2, 48)
            ws2.set_column(idx, idx, width)

        for colname in ["importe", "debito", "credito"]:
            if colname in df.columns:
                j = df.columns.get_loc(colname)
                ws2.set_column(j, j, 18, money_fmt)

        if "fecha" in df.columns:
            j = df.columns.get_loc("fecha")
            ws2.set_column(j, j, 14, date_fmt)

    st.download_button(
        "📥 Descargar Excel",
        data=output.getvalue(),
        file_name=f"conciliacion_bsf_plus{date_suffix}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
except Exception:
    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Descargar CSV (fallback)",
        data=csv_bytes,
        file_name=f"conciliacion_bsf_plus{date_suffix}.csv",
        mime="text/csv",
        use_container_width=True,
    )
