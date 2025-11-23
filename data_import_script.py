import pandas as pd
from supabase_client import supabase 
from datetime import datetime
import numpy as np

# PENTING: Skrip ini akan menghapus data lama di tabel-tabel utama Supabase Anda.

# ✅ MAPPING ID PRODUK BERDASARKAN INPUT ANDA
PRODUCT_CODE_TO_ID = {
    "A01": 1, 
    "A02": 2, 
    "B01": 3, 
    "B02": 4, 
    "C01": 5, 
    "C02": 6,
}


# --- 1. FUNGSI PEMBERSIHAN DATA (URUTAN YANG BENAR) ---
def clear_all_data():
    """
    Menghapus data dari tabel-tabel anak terdalam terlebih dahulu untuk menghindari Foreign Key Error (23503).
    """
    print("\n--- Membersihkan Data Database (Wajib) ---")
    
    # 1. Hapus Tabel Anak Terdalam (yang paling banyak merujuk produk/akun)
    print("Aksi: Membersihkan inventory_movements...")
    supabase.table("inventory_movements").delete().neq("id", 0).execute() 
    
    print("Aksi: Membersihkan journal_lines...")
    supabase.table("journal_lines").delete().neq("id", 0).execute() 
    
    print("Aksi: Membersihkan order_items...")
    supabase.table("order_items").delete().neq("id", 0).execute() 
    
    # 2. Hapus Tabel Induk Lapis Kedua
    print("Aksi: Membersihkan journal_entries...")
    supabase.table("journal_entries").delete().neq("id", 0).execute() 
    
    print("Aksi: Membersihkan orders...")
    supabase.table("orders").delete().neq("id", 0).execute() 

    # 3. Hapus Tabel Products (Tabel ini merujuk COA)
    print("Aksi: Membersihkan products...")
    supabase.table("products").delete().neq("id", 0).execute() 

    # 4. Hapus Tabel Induk Paling Atas (Sekarang Aman Dihapus)
    print("Aksi: Membersihkan chart_of_accounts...")
    supabase.table("chart_of_accounts").delete().neq("account_code", "DUMMY").execute() 
    
    print("Pembersihan data historis Selesai.")


# --- 2. LOGIKA IMPORT COA (Hanya Insert) ---
def infer_coa_details(account_code):
    """Menentukan tipe akun dan saldo normal dari kode akun."""
    code_prefix = str(account_code).split('-')[0]
    
    if code_prefix == '1': return "Asset", "Debit"
    elif code_prefix == '2': return "Liability", "Credit"
    elif code_prefix == '3':
        if account_code.strip() == '3-1200': return "Equity", "Debit"
        return "Equity", "Credit"
    elif code_prefix in ['4', '8']: return "Revenue", "Credit"
    elif code_prefix in ['5', '6', '9']: return "Expense", "Debit"
    return "Other", "Debit"

def import_coa(file_path):
    print(f"\n--- Memulai Import Chart of Accounts (COA) dari {file_path} ---")
    try:
        # Delimiter titik koma (;)
        df = pd.read_csv(file_path, header=4, usecols=[0, 1], names=['account_code', 'account_name'], 
                         skiprows=lambda x: x < 5 and x != 4, delimiter=';')
    except Exception as e:
         print(f"ERROR membaca file COA: {e}"); return

    df = df.dropna(subset=['account_code', 'account_name']).copy()
    df['account_code'] = df['account_code'].astype(str).str.strip()
    df = df[df['account_code'].str.contains(r'-')].copy()

    df['account_type'], df['normal_balance'] = zip(*df['account_code'].apply(infer_coa_details))
    data_to_insert = df[['account_code', 'account_name', 'account_type', 'normal_balance']].to_dict('records')
    
    response = supabase.table("chart_of_accounts").insert(data_to_insert).execute()
    print(f"Import COA Selesai. Total {len(response.data)} akun dimasukkan.")

