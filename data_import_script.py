import pandas as pd
from supabase_client import supabase 
from datetime import datetime, date
import numpy as np
import re

# --- KONFIGURASI ---
DEFAULT_YEAR = 2025
DEFAULT_MONTH = 11

# ✅ MAPPING ID PRODUK
PRODUCT_CODE_TO_ID = {
    "A01": 1, "A02": 2, 
    "B01": 3, "B02": 4, 
    "C01": 5, "C02": 6,
}

# --- HELPER FUNCTIONS ---
def normalize_code(code):
    """Membersihkan Kode Akun dari spasi, non-breaking space, dan karakter aneh."""
    if not isinstance(code, str): return str(code)
    return re.sub(r'[^\d-]', '', code)

def clean_rupiah_number_element(val):
    """Membersihkan format Rupiah (13.600.000) menjadi float."""
    val = str(val).strip()
    if not val or val.lower() == 'nan': return 0.0
    val = val.replace('Rp', '').replace(' ', '').replace('(', '').replace(')', '').replace('\xa0', '')
    val = val.replace('.', '').replace(',', '.')
    try:
        return float(val)
    except ValueError:
        return 0.0

# --- 1. FUNGSI PEMBERSIHAN DATA ---
def clear_all_data():
    print("\n--- Membersihkan Data Database ---")
    tables = ["inventory_movements", "journal_lines", "order_items", "journal_entries", "orders", "products", "chart_of_accounts"]
    for table in tables:
        print(f"Membersihkan {table}...")
        try:
            if table == 'chart_of_accounts':
                supabase.table(table).delete().neq("account_code", "DUMMY").execute()
            else:
                supabase.table(table).delete().neq("id", 0).execute()
        except Exception as e:
            pass
    print("Selesai membersihkan.")

# --- 2. SEEDING PRODUCTS (WAJIB UNTUK INVENTORY) ---
def seed_products():
    print("\n--- Seeding Products (Membuat Ulang Produk) ---")
    # Membuat produk placeholder agar inventory movements bisa masuk
    # Asumsi akun standar: Persediaan (1-1200), HPP (5-1100)
    products_data = [
        {"id": 1, "name": "Bibit 2\" (A01)", "price": 0, "cost_price": 0, "inventory_account_code": "1-1200", "hpp_account_code": "5-1100"},
        {"id": 2, "name": "Bibit 3\" (A02)", "price": 0, "cost_price": 0, "inventory_account_code": "1-1200", "hpp_account_code": "5-1100"},
        {"id": 3, "name": "Betina 2\" (B01)", "price": 0, "cost_price": 0, "inventory_account_code": "1-1200", "hpp_account_code": "5-1100"},
        {"id": 4, "name": "Betina 3\" (B02)", "price": 0, "cost_price": 0, "inventory_account_code": "1-1200", "hpp_account_code": "5-1100"},
        {"id": 5, "name": "Jantan 5\" (C01)", "price": 0, "cost_price": 0, "inventory_account_code": "1-1200", "hpp_account_code": "5-1100"},
        {"id": 6, "name": "Jantan 6\" (C02)", "price": 0, "cost_price": 0, "inventory_account_code": "1-1200", "hpp_account_code": "5-1100"},
    ]
    
    try:
        supabase.table("products").upsert(products_data).execute()
        print(f"Berhasil membuat ulang {len(products_data)} produk.")
    except Exception as e:
        print(f"Gagal seeding produk: {e}")
        print("Pastikan tabel 'products' memiliki kolom: id, name, price, cost_price, inventory_account_code, hpp_account_code")

# --- 3. IMPORT COA ---
def infer_coa_details(account_code):
    prefix = str(account_code).split('-')[0]
    if prefix == '1': return "Asset", "Debit"
    elif prefix == '2': return "Liability", "Credit"
    elif prefix == '3': return "Equity", "Credit" if account_code.strip() != '3-1200' else "Debit"
    elif prefix in ['4', '8']: return "Revenue", "Credit"
    return "Expense", "Debit"

def import_coa(file_path):
    print(f"\n--- Import COA: {file_path} ---")
    try:
        df = pd.read_csv(file_path, header=4, usecols=[0, 1], names=['account_code', 'account_name'], 
                         skiprows=lambda x: x < 5 and x != 4, delimiter=';', dtype=str)
        df = df.dropna(subset=['account_code']).copy()
        df['account_code'] = df['account_code'].apply(normalize_code)
        df['account_name'] = df['account_name'].astype(str).str.strip()
        df = df[df['account_code'].str.contains(r'^\d-\d{4}$', regex=True)].copy()
        
        df['account_type'], df['normal_balance'] = zip(*df['account_code'].apply(infer_coa_details))
        data = df[['account_code', 'account_name', 'account_type', 'normal_balance']].to_dict('records')
        
        if data:
            supabase.table("chart_of_accounts").insert(data).execute()
            print(f"Berhasil mengimpor {len(data)} akun.")
            
    except Exception as e:
        print(f"Gagal import COA: {e}")

