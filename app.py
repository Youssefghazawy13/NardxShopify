# streamlit_app.py
import streamlit as st
import pandas as pd
import io
import re
from datetime import datetime

st.set_page_config(page_title="Nard ⇄ Shopify Reconciler", layout="wide")
st.title("Slot-X (Nard ⇄ Shopify) — Strict Barcode Reconciler (with digits-only fallback)")
st.caption("Matches Nard (POS) ↔ Shopify by BARCODE only. Product name & prices come from Nard. Exact match → fallback to digits-only match.")

# --------------------
# Helpers
# --------------------
COMMON_SHOP_SKU = ['sku', 'variant.sku', 'variant_sku', 'barcode', 'variant.barcode', 'handle_sku']
COMMON_SHOP_QTY = ['on hand (new)', 'on hand', 'inventory_quantity', 'available', 'quantity', 'on_hand', 'inventory']
ENCODINGS = ['utf-8', 'latin1', 'cp1252']

def pick_col(cols, candidates):
    cols_map = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in cols_map:
            return cols_map[cand.lower()]
    for c in cols:
        for cand in candidates:
            if cand.lower() in c.lower():
                return c
    return None

def read_any_csv_like(uploaded_file):
    for enc in ENCODINGS:
        try:
            return pd.read_csv(uploaded_file, dtype=str, encoding=enc, low_memory=False)
        except Exception:
            uploaded_file.seek(0)
            continue
    uploaded_file.seek(0)
    return pd.read_csv(uploaded_file, dtype=str, low_memory=False)

def split_barcodes(cell):
    if pd.isna(cell) or str(cell).strip() == '':
        return []
    s = str(cell)
    for sep in [';', '|', '/', '\\']:
        s = s.replace(sep, ',')
    parts = [p.strip().lower() for p in s.split(',') if p.strip() != '']
    return parts

def digits_only(s):
    if s is None:
        return ''
    return re.sub(r'[^0-9]', '', str(s)).lstrip('0')

def normalize_key_exact(s):
    if s is None:
        return ''
    return str(s).strip().lower()

def format_qty(x):
    try:
        fx = float(x)
        return int(fx) if fx.is_integer() else fx
    except Exception:
        return 0

def compute_flag(n_qty, s_qty, matched_any, diff_threshold=5):
    if not matched_any:
        return "Missing in Shopify"
    if float(n_qty) == 0 and float(s_qty) > 0:
        return "Dead item"
    diff = float(s_qty) - float(n_qty)
    if diff > 0:
        if abs(diff) >= diff_threshold:
            return f"Shopify more (High Risk | diff={int(diff) if float(diff).is_integer() else diff})"
        return f"Shopify more (Low Risk | diff={int(diff) if float(diff).is_integer() else diff})"
    if diff < 0:
        if abs(diff) >= diff_threshold:
            return f"Nard more (High Risk | diff={int(abs(diff)) if float(abs(diff)).is_integer() else abs(diff)})"
        return f"Nard more (Low Risk | diff={int(abs(diff)) if float(abs(diff)).is_integer() else abs(diff)})"
    return "Synced"

def to_excel_bytes(df):
    with io.BytesIO() as buffer:
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="RECON")
        return buffer.getvalue()

# --------------------
# UI — Upload
# --------------------
st.header("1) Upload files")
col1, col2 = st.columns(2)
with col1:
    nard_file = st.file_uploader("Upload Nard (Excel) — must include: name_en, barcodes, sale_price, available_quantity", type=['xls', 'xlsx'])
with col2:
    shop_file = st.file_uploader("Upload Shopify (CSV or Excel) — contains SKU column and qty column (we auto-detect)", type=['csv', 'xls', 'xlsx'])

st.markdown("---")

# Options
st.header("2) Options")
opt_col1, opt_col2 = st.columns([2, 1])
with opt_col1:
    threshold = st.number_input("High-risk threshold (abs difference >=)", min_value=1, value=5, step=1, help="If abs(diff) >= this value -> High Risk")
    separate_mismatches_only = st.checkbox("Show only mismatches by default (hide Synced)", value=False)
