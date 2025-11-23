import pandas as pd
from supabase_client import supabase 
from datetime import datetime, date
import numpy as np

# --- KONFIGURASI ---
DEFAULT_YEAR = 2025
DEFAULT_MONTH = 11

# ✅ MAPPING ID PRODUK
PRODUCT_CODE_TO_ID = {
    "A01": 1, 
    "A02": 2, 
    "B01": 3, 
    "B02": 4, 
    "C01": 5, 
    "C02": 6,
}

# --- FUNGSI PEMBERSIH ANGKA ---
def clean_rupiah_number_element(val):
    """Membersihkan format Rupiah (13.600.000) menjadi float."""
    val = str(val).strip()
    if not val or val.lower() == 'nan': return 0.0
    
    # Hapus karakter non-numerik
    val = val.replace('Rp', '').replace(' ', '').replace('(', '').replace(')', '')
    # Hapus titik ribuan
    val = val.replace('.', '')
    # Ganti koma desimal
    val = val.replace(',', '.')
    
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
            print(f"Info: Gagal membersihkan {table} (mungkin sudah kosong): {e}")
    print("Selesai membersihkan.")

# --- 2. IMPORT COA ---
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
        # Pakai dtype=str untuk memastikan kode akun dibaca sebagai text
        df = pd.read_csv(file_path, header=4, usecols=[0, 1], names=['account_code', 'account_name'], 
                         skiprows=lambda x: x < 5 and x != 4, delimiter=';', dtype=str)
        
        df = df.dropna(subset=['account_code']).copy()
        df['account_code'] = df['account_code'].astype(str).str.strip()
        df['account_name'] = df['account_name'].astype(str).str.strip()
        
        # Filter hanya yang punya tanda '-' (format standar akun Anda)
        df = df[df['account_code'].str.contains(r'-')].copy()
        
        df['account_type'], df['normal_balance'] = zip(*df['account_code'].apply(infer_coa_details))
        data = df[['account_code', 'account_name', 'account_type', 'normal_balance']].to_dict('records')
        
        if data:
            supabase.table("chart_of_accounts").insert(data).execute()
            print(f"Berhasil mengimpor {len(data)} akun.")
        else:
            print("Tidak ada data akun yang ditemukan.")
    except Exception as e:
        print(f"Gagal import COA: {e}")

# --- 3. IMPORT JURNAL UMUM (GJ) ---
def import_general_journal(file_path):
    print(f"\n--- Import GJ: {file_path} ---")
    try:
        # Ambil daftar akun valid dari database untuk validasi
        valid_accounts_resp = supabase.table("chart_of_accounts").select("account_code").execute()
        valid_account_codes = set(item['account_code'] for item in valid_accounts_resp.data)
        print(f"Validasi: Ditemukan {len(valid_account_codes)} akun valid di database.")

        df_raw = pd.read_csv(file_path, header=5, delimiter=';', engine='python', dtype=str)
        
        # Ambil kolom 1, 2, 3, 4, 5 (Day, Desc, Ref, Debet, Credit)
        df = df_raw.iloc[:, [1, 2, 3, 4, 5]].copy() 
        df.columns = ['Day', 'Description', 'REF', 'DEBET', 'CREDIT']
        
        df['Day'] = df['Day'].ffill()
        df['Description'] = df['Description'].ffill()
        
        df = df.dropna(subset=['REF']).copy()
        df['REF'] = df['REF'].astype(str).str.strip()
        
        # Bersihkan Angka
        df['DEBET'] = df['DEBET'].apply(clean_rupiah_number_element)
        df['CREDIT'] = df['CREDIT'].apply(clean_rupiah_number_element)
        
        def parse_day(d):
            try:
                d_clean = str(d).split('.')[0] # Handle "1.0" string from excel
                d_int = int(d_clean)
                return date(DEFAULT_YEAR, DEFAULT_MONTH, d_int).isoformat()
            except:
                return date(DEFAULT_YEAR, DEFAULT_MONTH, 1).isoformat()
                
        df['transaction_date'] = df['Day'].apply(parse_day)
        
        grouped = df.groupby(['transaction_date', 'Description'])
        
        entries_count = 0
        lines_count = 0
        skipped_lines = 0
        
        for (tx_date, desc), group in grouped:
            if group['DEBET'].sum() == 0 and group['CREDIT'].sum() == 0:
                continue
            
            # Buat Header
            entry = supabase.table("journal_entries").insert({
                "transaction_date": tx_date,
                "description": desc
            }).execute().data[0]
            journal_id = entry['id']
            entries_count += 1
            
            lines = []
            for _, row in group.iterrows():
                acc_code = row['REF']
                
                # CEK VALIDITAS AKUN
                if acc_code not in valid_account_codes:
                    print(f"WARNING: Akun {acc_code} tidak ditemukan di COA. Baris dilewati.")
                    skipped_lines += 1
                    continue

                if row['DEBET'] > 0 or row['CREDIT'] > 0:
                    lines.append({
                        "journal_id": journal_id,
                        "account_code": acc_code,
                        "debit_amount": float(row['DEBET']),
                        "credit_amount": float(row['CREDIT'])
                    })
            
            if lines:
                supabase.table("journal_lines").insert(lines).execute()
                lines_count += len(lines)
                
        print(f"Berhasil mengimpor {entries_count} transaksi jurnal dan {lines_count} baris detail.")
        if skipped_lines > 0:
            print(f"PERHATIAN: {skipped_lines} baris dilewati karena Kode Akun tidak ditemukan di database.")
        
    except Exception as e:
        print(f"Gagal import GJ: {e}")

