# ia_resumen_bsf_plus.py
# IA Resumen Bancario – Nuevo Banco de Santa Fe PLUS
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
    page_title="IA Resumen Bancario – Nuevo Banco de Santa Fe PLUS",
    page_icon=str(FAVICON) if FAVICON.exists() else None,
    layout="centered",
)

if LOGO.exists():
    st.image(str(LOGO), width=220)

st.title("IA Resumen Bancario – Nuevo Banco de Santa Fe PLUS")

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

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

# ---------------- regex ----------------
DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\b")
MONEY_RE = re.compile(r"-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}")
ROW_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+"          # fecha
    r"(\d+)\s+"                          # código concepto
    r"(.+?)\s+"                           # concepto
    r"(\d+)\s+"                           # nro comprobante
    r"(\d+)\s+"                           # sucursal
    r"\$\s+"                              # signo pesos
    r"(-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})$"  # importe
)

# Resumen operativo / registración
RE_SIRCREB = re.compile(r"SIRCREB", re.IGNORECASE)
RE_LEY_25413 = re.compile(r"IMP\.\s*LEY\s*25413|LEY\s*25\.?413", re.IGNORECASE)
RE_IVA_GRAL = re.compile(r"\bIVA\s+GRAL\.?\b", re.IGNORECASE)
RE_IVA_REDUC = re.compile(r"\bIVA\s+REDUC\.R\.I\.?\b|\bIVA\s+REDUC", re.IGNORECASE)
RE_IVA_PERCEP = re.compile(r"\bIVA\s+PERCEP\.RG3337\b|\bIVA\s+PERCEP", re.IGNORECASE)
RE_COMISION_SELLADO = re.compile(r"\bCOMIS\b|\bCOMIS\s|\bCOMIS\.\b|SELLADO", re.IGNORECASE)
RE_INTERESES_CC = re.compile(r"\bINT\.CC\b", re.IGNORECASE)

# ---------------- utils ----------------
def normalize_money(tok: str) -> float:
    """Normaliza importes argentinos: -5,28 ó 1.234,56."""
    if not tok:
        return np.nan
    tok = tok.strip().replace("−", "-")
    neg = tok.startswith("-")
    tok = tok.lstrip("-").strip()
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
    if n is None or (isinstance(n, float) and np.isnan(n)):
        return "—"
    return f"{n:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _text_from_pdf(file_like) -> str:
    try:
        with pdfplumber.open(file_like) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return ""


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


def extract_metadata(lines: list[str]) -> dict:
    joined = "\n".join(lines)
    meta = {}
    m = re.search(r"Nro\.\s*de\s+CC\s*\$\s*([0-9]+)", joined, re.IGNORECASE)
    if m:
        meta["Cuenta"] = f"CC $ {m.group(1)}"
    m = re.search(r"CBU:\s*([0-9]{18,24})", joined, re.IGNORECASE)
    if m:
        meta["CBU"] = m.group(1)
    m = re.search(r"Fecha\s+Desde:\s*(\d{2}/\d{2}/\d{4})", joined, re.IGNORECASE)
    if m:
        meta["Fecha desde"] = m.group(1)
    m = re.search(r"Fecha\s+Hasta:\s*(\d{2}/\d{2}/\d{4})", joined, re.IGNORECASE)
    if m:
        meta["Fecha hasta"] = m.group(1)
    return meta


def parse_bsf_plus_movs(lines: list[str]) -> pd.DataFrame:
    """
    Parser Nuevo Banco de Santa Fe PLUS.
    Formato esperado:
    Fecha | Cód. concepto | Concepto | Nro. comprobante | Sucursal | Débito/Crédito

    El PDF no trae columna de saldo. La conciliación se hace con saldos manuales
    o por movimiento neto del período.
    """
    rows = []
    orden = 0
    i = 0
    n = len(lines)

    while i < n:
        ln = lines[i]
        if not DATE_RE.match(ln):
            i += 1
            continue

        # Algunos renglones pueden venir partidos: se une hasta encontrar importe.
        text = ln
        j = i + 1
        while not MONEY_RE.search(text) and j < n and not DATE_RE.match(lines[j]):
            text += " " + lines[j]
            j += 1

        m = ROW_RE.match(text)
        if m:
            fecha, codigo, concepto, comprobante, sucursal, importe_txt = m.groups()
            importe = normalize_money(importe_txt)
            if not np.isnan(importe):
                orden += 1
                rows.append(
                    {
                        "orden": orden,
                        "fecha": pd.to_datetime(fecha, format="%d/%m/%Y", errors="coerce"),
                        "codigo": str(codigo).strip(),
                        "concepto": " ".join(str(concepto).split()),
                        "comprobante": str(comprobante).strip(),
                        "sucursal": str(sucursal).strip(),
                        "importe": float(importe),
                    }
                )
        i = max(j, i + 1)

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["debito"] = np.where(df["importe"] < 0, -df["importe"], 0.0)
    df["credito"] = np.where(df["importe"] > 0, df["importe"], 0.0)

    # Respeta el orden original del PDF. No ordenar por fecha/comprobante.
    df = df.sort_values("orden").reset_index(drop=True)
    return df


