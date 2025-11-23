import pandas as pd
from supabase_client import supabase 
from datetime import datetime
import numpy as np

# --- KONFIGURASI ---
# Tanggal default jika parsing tanggal gagal (sesuai periode laporan Anda)
DEFAULT_YEAR = 2025
DEFAULT_MONTH = 11

# ✅ MAPPING ID PRODUK (Pastikan ID ini benar dari tabel products Anda)
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
        supabase.table(table).delete().neq("id", 0).execute() # 'id' atau 'account_code' tergantung tabel, tapi neq id 0 biasanya aman untuk id int.
        # Khusus COA, pakai account_code
        if table == 'chart_of_accounts':
             supabase.table(table).delete().neq("account_code", "DUMMY").execute()
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
        
        supabase.table("chart_of_accounts").insert(data).execute()
        print(f"Berhasil mengimpor {len(data)} akun.")
    except Exception as e:
        print(f"Gagal import COA: {e}")

# --- 3. IMPORT JURNAL UMUM (GJ) ---
def import_general_journal(file_path):
    print(f"\n--- Import GJ: {file_path} ---")
    try:
        # Header=5 (Baris 6 Excel). Mengambil kolom Day, Desc, Ref, Debet, Credit
        df_raw = pd.read_csv(file_path, header=5, delimiter=';', engine='python')
        
        # Ambil kolom berdasarkan indeks posisi (1=Day, 2=Desc, 3=Ref, 4=Debet, 5=Credit)
        # Kita asumsikan struktur kolom file tetap.
        df = df_raw.iloc[:, [1, 2, 3, 4, 5]].copy() 
        df.columns = ['Day', 'Description', 'REF', 'DEBET', 'CREDIT']
        
        # Fill down Tanggal dan Deskripsi (untuk baris kredit yang kosong tanggalnya)
        df['Day'] = df['Day'].ffill()
        df['Description'] = df['Description'].ffill()
        
        # Hapus baris yang REF-nya kosong (biasanya baris header bulan/tahun atau kosong)
        df = df.dropna(subset=['REF']).copy()
        
        # Bersihkan Angka
        df['DEBET'] = df['DEBET'].apply(clean_rupiah_number_element)
        df['CREDIT'] = df['CREDIT'].apply(clean_rupiah_number_element)
        
        # Buat Tanggal Lengkap (Asumsi Nov 2025)
        def parse_day(d):
            try:
                d_int = int(float(d)) # Handle '1.0' string
                return date(DEFAULT_YEAR, DEFAULT_MONTH, d_int).isoformat()
            except:
                return date(DEFAULT_YEAR, DEFAULT_MONTH, 1).isoformat() # Default tgl 1 jika gagal
                
        df['transaction_date'] = df['Day'].apply(parse_day)
        
        # Grouping per Tanggal dan Deskripsi untuk membuat Journal Entry Header
        grouped = df.groupby(['transaction_date', 'Description'])
        
        entries_count = 0
        lines_count = 0
        
        for (tx_date, desc), group in grouped:
            # Skip jika total debet dan kredit 0
            if group['DEBET'].sum() == 0 and group['CREDIT'].sum() == 0:
                continue
                
            # Buat Header
            entry = supabase.table("journal_entries").insert({
                "transaction_date": tx_date,
                "description": desc
            }).execute().data[0]
            journal_id = entry['id']
            entries_count += 1
            
            # Buat Lines
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
        current_prod_id = None
        
        for idx, row in df_raw.iterrows():
            row_str = row.astype(str)
            
            # Deteksi Header Item
            if 'ITEM' in row_str.iloc[1]: # Cek di kolom 1
                # Cari kode di kolom ke-14 (index 13) atau scan baris
                # Cara aman: cari string "Kode" lalu ambil value sebelahnya
                for i, val in enumerate(row_str):
                    if "Kode" in val:
                        code_cand = row_str.iloc[i+1].strip()
                        current_prod_id = PRODUCT_CODE_TO_ID.get(code_cand)
                        break
                continue
            
            # Proses Baris Data (Cek jika ada Tanggal/Hari di Col 2)
            # Struktur: Col 2 = Day. Col 4 = IN Qty. Col 5 = IN Price. Col 7 = OUT Qty. Col 8 = OUT Price.
            day_val = row_str.iloc[2].strip()
            
            if current_prod_id and day_val.isdigit(): # Validasi baris data: ada Produk ID dan Hari adalah angka
                try:
                    day = int(day_val)
                    tx_date = date(DEFAULT_YEAR, DEFAULT_MONTH, day).isoformat()
                    
                    # Parse Angka
                    qty_in = pd.to_numeric(row_str.iloc[4], errors='coerce') or 0
                    cost_in = clean_rupiah_number_element(row_str.iloc[5])
                    qty_out = pd.to_numeric(row_str.iloc[7], errors='coerce') or 0 # Index 7 (Col H)
                    cost_out = clean_rupiah_number_element(row_str.iloc[8]) # Index 8 (Col I)
                    
                    if qty_in > 0:
                        movements.append({
                            "product_id": current_prod_id, "movement_date": tx_date,
                            "movement_type": "RECEIPT", "quantity_change": qty_in,
                            "unit_cost": cost_in, "reference_id": f"IMP-IN-{idx}"
                        })
                    
                    if qty_out > 0:
                        movements.append({
                            "product_id": current_prod_id, "movement_date": tx_date,
                            "movement_type": "ISSUE", "quantity_change": -qty_out,
                            "unit_cost": cost_out, "reference_id": f"IMP-OUT-{idx}"
                        })
                except:
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