with opt_col2:
    explode_barcodes = st.radio("Multiple barcodes in Nard cell:", options=[
        "Match if ANY barcode matches Shopify (default, sums Shopify qty)",
        "Use FIRST barcode only"
    ], index=0)

st.markdown("---")

# --------------------
# Process
# --------------------
if st.button("Generate Reconciliation Report"):
    if not nard_file:
        st.error("Please upload the Nard Excel file.")
        st.stop()
    if not shop_file:
        st.error("Please upload the Shopify file.")
        st.stop()

    # Read Nard
    try:
        nard_df = pd.read_excel(nard_file, sheet_name=0, dtype=str)
    except Exception as e:
        st.error(f"Failed to read Nard Excel: {e}")
        st.stop()

    required_nard_cols = ['name_en', 'barcodes', 'sale_price', 'available_quantity']
    missing = [c for c in required_nard_cols if c not in [col for col in nard_df.columns]]
    if missing:
        st.error(f"Nard is missing required columns: {missing}. Rename or provide these columns and try again.")
        st.stop()

    # Normalize Nard
    nard = nard_df.copy()
    nard['Product name'] = nard['name_en'].astype(str).str.strip()
    nard['Barcodes_raw'] = nard['barcodes'].astype(str).fillna('')
    if explode_barcodes.startswith("Match if ANY"):
        nard['Barcodes_list'] = nard['Barcodes_raw'].apply(split_barcodes)
    else:
        nard['Barcodes_list'] = nard['Barcodes_raw'].apply(lambda s: [split_barcodes(s)[0]] if len(split_barcodes(s))>0 else [])

    nard['Nard Quantity'] = pd.to_numeric(nard['available_quantity'], errors='coerce').fillna(0).astype(float)
    nard['Sale Price'] = pd.to_numeric(nard['sale_price'], errors='coerce')

    # Read Shopify
    shop_df = None
    if str(shop_file.name).lower().endswith('.csv'):
        shop_df = read_any_csv_like(shop_file)
    else:
        try:
            shop_df = pd.read_excel(shop_file, sheet_name=0, dtype=str)
        except Exception:
            shop_file.seek(0)
            shop_df = read_any_csv_like(shop_file)

    shop_cols = list(shop_df.columns)
    sku_col = pick_col(shop_cols, COMMON_SHOP_SKU)
    qty_col = pick_col(shop_cols, COMMON_SHOP_QTY)

    if sku_col is None:
        st.error(f"Could not detect SKU column in Shopify file. Shopify columns: {shop_cols}")
        st.stop()

    # Normalize Shopify keys & quantities
    shop_df['sku_key_exact'] = shop_df[sku_col].astype(str).apply(normalize_key_exact)
    shop_df['sku_key_digits'] = shop_df[sku_col].astype(str).apply(digits_only)
    if qty_col:
        shop_df['qty_num'] = pd.to_numeric(shop_df[qty_col].fillna('0'), errors='coerce').fillna(0).astype(float)
    else:
        shop_df['qty_num'] = 0.0

    # Aggregate Shopify by exact sku and by digits-only separately (summing qtys)
    shop_exact_agg = shop_df.groupby('sku_key_exact', dropna=False)['qty_num'].sum().reset_index()
    shop_digits_agg = shop_df.groupby('sku_key_digits', dropna=False)['qty_num'].sum().reset_index()

    shop_map_exact = dict(zip(shop_exact_agg['sku_key_exact'], shop_exact_agg['qty_num']))
    shop_map_digits = dict(zip(shop_digits_agg['sku_key_digits'], shop_digits_agg['qty_num']))

    # For diagnostics: counts
    total_nard = len(nard)
    total_shop_rows = len(shop_df)

    # Build results
    results = []
    for idx, row in nard.iterrows():
        product_name = row['Product name']
        barcodes_list = row['Barcodes_list'] if isinstance(row['Barcodes_list'], list) else []
        n_qty = float(row['Nard Quantity'])
        sale_price = row['Sale Price'] if not pd.isna(row['Sale Price']) else None

        matched_skus = set()
        shop_total_qty = 0.0
        # 1) exact matching attempt
        for bc in barcodes_list:
            if not bc:
                continue
            key_exact = normalize_key_exact(bc)
            if key_exact in shop_map_exact:
                matched_skus.add(key_exact)
                shop_total_qty += float(shop_map_exact[key_exact])

        # 2) digits-only fallback for barcodes that didn't match exactly
        #    we also attempt digits-only even if exact matched nothing
        if not matched_skus:
            for bc in barcodes_list:
                if not bc:
                    continue
                key_digits = digits_only(bc)
                if key_digits and key_digits in shop_map_digits:
                    matched_skus.add(key_digits)
                    shop_total_qty += float(shop_map_digits[key_digits])

        # Additional safety: if there were exact matches but also digits-only matches on DIFFERENT keys,
        # we keep exact matches only (exact > fallback). This block ensures strict priority.
        # (already enforced by checking digits-only only when no exact matched above)
        matched_any = len(matched_skus) > 0
        s_qty = float(shop_total_qty) if matched_any else 0.0
        diff = s_qty - n_qty
        flag = compute_flag(n_qty, s_qty, matched_any, diff_threshold=threshold)

        results.append({
            "Product name": product_name,
            "Barcodes": row['Barcodes_raw'],
            "Nard Quantity": n_qty,
            "Shopify Quantity": s_qty,
            "Quantity Difference": diff,
            "Sale Price": sale_price,
            "Sku Flag": flag,
            "Matched Shop SKUs": ",".join(sorted(matched_skus)) if matched_any else ""
        })

    final = pd.DataFrame(results)
    for c in ["Nard Quantity", "Shopify Quantity", "Quantity Difference"]:
        final[c] = final[c].apply(format_qty)

    if separate_mismatches_only:
        display_df = final[final['Sku Flag'] != 'Synced'].copy()
    else:
        display_df = final.copy()

    # Summary
    st.header("Report — preview")
    total = len(final)
    mismatches = len(final[final['Sku Flag'] != 'Synced'])
    synced = len(final[final['Sku Flag'] == 'Synced'])
    c1, c2, c3 = st.columns(3)
    c1.metric("Total items (Nard)", total)
    c2.metric("Mismatches / special", mismatches)
    c3.metric("Synced", synced)

    st.dataframe(display_df.head(500), use_container_width=True)

    # Downloads
    st.markdown("---")
    st.header("Download")
    csv_bytes = final.to_csv(index=False).encode('utf-8')
    excel_bytes = to_excel_bytes(final)

    st.download_button("Download full report — CSV", data=csv_bytes,
                       file_name=f"nard_shopify_recon_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv",
                       mime="text/csv")

    st.download_button("Download full report — Excel", data=excel_bytes,
                       file_name=f"nard_shopify_recon_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # mismatches-only export
    mismatched_df = final[final['Sku Flag'] != 'Synced']
    if not mismatched_df.empty:
        m_csv = mismatched_df.to_csv(index=False).encode('utf-8')
        m_xlsx = to_excel_bytes(mismatched_df)
        st.download_button("Download mismatches only — CSV", data=m_csv,
                           file_name=f"nard_shopify_mismatches_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv",
                           mime="text/csv")
        st.download_button("Download mismatches only — Excel", data=m_xlsx,
                           file_name=f"nard_shopify_mismatches_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    st.markdown("""
    **Notes & assumptions**
    - Matching flow: **exact barcode** match first (normalized lowercase/trim) → **digits-only** fallback if no exact match.  
    - If multiple Shopify rows share the same SKU, their quantities are summed before matching.  
    - Product name and Sale Price come from Nard only.  
    - `Sku Flag` is unified and includes high/low risk labels when applicable.
    """)