def _sum_abs_or_net(df: pd.DataFrame, regex: re.Pattern, neto: bool = True) -> float:
    vals = df.loc[df["concepto"].str.contains(regex, na=False), "importe"]
    if vals.empty:
        return 0.0
    if neto:
        # débito negativo = gasto positivo; notas de crédito positivas restan.
        return float(-vals.sum())
    return float(vals.abs().sum())


def resumen_operativo(df: pd.DataFrame) -> pd.DataFrame:
    """
    Resumen operativo para registración.
    Se calcula por concepto neto: débitos positivos y créditos/devoluciones restan.
    """
    if df.empty:
        return pd.DataFrame(columns=["Concepto", "Importe"])

    out = [
        ["INTERESES CUENTA CORRIENTE", _sum_abs_or_net(df, RE_INTERESES_CC, neto=True)],
        ["COMISIONES / SELLADOS", _sum_abs_or_net(df, RE_COMISION_SELLADO, neto=True)],
        ["IVA GRAL. (21%)", _sum_abs_or_net(df, RE_IVA_GRAL, neto=True)],
        ["IVA REDUC. R.I.", _sum_abs_or_net(df, RE_IVA_REDUC, neto=True)],
        ["IVA PERCEP. RG3337", _sum_abs_or_net(df, RE_IVA_PERCEP, neto=True)],
        ["SIRCREB (NETO)", _sum_abs_or_net(df, RE_SIRCREB, neto=True)],
        ["IMP. LEY 25413 (NETO)", _sum_abs_or_net(df, RE_LEY_25413, neto=True)],
    ]
    total = sum(x[1] for x in out)
    out.append(["TOTAL", float(total)])
    return pd.DataFrame(out, columns=["Concepto", "Importe"])


# ---------------- UI principal ----------------
uploaded = st.file_uploader("Subí un PDF del resumen (Nuevo Banco de Santa Fe PLUS)", type=["pdf"])
if uploaded is None:
    st.info("La app no almacena datos. Procesamiento local en memoria.")
    st.stop()

data = uploaded.read()
txt_full = _text_from_pdf(io.BytesIO(data)).strip()
if not txt_full:
    st.error(
        "No se pudo leer texto del PDF. "
        "Este resumen parece estar escaneado (solo imagen). "
        "La herramienta solo funciona con PDFs descargados del home banking, "
        "donde el texto sea seleccionable."
    )
    st.stop()

lines = extract_lines(io.BytesIO(data))
meta = extract_metadata(lines)
df = parse_bsf_plus_movs(lines)

if df.empty:
    st.error("No se detectaron movimientos.")
    st.stop()

if meta:
    with st.expander("Datos del resumen", expanded=False):
        st.write(meta)

# ---------------- Conciliación ----------------
st.subheader("Conciliación bancaria")

cant_mov = int(len(df))
total_debitos = float(df["debito"].sum())
total_creditos = float(df["credito"].sum())
movimiento_neto = total_creditos - total_debitos

# Banco Santa Fe PLUS: en el PDF cargado no existe columna Saldo.
# Si en otro PDF apareciera saldo final/saldo anterior en el texto, se podría extender acá.
saldo_anterior = np.nan
saldo_final_inferido = np.nan
diferencia = np.nan
cuadra = False

r1c1, r1c2, r1c3 = st.columns(3)
with r1c1:
    st.metric("Saldo anterior", "$ —")
with r1c2:
    st.metric("Total débitos (–)", f"$ {fmt_ar(total_debitos)}")
with r1c3:
    st.metric("Total créditos (+)", f"$ {fmt_ar(total_creditos)}")

r2c1, r2c2 = st.columns(2)
with r2c1:
    st.metric("Saldo final (inferido)", "$ —")
with r2c2:
    st.metric("Diferencia", "$ —")

st.warning(
    "El PDF de Banco Santa Fe PLUS cargado no informa saldos. "
    "La app detecta movimientos, débitos, créditos y resumen operativo, "
    "pero no puede inferir automáticamente saldo anterior/final sin una columna Saldo o un saldo final informado por el banco."
)

