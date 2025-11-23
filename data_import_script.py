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

# --- 1. FUNGSI PEMBERSIHAN DATA (FIXED) ---
def clear_all_data():
    print("\n--- Membersihkan Data Database ---")
    # Urutan penghapusan dari anak ke induk
    tables = ["inventory_movements", "journal_lines", "order_items", "journal_entries", "orders", "products", "chart_of_accounts"]
    
    for table in tables:
        print(f"Membersihkan {table}...")
        try:
            # Logika pembedaan kolom kunci
            if table == 'chart_of_accounts':
                # COA menggunakan account_code, bukan id
                supabase.table(table).delete().neq("account_code", "DUMMY").execute()
            else:
                # Tabel lain menggunakan id
                supabase.table(table).delete().neq("id", 0).execute()
        except Exception as e:
            print(f"Gagal membersihkan {table}: {e}")
            # Lanjut ke tabel berikutnya meskipun satu gagal (opsional, tapi lebih aman berhenti jika fatal)
            raise e 
            
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
        df = pd.read_csv(file_path, header=4, usecols=[0, 1], names=['account_code', 'account_name'], skiprows=lambda x: x < 5 and x != 4, delimiter=';')
        df = df.dropna(subset=['account_code']).copy()
        df['account_code'] = df['account_code'].astype(str).str.strip()
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
        df_raw = pd.read_csv(file_path, header=5, delimiter=';', engine='python')
        
        # Ambil kolom 1, 2, 3, 4, 5 (Day, Desc, Ref, Debet, Credit)
        df = df_raw.iloc[:, [1, 2, 3, 4, 5]].copy() 
        df.columns = ['Day', 'Description', 'REF', 'DEBET', 'CREDIT']
        
        df['Day'] = df['Day'].ffill()
        df['Description'] = df['Description'].ffill()
        
        df = df.dropna(subset=['REF']).copy()
        
        df['DEBET'] = df['DEBET'].apply(clean_rupiah_number_element)
        df['CREDIT'] = df['CREDIT'].apply(clean_rupiah_number_element)
        
        def parse_day(d):
            try:
                d_int = int(float(d))
                return date(DEFAULT_YEAR, DEFAULT_MONTH, d_int).isoformat()
            except:
                return date(DEFAULT_YEAR, DEFAULT_MONTH, 1).isoformat()
                
        df['transaction_date'] = df['Day'].apply(parse_day)
        
        grouped = df.groupby(['transaction_date', 'Description'])
        
        entries_count = 0
        lines_count = 0
        
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
                if row['DEBET'] > 0 or row['CREDIT'] > 0:
                    lines.append({
                        "journal_id": journal_id,
                        "account_code": row['REF'].strip(),
                        "debit_amount": row['DEBET'],
                        "credit_amount": row['CREDIT']
                    })
            
            if lines:
                supabase.table("journal_lines").insert(lines).execute()
                lines_count += len(lines)
                
        print(f"Berhasil mengimpor {entries_count} transaksi jurnal dan {lines_count} baris detail.")
        
    except Exception as e:
        print(f"Gagal import GJ: {e}")

# --- 4. IMPORT INVENTORY ---
def import_inventory_movements(file_path):
    print(f"\n--- Import Inventory: {file_path} ---")
    if not PRODUCT_CODE_TO_ID:
        print("Mapping ID Produk kosong.")
        return

    try:
        df_raw = pd.read_csv(file_path, header=None, skiprows=5, delimiter=';')
        movements = []
        
        for idx, row in df_raw.iterrows():
            row_str = row.astype(str)
            
            # Cari Kode Produk
            current_prod_id = None
            if 'ITEM' in row_str.iloc[1]: 
                for i, val in enumerate(row_str):
                    if "Kode" in val:
                        code_cand = row_str.iloc[i+1].strip()
                        current_prod_id = PRODUCT_CODE_TO_ID.get(code_cand)
                        break
                # Simpan state produk untuk baris berikutnya jika struktur memungkinkan, 
                # TAPI struktur file ini sepertinya per-block. 
                # Kode di atas hanya mendeteksi baris header.
                # Kita butuh state machine sederhana.
                continue 
            
            # Logic state machine sederhana:
            # Jika baris ini bukan header item, kita perlu tahu produk apa yang sedang diproses.
            # Karena iterasi baris demi baris, kita harus menyimpan current_prod_id di luar loop atau mendeteksi ulang.
            # KOREKSI: Menggunakan variabel state di luar loop.
            pass # Placeholder, lihat implementasi bawah.

        # IMPLEMENTASI ULANG LOOP DENGAN STATE
        current_active_prod_id = None
        
        for idx, row in df_raw.iterrows():
            row_str = row.astype(str)
            
            # 1. Deteksi Header Produk Baru
            if 'ITEM' in str(row.values):
                for i, val in enumerate(row_str):
                    if "Kode" in str(val):
                        try:
                            code_cand = row_str.iloc[i+1].strip()
                            current_active_prod_id = PRODUCT_CODE_TO_ID.get(code_cand)
                        except:
                            pass
                continue # Pindah ke baris berikutnya
            
            # 2. Proses Data Transaksi (jika ada produk aktif)
            # Cek kolom Day (index 2)
            day_val = str(row_str.iloc[2]).strip()
            
            if current_active_prod_id and day_val.replace('.','',1).isdigit(): 
                try:
                    day = int(float(day_val))
                    tx_date = date(DEFAULT_YEAR, DEFAULT_MONTH, day).isoformat()
                    
                    # Parsing Angka
                    qty_in = pd.to_numeric(row_str.iloc[4], errors='coerce') or 0
                    cost_in = clean_rupiah_number_element(row_str.iloc[5])
                    qty_out = pd.to_numeric(row_str.iloc[7], errors='coerce') or 0 
                    cost_out = clean_rupiah_number_element(row_str.iloc[8]) 
                    
                    if qty_in > 0:
                        movements.append({
                            "product_id": current_active_prod_id, "movement_date": tx_date,
                            "movement_type": "RECEIPT", "quantity_change": qty_in,
                            "unit_cost": cost_in, "reference_id": f"IMP-IN-{idx}"
                        })
                    
                    if qty_out > 0:
                        movements.append({
                            "product_id": current_active_prod_id, "movement_date": tx_date,
                            "movement_type": "ISSUE", "quantity_change": -qty_out,
                            "unit_cost": cost_out, "reference_id": f"IMP-OUT-{idx}"
                        })
                except:
                    continue

        if movements:
            supabase.table("inventory_movements").insert(movements).execute()
            print(f"Berhasil mengimpor {len(movements)} pergerakan inventori.")
        else:
            print("Tidak ada data inventori valid yang ditemukan.")
            
    except Exception as e:
        print(f"Gagal import Inventory: {e}")

# --- MAIN ---
if __name__ == "__main__":
    # Jalankan urutan
    try:
        clear_all_data()
        import_coa("SIKLUS EXCEL.xlsx - AKUN.csv")
        import_general_journal("SIKLUS EXCEL.xlsx - GJ.csv")
        import_inventory_movements("SIKLUS EXCEL.xlsx - INVENTORY.csv")
        print("\n*** DATA SEEDING SELESAI ***")
    except Exception as main_e:
        print(f"\nTERJADI ERROR UTAMA: {main_e}")
