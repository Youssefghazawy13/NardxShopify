# Nard–Shopify Inventory Reconciliation App

This Streamlit app compares inventory between:
- Nard POS (Excel sheet)
- Shopify inventory export (CSV)

It generates a 7-column reconciliation report:
1. Product name  
2. Barcodes  
3. Nard Quantity  
4. Shopify Quantity  
5. Quantity Difference  
6. Sale Price  
7. Sku Flag  
8. Risk Level

### Flags Logic:
- Dead item  
- Missing in Shopify  
- Shopify more (High/Low Risk)  
- Nard more (High/Low Risk)  
- Synced  

### Risk Level:
- High Risk: |diff| ≥ 5 or Dead/Missing  
- Low Risk: |diff| < 5  
- Synced: N/A  

### How to run:
1. Upload Nard Excel sheet  
2. Upload Shopify CSV  
3. Generate the report  

Deploy on Streamlit Cloud easily by pointing to `streamlit_app.py`.