with st.expander("Control manual de conciliación", expanded=False):
    st.caption("Usar solo si tenés el saldo final del banco. La fórmula es: Saldo anterior = Saldo final - Créditos + Débitos.")
    saldo_final_manual = st.number_input("Saldo final del banco", value=0.0, step=1000.0, format="%.2f")
    saldo_anterior_calculado = saldo_final_manual - total_creditos + total_debitos
    st.metric("Saldo anterior calculado", f"$ {fmt_ar(saldo_anterior_calculado)}")
    st.metric("Movimiento neto del período", f"$ {fmt_ar(movimiento_neto)}")

# ---------------- Resumen Operativo ----------------
st.subheader("Resumen Operativo: Registración Módulo IVA")
df_ro = resumen_operativo(df)
df_ro_view = df_ro.copy()
df_ro_view["Importe"] = df_ro_view["Importe"].map(fmt_ar)
st.dataframe(df_ro_view, use_container_width=True, hide_index=True)

# ---------------- Detalle de movimientos ----------------
st.subheader("Detalle de movimientos")

df_view = df.copy()
for c in ["importe", "debito", "credito"]:
    if c in df_view.columns:
        df_view[c] = df_view[c].map(fmt_ar)
if "fecha" in df_view.columns:
    df_view["fecha"] = df_view["fecha"].dt.strftime("%d/%m/%Y")

st.dataframe(df_view, use_container_width=True, hide_index=True)

# ---------------- Descargas ----------------
st.subheader("Descargas")

first_date = df["fecha"].dropna().min()
last_date = df["fecha"].dropna().max()
date_suffix = ""
if pd.notna(first_date) and pd.notna(last_date):
    date_suffix = f"_{first_date.strftime('%Y%m%d')}_{last_date.strftime('%Y%m%d')}"

# Excel
try:
    import xlsxwriter  # noqa: F401

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Movimientos")
        df_ro.to_excel(writer, index=False, sheet_name="Resumen_Operativo")

        wb = writer.book
        money_fmt = wb.add_format({"num_format": "#,##0.00"})
        date_fmt = wb.add_format({"num_format": "dd/mm/yyyy"})

        ws = writer.sheets["Movimientos"]
        for idx, col in enumerate(df.columns):
            width = min(max(len(str(col)), 12) + 2, 48)
            ws.set_column(idx, idx, width)

        for colname in ["importe", "debito", "credito"]:
            if colname in df.columns:
                j = df.columns.get_loc(colname)
                ws.set_column(j, j, 18, money_fmt)

        if "fecha" in df.columns:
            j = df.columns.get_loc("fecha")
            ws.set_column(j, j, 14, date_fmt)

        ws2 = writer.sheets["Resumen_Operativo"]
        ws2.set_column(0, 0, 40)
        ws2.set_column(1, 1, 18, money_fmt)

    st.download_button(
        "📥 Descargar Excel",
        data=output.getvalue(),
        file_name=f"resumen_banco_santa_fe_plus{date_suffix}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
except Exception:
    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Descargar CSV (fallback)",
        data=csv_bytes,
        file_name=f"resumen_banco_santa_fe_plus{date_suffix}.csv",
        mime="text/csv",
        use_container_width=True,
    )

# PDF Resumen Operativo
if REPORTLAB_OK:
    try:
        pdf_buf = io.BytesIO()
        doc = SimpleDocTemplate(pdf_buf, pagesize=A4, title="Resumen Operativo - Nuevo Banco de Santa Fe PLUS")
        styles = getSampleStyleSheet()

        elems = [
            Paragraph("Resumen Operativo: Registración Módulo IVA (Nuevo Banco de Santa Fe PLUS)", styles["Title"]),
            Spacer(1, 10),
        ]

        datos = [["Concepto", "Importe"]]
        for _, r in df_ro.iterrows():
            datos.append([str(r["Concepto"]), fmt_ar(float(r["Importe"]))])

        tbl = Table(datos, colWidths=[360, 140])
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ]
            )
        )

        elems.append(tbl)
        elems.append(Spacer(1, 14))
        elems.append(Paragraph("Herramienta para uso interno - AIE San Justo", styles["Normal"]))

        doc.build(elems)

        st.download_button(
            "📄 Descargar PDF – Resumen Operativo",
            data=pdf_buf.getvalue(),
            file_name=f"Resumen_Operativo_Banco_Santa_Fe_PLUS{date_suffix}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    except Exception as e:
        st.info(f"No se pudo generar el PDF del Resumen Operativo: {e}")