# --- 4. IMPORT INVENTORY ---
def import_inventory_movements(file_path):
    print(f"\n--- Import Inventory: {file_path} ---")
    if not PRODUCT_CODE_TO_ID:
        print("Mapping ID Produk kosong.")
        return

    try:
        df_raw = pd.read_csv(file_path, header=None, skiprows=5, delimiter=';', dtype=str)
        movements = []
        
        # Loop manual sederhana
        current_prod_id = None
        
        for idx, row in df_raw.iterrows():
            row_str = row.fillna('').astype(str)
            
            # Deteksi Header Item (Kode)
            if 'Kode' in row_str.iloc[13]: # Cek kolom N (index 13)
                 code_cand = row_str.iloc[14].strip()
                 current_prod_id = PRODUCT_CODE_TO_ID.get(code_cand)
                 continue
            
            # Validasi baris data: Kolom 2 (Day) adalah angka
            day_val = row_str.iloc[2].strip()
            
            if current_prod_id and day_val.replace('.','',1).isdigit():
                try:
                    day = int(float(day_val))
                    tx_date = date(DEFAULT_YEAR, DEFAULT_MONTH, day).isoformat()
                    
                    qty_in = pd.to_numeric(row_str.iloc[4], errors='coerce') or 0
                    cost_in = clean_rupiah_number_element(row_str.iloc[5])
                    qty_out = pd.to_numeric(row_str.iloc[7], errors='coerce') or 0 
                    cost_out = clean_rupiah_number_element(row_str.iloc[8])
                    
                    # PENTING: Konversi ke tipe native Python (int/float) untuk JSON Serialization
                    if qty_in > 0:
                        movements.append({
                            "product_id": int(current_prod_id),
                            "movement_date": tx_date,
                            "movement_type": "RECEIPT", 
                            "quantity_change": int(qty_in),
                            "unit_cost": float(cost_in),
                            "reference_id": f"IMP-IN-{idx}"
                        })
                    
                    if qty_out > 0:
                        movements.append({
                            "product_id": int(current_prod_id),
                            "movement_date": tx_date,
                            "movement_type": "ISSUE", 
                            "quantity_change": int(-qty_out), # Negatif
                            "unit_cost": float(cost_out),
                            "reference_id": f"IMP-OUT-{idx}"
                        })
                except Exception as e:
                    # print(f"Skip row {idx}: {e}")
                    continue

        if movements:
            supabase.table("inventory_movements").insert(movements).execute()
            print(f"Berhasil mengimpor {len(movements)} pergerakan inventori.")
        else:
            print("Tidak ada data inventori yang ditemukan.")
            
    except Exception as e:
        print(f"Gagal import Inventory: {e}")

# --- MAIN ---
if __name__ == "__main__":
    clear_all_data()
    import_coa("SIKLUS EXCEL.xlsx - AKUN.csv")
    import_general_journal("SIKLUS EXCEL.xlsx - GJ.csv")
    import_inventory_movements("SIKLUS EXCEL.xlsx - INVENTORY.csv")
    print("\n*** SELESAI ***")