# --- 3. LOGIKA IMPORT JURNAL UMUM (Hanya Insert) ---
def import_general_journal(file_path):
    print(f"\n--- Memulai Import Jurnal Umum (GJ) dari {file_path} ---")
    try:
        # Perbaikan Indexing Kolom: Mengambil 5 kolom yang diperlukan. Delimiter titik koma (;).
        df_raw = pd.read_csv(file_path, header=6, usecols=[0, 2, 3, 4, 5], names=['Date', 'Description', 'REF', 'DEBET', 'CREDIT'], delimiter=';', engine='python')
    except Exception as e:
        print(f"ERROR membaca file GJ: {e}")
        return

    df_raw['Date'] = df_raw['Date'].ffill() 
    df_raw['Description'] = df_raw['Description'].ffill() 
    df_lines = df_raw.dropna(subset=['REF']).copy()
    
    # KOREKSI VALUE ERROR: Ganti string kosong dengan NaN sebelum konversi float
    
    # Proses DEBET
    df_lines['DEBET'] = df_lines['DEBET'].astype(str).str.strip()
    df_lines['DEBET'] = df_lines['DEBET'].str.replace(r'[^\d,\.]', '', regex=True).str.replace(',', '.', regex=False)
    df_lines['DEBET'] = df_lines['DEBET'].replace('', np.nan).astype(float).fillna(0)
    
    # Proses CREDIT
    df_lines['CREDIT'] = df_lines['CREDIT'].astype(str).str.strip()
    df_lines['CREDIT'] = df_lines['CREDIT'].str.replace(r'[^\d,\.]', '', regex=True).str.replace(',', '.', regex=False)
    df_lines['CREDIT'] = df_lines['CREDIT'].replace('', np.nan).astype(float).fillna(0)
    
    df_lines['full_date_str'] = df_lines['Date'].astype(str) + ' Nov 2025'
    df_lines['transaction_date'] = pd.to_datetime(df_lines['full_date_str'], format='%d %b %Y', errors='coerce').dt.normalize()

    journal_groups = df_lines.groupby(['transaction_date', 'Description'], dropna=True)
    lines_to_insert = []
    
    for (date, desc), group in journal_groups:
        try:
            entry_data = supabase.table("journal_entries").insert({
                "transaction_date": str(date.date()), "description": desc.strip().replace('(','').replace(')','').replace(',',''),
            }).execute().data[0]
            journal_id = entry_data['id']
        except Exception as e:
            print(f"ERROR memasukkan header untuk {desc} pada {date.date()}: {e}"); continue

        for _, row in group.iterrows():
            if row['DEBET'] > 0 or row['CREDIT'] > 0:
                lines_to_insert.append({
                    "journal_id": journal_id, "account_code": row['REF'].strip(),
                    "debit_amount": row['DEBET'], "credit_amount": row['CREDIT'],
                })

    if lines_to_insert:
        response = supabase.table("journal_lines").insert(lines_to_insert).execute()
        print(f"Import Journal Lines Selesai. Total {len(response.data)} baris jurnal dimasukkan.")
    else:
        print("Tidak ada baris jurnal yang dimasukkan.")
    print("Import Jurnal Umum Selesai.")

# --- 4. LOGIKA IMPORT INVENTORY MOVEMENTS (Kartu Persediaan) ---
def import_inventory_movements(file_path):
    print("\n--- Memulai Import Data Unit Kartu Persediaan ---")
    
    if not PRODUCT_CODE_TO_ID:
        print("GAGAL: PRODUCT_CODE_TO_ID kosong.")
        return

    try:
        # Delimiter titik koma (;)
        df_raw = pd.read_csv(file_path, header=None, skiprows=5, delimiter=';') 
        
    except Exception as e:
        print(f"ERROR membaca file INVENTORY: {e}")
        return
    
    try:
        
        movements_to_insert = []
        current_product_code = None

        for index, row in df_raw.iterrows():
            row_str = row.astype(str)
            
            if 'ITEM' in row_str.iloc[0] and 'Kode' in row_str.iloc[13]:
                current_product_code = row_str.iloc[14].strip() 
                continue
            
            if current_product_code and row_str.iloc[0].strip().startswith('2025'):
                
                try:
                    date_part = row_str.iloc[0].strip