# --- 4. IMPORT JURNAL UMUM (GJ) ---
def import_general_journal(file_path):
    print(f"\n--- Import GJ: {file_path} ---")
    try:
        valid_accounts_resp = supabase.table("chart_of_accounts").select("account_code").execute()
        valid_account_codes = set(item['account_code'] for item in valid_accounts_resp.data)
        
        df_raw = pd.read_csv(file_path, header=5, delimiter=';', engine='python', dtype=str)
        df = df_raw.iloc[:, [1, 2, 3, 4, 5]].copy() 
        df.columns = ['Day', 'Description', 'REF', 'DEBET', 'CREDIT']
        df['Day'] = df['Day'].ffill()
        df['Description'] = df['Description'].ffill()
        df = df.dropna(subset=['REF']).copy()
        
        df['REF'] = df['REF'].apply(normalize_code)
        df['DEBET'] = df['DEBET'].apply(clean_rupiah_number_element)
        df['CREDIT'] = df['CREDIT'].apply(clean_rupiah_number_element)
        
        def parse_day(d):
            try: return date(DEFAULT_YEAR, DEFAULT_MONTH, int(float(d))).isoformat()
            except: return date(DEFAULT_YEAR, DEFAULT_MONTH, 1).isoformat()
        df['transaction_date'] = df['Day'].apply(parse_day)
        
        grouped = df.groupby(['transaction_date', 'Description'])
        entries_count = 0
        lines_count = 0
        
        for (tx_date, desc), group in grouped:
            if group['DEBET'].sum() == 0 and group['CREDIT'].sum() == 0: continue
            
            entry = supabase.table("journal_entries").insert({"transaction_date": tx_date, "description": desc}).execute().data[0]
            journal_id = entry['id']
            entries_count += 1
            
            lines = []
            for _, row in group.iterrows():
                if row['REF'] in valid_account_codes and (row['DEBET'] > 0 or row['CREDIT'] > 0):
                    lines.append({
                        "journal_id": journal_id, "account_code": row['REF'],
                        "debit_amount": float(row['DEBET']), "credit_amount": float(row['CREDIT'])
                    })
            if lines:
                supabase.table("journal_lines").insert(lines).execute()
                lines_count += len(lines)
                
        print(f"Berhasil mengimpor {entries_count} transaksi jurnal dan {lines_count} baris detail.")
        
    except Exception as e:
        print(f"Gagal import GJ: {e}")

# --- 5. IMPORT INVENTORY ---
def import_inventory_movements(file_path):
    print(f"\n--- Import Inventory: {file_path} ---")
    try:
        df_raw = pd.read_csv(file_path, header=None, skiprows=5, delimiter=';', dtype=str)
        movements = []
        current_prod_id = None
        
        for idx, row in df_raw.iterrows():
            row_str = row.fillna('').astype(str).str.strip().tolist()
            full_text = " ".join(row_str)
            
            if 'ITEM' in full_text:
                for code, pid in PRODUCT_CODE_TO_ID.items():
                    if code in row_str: 
                        current_prod_id = pid
                        break
                continue
            
            if current_prod_id and row_str[2].replace('.','',1).isdigit():
                try:
                    tx_date = date(DEFAULT_YEAR, DEFAULT_MONTH, int(float(row_str[2]))).isoformat()
                    qty_in = pd.to_numeric(row_str[4], errors='coerce') or 0
                    cost_in = clean_rupiah_number_element(row_str[5])
                    qty_out = pd.to_numeric(row_str[7], errors='coerce') or 0 
                    cost_out = clean_rupiah_number_element(row_str[8])
                    
                    if qty_in > 0:
                        movements.append({"product_id": int(current_prod_id), "movement_date": tx_date, "movement_type": "RECEIPT", "quantity_change": int(qty_in), "unit_cost": float(cost_in), "reference_id": f"IMP-IN-{idx}"})
                    if qty_out > 0:
                        movements.append({"product_id": int(current_prod_id), "movement_date": tx_date, "movement_type": "ISSUE", "quantity_change": int(-qty_out), "unit_cost": float(cost_out), "reference_id": f"IMP-OUT-{idx}"})
                except: continue

        if movements:
            # Batch insert
            for i in range(0, len(movements), 100):
                supabase.table("inventory_movements").insert(movements[i:i+100]).execute()
            print(f"Berhasil mengimpor {len(movements)} pergerakan inventori.")
        else:
            print("Tidak ada data inventori valid yang ditemukan.")
            
    except Exception as e:
        print(f"Gagal import Inventory: {e}")

# --- MAIN ---
if __name__ == "__main__":
    clear_all_data()
    import_coa("SIKLUS EXCEL.xlsx - AKUN.csv")
    seed_products() # <--- LANGKAH BARU YANG KRUSIAL
    import_general_journal("SIKLUS EXCEL.xlsx - GJ.csv")
    import_inventory_movements("SIKLUS EXCEL.xlsx - INVENTORY.csv")
    print("\n*** SELESAI ***")
