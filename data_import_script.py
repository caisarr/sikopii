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
    # Hapus semua karakter yang bukan angka atau tanda strip (-)
    # Ini akan menghapus spasi, \xa0, tab, dll.
    clean = re.sub(r'[^\d-]', '', code)
    return clean

def clean_rupiah_number_element(val):
    """Membersihkan format Rupiah (13.600.000) menjadi float."""
    val = str(val).strip()
    if not val or val.lower() == 'nan': return 0.0
    
    # Hapus karakter non-numerik umum
    val = val.replace('Rp', '').replace(' ', '').replace('(', '').replace(')', '')
    # Hapus non-breaking space jika ada
    val = val.replace('\xa0', '')
    
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
            # Abaikan error jika tabel sudah kosong
            pass
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
        # Baca semua sebagai string dulu
        df = pd.read_csv(file_path, header=4, usecols=[0, 1], names=['account_code', 'account_name'], 
                         skiprows=lambda x: x < 5 and x != 4, delimiter=';', dtype=str)
        
        df = df.dropna(subset=['account_code']).copy()
        
        # NORMALISASI KODE AKUN (CRITICAL FIX)
        df['account_code'] = df['account_code'].apply(normalize_code)
        df['account_name'] = df['account_name'].astype(str).str.strip()
        
        # Filter format standar (X-XXXX)
        df = df[df['account_code'].str.contains(r'^\d-\d{4}$', regex=True)].copy()
        
        df['account_type'], df['normal_balance'] = zip(*df['account_code'].apply(infer_coa_details))
        data = df[['account_code', 'account_name', 'account_type', 'normal_balance']].to_dict('records')
        
        if data:
            supabase.table("chart_of_accounts").insert(data).execute()
            print(f"Berhasil mengimpor {len(data)} akun.")
        else:
            print("Tidak ada data akun yang ditemukan setelah normalisasi.")
            
    except Exception as e:
        print(f"Gagal import COA: {e}")

# --- 3. IMPORT JURNAL UMUM (GJ) ---
def import_general_journal(file_path):
    print(f"\n--- Import GJ: {file_path} ---")
    try:
        # Ambil daftar akun valid dari database untuk validasi
        valid_accounts_resp = supabase.table("chart_of_accounts").select("account_code").execute()
        valid_account_codes = set(item['account_code'] for item in valid_accounts_resp.data)
        print(f"Info: Database memiliki {len(valid_account_codes)} akun valid.")

        df_raw = pd.read_csv(file_path, header=5, delimiter=';', engine='python', dtype=str)
        
        # Ambil kolom Day, Desc, Ref, Debet, Credit
        df = df_raw.iloc[:, [1, 2, 3, 4, 5]].copy() 
        df.columns = ['Day', 'Description', 'REF', 'DEBET', 'CREDIT']
        
        df['Day'] = df['Day'].ffill()
        df['Description'] = df['Description'].ffill()
        
        df = df.dropna(subset=['REF']).copy()
        
        # NORMALISASI KODE AKUN DI JURNAL (CRITICAL FIX)
        df['REF'] = df['REF'].apply(normalize_code)
        
        # Bersihkan Angka
        df['DEBET'] = df['DEBET'].apply(clean_rupiah_number_element)
        df['CREDIT'] = df['CREDIT'].apply(clean_rupiah_number_element)
        
        def parse_day(d):
            try:
                d_clean = str(d).split('.')[0]
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
            
            entry = supabase.table("journal_entries").insert({
                "transaction_date": tx_date,
                "description": desc
            }).execute().data[0]
            journal_id = entry['id']
            entries_count += 1
            
            lines = []
            for _, row in group.iterrows():
                acc_code = row['REF']
                
                if acc_code not in valid_account_codes:
                    # Debug print untuk melihat apa yang salah
                    # print(f"DEBUG SKIP: '{acc_code}' tidak ada di Database.")
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
            print(f"PERHATIAN: {skipped_lines} baris dilewati (Kode Akun mismatch).")
        
    except Exception as e:
        print(f"Gagal import GJ: {e}")

# --- 4. IMPORT INVENTORY ---
def import_inventory_movements(file_path):
    print(f"\n--- Import Inventory: {file_path} ---")
    
    try:
        df_raw = pd.read_csv(file_path, header=None, skiprows=5, delimiter=';', dtype=str)
        movements = []
        current_prod_id = None
        
        # Balik map untuk pencarian string (misal cari "A01" di baris)
        # PRODUCT_CODE_TO_ID = {'A01': 1, ...}
        
        for idx, row in df_raw.iterrows():
            row_str = row.fillna('').astype(str).str.strip().tolist()
            full_row_text = " ".join(row_str)
            
            # 1. Deteksi Produk (Scan seluruh baris untuk Kode Produk yang kita kenal)
            found_new_prod = False
            if 'ITEM' in full_row_text:
                for code, pid in PRODUCT_CODE_TO_ID.items():
                    # Cari kode (misal "A01") di baris ini. Tambah spasi biar gak partial match.
                    if code in row_str: 
                        current_prod_id = pid
                        found_new_prod = True
                        break
                if found_new_prod: continue
            
            # 2. Validasi Baris Data
            # Kolom 2 (Index 2) harus berupa angka tanggal (1-31)
            day_val = row_str[2]
            
            if current_prod_id and day_val.replace('.','',1).isdigit():
                try:
                    day = int(float(day_val))
                    tx_date = date(DEFAULT_YEAR, DEFAULT_MONTH, day).isoformat()
                    
                    # Parsing Angka (Col 4=IN Qty, Col 5=IN Cost, Col 7=OUT Qty, Col 8=OUT Cost)
                    qty_in = pd.to_numeric(row_str[4], errors='coerce') or 0
                    cost_in = clean_rupiah_number_element(row_str[5])
                    qty_out = pd.to_numeric(row_str[7], errors='coerce') or 0 
                    cost_out = clean_rupiah_number_element(row_str[8])
                    
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
                            "quantity_change": int(-qty_out),
                            "unit_cost": float(cost_out),
                            "reference_id": f"IMP-OUT-{idx}"
                        })
                except Exception as e:
                    continue

        if movements:
            # Batch insert untuk performa
            batch_size = 100
            for i in range(0, len(movements), batch_size):
                batch = movements[i:i+batch_size]
                supabase.table("inventory_movements").insert(batch).execute()
            print(f"Berhasil mengimpor {len(movements)} pergerakan inventori.")
        else:
            print("Tidak ada data inventori valid yang ditemukan.")
            
    except Exception as e:
        print(f"Gagal import Inventory: {e}")

# --- MAIN ---
if __name__ == "__main__":
    try:
        clear_all_data()
        import_coa("SIKLUS EXCEL.xlsx - AKUN.csv")
        import_general_journal("SIKLUS EXCEL.xlsx - GJ.csv")
        import_inventory_movements("SIKLUS EXCEL.xlsx - INVENTORY.csv")
        print("\n*** SELESAI ***")
    except Exception as main_e:
        print(f"\nERROR FATAL: {main_e}")
