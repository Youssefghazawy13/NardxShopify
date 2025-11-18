# streamlit_app.py
import streamlit as st
import pandas as pd
import io
from datetime import datetime

st.set_page_config(page_title="Nard ⇄ Shopify Reconciler", layout="wide")

st.title("Slot-X (Nard ⇄ Shopify) — Strict Barcode Reconciler")
st.caption("Matches Nard (POS) ↔ Shopify by BARCODE only. Product name & prices come from Nard.")

# --------------------
# Helpers
# --------------------
COMMON_SHOP_SKU = ['sku', 'variant.sku', 'variant_sku', 'barcode', 'variant.barcode', 'handle_sku']
COMMON_SHOP_QTY = ['on hand (new)', 'on hand', 'inventory_quantity', 'available', 'quantity', 'on_hand', 'inventory']
ENCODINGS = ['utf-8', 'latin1', 'cp1252']

def pick_col(cols, candidates):
    cols_map = {c.lower(): c for c in cols}
    # exact lower-case match first
    for cand in candidates:
        if cand.lower() in cols_map:
            return cols_map[cand.lower()]
    # partial-match fallback
    for c in cols:
        for cand in candidates:
            if cand.lower() in c.lower():
                return c
    return None

def read_any_csv_like(uploaded_file):
    # try common encodings
    for enc in ENCODINGS:
        try:
            return pd.read_csv(uploaded_file, dtype=str, encoding=enc, low_memory=False)
        except Exception:
            uploaded_file.seek(0)
            continue
    # as final resort, let pandas guess
    uploaded_file.seek(0)
    return pd.read_csv(uploaded_file, dtype=str, low_memory=False)

def split_barcodes(cell):
    if pd.isna(cell) or str(cell).strip() == '':
        return []
    s = str(cell)
    # normalize separators ; | / whitespace , etc -> comma
    for sep in [';', '|', '/', '\\']:
        s = s.replace(sep, ',')
    parts = [p.strip().lower() for p in s.split(',') if p.strip() != '']
    return parts

def format_qty(x):
    try:
        fx = float(x)
        return int(fx) if fx.is_integer() else fx
    except Exception:
        return 0

def compute_flag(n_qty, s_qty, matched_any, diff_threshold=5):
    # Priority rules (single flag returned)
    if not matched_any:
        return "Missing in Shopify"
    if float(n_qty) == 0 and float(s_qty) > 0:
        return "Dead item"
    diff = float(s_qty) - float(n_qty)
    if diff > 0:
        # Shopify more
        if abs(diff) >= diff_threshold:
            return f"Shopify more (High Risk | diff={int(diff) if float(diff).is_integer() else diff})"
        return f"Shopify more (Low Risk | diff={int(diff) if float(diff).is_integer() else diff})"
    if diff < 0:
        # Nard more
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
    shop_file = st.file_uploader("Upload Shopify (CSV) — contains SKU column and qty column (we auto-detect)", type=['csv', 'xls', 'xlsx'])

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
# Process button
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

    # Validate required columns in Nard
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
        # use first barcode only
        nard['Barcodes_list'] = nard['Barcodes_raw'].apply(lambda s: [split_barcodes(s)[0]] if len(split_barcodes(s))>0 else [])

    # numeric conversions
    nard['Nard Quantity'] = pd.to_numeric(nard['available_quantity'], errors='coerce').fillna(0).astype(float)
    nard['Sale Price'] = pd.to_numeric(nard['sale_price'], errors='coerce')

    # Read Shopify
    # allow either CSV or Excel
    shop_df = None
    if str(shop_file.name).lower().endswith('.csv'):
        shop_df = read_any_csv_like(shop_file)
    else:
        try:
            shop_df = pd.read_excel(shop_file, sheet_name=0, dtype=str)
        except Exception:
            # fallback: try CSV read
            shop_file.seek(0)
            shop_df = read_any_csv_like(shop_file)

    shop_cols = list(shop_df.columns)

    # Detect sku and qty columns
    sku_col = pick_col(shop_cols, COMMON_SHOP_SKU)
    qty_col = pick_col(shop_cols, COMMON_SHOP_QTY)

    if sku_col is None:
        st.error(f"Could not detect SKU column in Shopify file. Shopify columns: {shop_cols}")
        st.stop()

    # normalize shop sku -> key and qty
    shop_df['sku_key'] = shop_df[sku_col].astype(str).str.lower().str.strip()
    if qty_col:
        shop_df['qty_num'] = pd.to_numeric(shop_df[qty_col].fillna('0'), errors='coerce').fillna(0).astype(float)
    else:
        shop_df['qty_num'] = 0.0

    # Aggregate Shopify by sku_key (sum across duplicates)
    shop_agg = shop_df.groupby('sku_key', dropna=False)['qty_num'].sum().reset_index()
    shop_map = dict(zip(shop_agg['sku_key'], shop_agg['qty_num']))

    # Prepare result container
    results = []
    for idx, row in nard.iterrows():
        product_name = row['Product name']
        barcodes_list = row['Barcodes_list'] if isinstance(row['Barcodes_list'], list) else []
        n_qty = float(row['Nard Quantity'])
        sale_price = row['Sale Price'] if not pd.isna(row['Sale Price']) else None

        # strict barcode-only matching: check each barcode (lowercased)
        matched_skus = []
        shop_total_qty = 0.0
        for bc in barcodes_list:
            if not bc:
                continue
            # try both exact bc and bc stripped of leading zeros (common issues)
            key = bc.lower().strip()
            if key in shop_map:
                matched_skus.append(key)
                shop_total_qty += float(shop_map[key])
            else:
                # try without leading zeros
                alt = key.lstrip('0')
                if alt in shop_map and alt not in matched_skus:
                    matched_skus.append(alt)
                    shop_total_qty += float(shop_map[alt])

        matched_any = len(matched_skus) > 0
        s_qty = float(shop_total_qty) if matched_any else 0.0
        diff = s_qty - n_qty

        # flag
        flag = compute_flag(n_qty, s_qty, matched_any, diff_threshold=threshold)

        results.append({
            "Product name": product_name,
            "Barcodes": row['Barcodes_raw'],
            "Nard Quantity": n_qty,
            "Shopify Quantity": s_qty,
            "Quantity Difference": diff,
            "Sale Price": sale_price,
            "Sku Flag": flag,
            "Matched Shop SKUs": ",".join(matched_skus) if matched_any else ""
        })

    final = pd.DataFrame(results)

    # Format numeric columns (display integers when whole)
    for c in ["Nard Quantity", "Shopify Quantity", "Quantity Difference"]:
        final[c] = final[c].apply(format_qty)

    # Optionally filter out Synced
    if separate_mismatches_only:
        display_df = final[final['Sku Flag'] != 'Synced'].copy()
    else:
        display_df = final.copy()

    # Show summary metrics
    st.header("Report — preview")
    total = len(final)
    mismatches = len(final[final['Sku Flag'] != 'Synced'])
    synced = len(final[final['Sku Flag'] == 'Synced'])
    c1, c2, c3 = st.columns(3)
    c1.metric("Total items (Nard)", total)
    c2.metric("Mismatches / special", mismatches)
    c3.metric("Synced", synced)

    # Dataframe preview
    st.dataframe(display_df.head(500), use_container_width=True)

    # Download options
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

    # Also offer a compact mismatches-only export
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

    # show a couple of helpful notes
    st.markdown("""
    **Notes & assumptions**
    - Matching is *strict barcode-only* (Nard `barcodes` ↔ Shopify `sku`).  
    - If a Nard cell contains multiple barcodes (`,` `;` `|`), we try any of them (or first one if you selected that option).  
    - Shopify SKU aggregation: if same SKU appears multiple times in Shopify export, quantities are summed.  
    - Product name and Sale Price are taken from Nard only.
    - `Sku Flag` is unified and includes high/low risk labels when applicable.
    """)
