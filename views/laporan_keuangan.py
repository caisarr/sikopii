import streamlit as st
import pandas as pd
from supabase_client import supabase
from io import BytesIO
from datetime import date, timedelta
import numpy as np

# --- RUPIAH FORMAT UTILITY ---
def format_rupiah(amount):
    """Mengubah angka menjadi string format Rp. X.XXX.XXX"""
    if pd.isna(amount) or amount == '':
        return ''
    if amount < 0:
        return f"(Rp {-amount:,.0f})".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"Rp {amount:,.0f}".replace(",", "_").replace(".", ",").replace("_", ".")


# --- LOGIKA PENYESUAIAN VIRTUAL (BERDASARKAN MJ.CSV) ---
# Data ini diambil dari file SIKLUS EXCEL.xlsx - MJ.csv
VIRTUAL_ADJUSTMENTS = [
    # 1. Beban Depresiasi
    {'Kode Akun': '6-1600', 'Debit': 508333.3333333333, 'Kredit': 0.00}, 
    {'Kode Akun': '1-2210', 'Debit': 0.00, 'Kredit': 333333.3333333333},  # Akumulasi Penyusutan Bangunan
    {'Kode Akun': '1-2310', 'Debit': 0.00, 'Kredit': 150000.00},  # Akumulasi Penyusutan Kendaraan
    {'Kode Akun': '1-2410', 'Debit': 0.00, 'Kredit': 25000.00},   # Akumulasi Penyusutan Peralatan

    # 2. Beban Perlengkapan
    {'Kode Akun': '6-1400', 'Debit': 1410000.00, 'Kredit': 0.00},
    {'Kode Akun': '1-1300', 'Debit': 0.00, 'Kredit': 1410000.00},  # Perlengkapan

    # 3. Beban Pakan Terpakai
    {'Kode Akun': '6-1700', 'Debit': 1050000.00, 'Kredit': 0.00},
    {'Kode Akun': '1-1400', 'Debit': 0.00, 'Kredit': 1050000.00},  # Pakan lobster

    # 4. Beban Vitamin Terpakai
    {'Kode Akun': '6-1800', 'Debit': 200000.00, 'Kredit': 0.00},
    {'Kode Akun': '1-1500', 'Debit': 0.00, 'Kredit': 200000.00},  # Vitamin lobster
]
# Konversi Adjustment Virtual menjadi DataFrame untuk diproses
df_adjustments = pd.DataFrame(VIRTUAL_ADJUSTMENTS).groupby('Kode Akun').sum().reset_index().fillna(0)


# --- DATA FETCHING & FILTERING ---

@st.cache_data
def fetch_all_accounting_data():
    """Mengambil semua data yang diperlukan dari Supabase dan mengonversi tipe data."""
    
    try:
        # Mengambil inventory_movements dengan join ke products untuk nama
        inventory_response = supabase.table("inventory_movements").select("*, products(name)").execute()
        
        journal_lines_response = supabase.table("journal_lines").select("*").execute()
        journal_entries_response = supabase.table("journal_entries").select("id, transaction_date, description, order_id").execute()
        coa_response = supabase.table("chart_of_accounts").select("*").execute()
        
        df_entries = pd.DataFrame(journal_entries_response.data)
        
        # Konversi Tanggal di Sumber (di dalam cache) dan hapus zona waktu
        df_entries['transaction_date'] = pd.to_datetime(df_entries['transaction_date'], errors='coerce')
        if df_entries['transaction_date'].dt.tz is not None:
             df_entries['transaction_date'] = df_entries['transaction_date'].dt.tz_localize(None)
        df_entries['transaction_date'] = df_entries['transaction_date'].dt.normalize()
        
        return {
            "journal_lines": pd.DataFrame(journal_lines_response.data).fillna(0),
            "journal_entries": df_entries,
            "coa": pd.DataFrame(coa_response.data),
            "inventory_movements": pd.DataFrame(inventory_response.data),
        }
    except Exception as e:
        st.error(f"Gagal mengambil data dari Supabase: {e}. Pastikan Supabase aktif.")
        return {
            "journal_lines": pd.DataFrame(),
            "journal_entries": pd.DataFrame(),
            "coa": pd.DataFrame(columns=['account_code', 'account_name', 'account_type', 'normal_balance']),
            "inventory_movements": pd.DataFrame(),
        }


def get_base_data_and_filter(start_date, end_date):
    """
    Mengambil SEMUA transaksi HINGGA tanggal akhir laporan (filter_end),
    untuk memastikan saldo kumulatif (termasuk Pendapatan dan Saldo Awal) terhitung.
    """
    data = fetch_all_accounting_data()
    df_lines = data["journal_lines"]
    df_entries = data["journal_entries"].copy()
    df_coa = data["coa"]
    df_movements = data["inventory_movements"]
    
    if df_entries.empty or df_lines.empty:
        empty_merged = pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount'])
        return empty_merged, df_coa, df_movements
        
    df_entries['transaction_date'] = df_entries['transaction_date'].astype('datetime64[ns]')
    
    filter_end = pd.to_datetime(end_date)
    
    # Filter semua transaksi yang tanggalnya KURANG DARI ATAU SAMA DENGAN tanggal akhir laporan.
    df_journal_entries_final = df_entries.loc[
        (df_entries['transaction_date'] <= filter_end)
    ].copy()
        
    if df_journal_entries_final.empty:
        empty_merged = pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount'])
        return empty_merged, df_coa, df_movements

    df_journal_merged = df_lines.merge(df_journal_entries_final, left_on='journal_id', right_on='id', suffixes=('_line', '_entry'))
    df_journal_merged = df_journal_merged.merge(df_coa, on='account_code')
    
    return df_journal_merged.sort_values(
        by=['transaction_date', 'journal_id', 'debit_amount'], 
        ascending=[True, True, False]
    ), df_coa, df_movements


def calculate_trial_balance(df_journal, df_coa):
    """Menghitung Neraca Saldo (TB) dari data jurnal yang digabungkan."""
    
    if df_journal.empty:
        df_tb = df_coa[['account_code', 'account_name', 'account_type']].copy()
        df_tb['Debit'] = 0.0
        df_tb['Kredit'] = 0.0
    else:
        df_tb = df_journal.groupby('account_code').agg(
            Total_Debit=('debit_amount', 'sum'),
            Total_Kredit=('credit_amount', 'sum')
        ).reset_index()
        
        df_tb = df_tb.merge(df_coa, on='account_code', how='right').fillna(0)
        
        df_tb['Tipe_Num'] = df_tb['account_code'].astype(str).str[0].astype(int) 

        df_tb['Saldo Bersih'] = df_tb['Total_Debit'] - df_tb['Total_Kredit']
        
        df_tb['Debit'] = df_tb.apply(
            lambda row: row['Saldo Bersih'] if row['normal_balance'] == 'Debit' and row['Saldo Bersih'] >= 0 else 
                        -row['Saldo Bersih'] if row['normal_balance'] == 'Credit' and row['Saldo Bersih'] < 0 else 0, axis=1
        )
        df_tb['Kredit'] = df_tb.apply(
            lambda row: row['Saldo Bersih'] if row['normal_balance'] == 'Credit' and row['Saldo Bersih'] >= 0 else 
                        -row['Saldo Bersih'] if row['normal_balance'] == 'Debit' and row['Saldo Bersih'] < 0 else 0, axis=1
        )
        
    df_tb = df_tb[['account_code', 'account_name', 'account_type', 'Debit', 'Kredit', 'Tipe_Num']].sort_values(by='account_code')
    df_tb.columns = ['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Debit', 'Kredit', 'Tipe_Num']
    
    return df_tb


def calculate_closing_and_reporting_data(df_tb_adj):
    """
    Menghitung Laba Bersih, Modal Akhir, dan menyiapkan kolom IS/BS untuk Worksheet.
    """
    
    AKUN_MODAL = '3-1100'
    AKUN_PRIVE = '3-1200'
    
    # 1. HITUNG LABA BERSIH DARI TB ADJ
    
    Total_Revenue = df_tb_adj[df_tb_adj['Tipe_Num'].isin([4, 8])]['TB ADJ Kredit'].sum()
    Total_Expense = df_tb_adj[df_tb_adj['Tipe_Num'].isin([5, 6, 9])]['TB ADJ Debit'].sum()
    Prive_Value = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_PRIVE]['TB ADJ Debit'].sum()
    Modal_Awal_Baris = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_MODAL]['TB ADJ Kredit'].sum()
    
    Net_Income = Total_Revenue - Total_Expense
    Modal_Baru = Modal_Awal_Baris + Net_Income - Prive_Value
    
    # 2. SIAPKAN KOLOM IS/BS UNTUK WORKSHEET
    df_ws_final = df_tb_adj.copy()
    
    df_ws_final['IS Debit'] = 0.0
    df_ws_final['IS Kredit'] = 0.0
    df_ws_final['BS Debit'] = 0.0
    df_ws_final['BS Kredit'] = 0.0
    
    IS_TYPES = [4, 5, 6, 8, 9] # Nominal Accounts
    BS_TYPES = [1, 2, 3] # Real Accounts

    # Populate IS columns
    df_ws_final.loc[df_ws_final['Tipe_Num'].isin(IS_TYPES), 'IS Debit'] = df_ws_final['TB ADJ Debit']
    df_ws_final.loc[df_ws_final['Tipe_Num'].isin(IS_TYPES), 'IS Kredit'] = df_ws_final['TB ADJ Kredit']

    # Populate BS columns
    df_ws_final.loc[df_ws_final['Tipe_Num'].isin(BS_TYPES), 'BS Debit'] = df_ws_final['TB ADJ Debit']
    
    # Khusus untuk Saldo Modal di BS, kita pakai Modal_Baru (Modal Akhir)
    df_ws_final.loc[df_ws_final['Kode Akun'] == AKUN_MODAL, 'BS Kredit'] = Modal_Baru
    
    # Untuk akun real lainnya, gunakan TB ADJ Kredit
    df_ws_final.loc[df_ws_final['Tipe_Num'].isin([1, 2]) | (df_ws_final['Kode Akun'] == AKUN_PRIVE), 'BS Kredit'] = df_ws_final['TB ADJ Kredit']
    
    
    # 3. LAPORAN KEUANGAN
    df_laba_rugi = create_income_statement_df(df_tb_adj, Total_Revenue, Total_Expense, Net_Income)
    
    # Laporan Perubahan Modal (RE)
    df_re = pd.DataFrame({
        'Deskripsi': ['Modal Awal', 'Laba Bersih Periode', 'Prive', 'Modal Akhir'],
        'Jumlah': [Modal_Awal_Baris, Net_Income, -Prive_Value, Modal_Baru]
    })
    
    df_laporan_posisi_keuangan = create_balance_sheet_df(df_tb_adj, Modal_Baru)


    return Net_Income, df_laba_rugi, df_re, df_laporan_posisi_keuangan, df_ws_final


def create_income_statement_df(df_tb_adj, Total_Revenue, Total_Expense, Net_Income):
    """Membuat DataFrame yang rapi untuk Laporan Laba Rugi sesuai format Excel."""
    data = []
    
    df_is = df_tb_adj[df_tb_adj['Tipe_Num'].isin([4, 5, 6, 8, 9])].copy()
    
    def get_saldo_and_df(df, tipe_nums, saldo_col_adj, sort_by='Kode Akun'):
        df_filtered = df[df['Tipe_Num'].isin(tipe_nums)].sort_values(by=sort_by)
        total = df_filtered[saldo_col_adj].sum()
        return total, df_filtered

    # 1. PENDAPATAN (Akun 4)
    data.append(['PENDAPATAN', '', ''])
    Total_4, df_4 = get_saldo_and_df(df_is, [4], 'TB ADJ Kredit')
    for index, row in df_4.iterrows():
        data.append([row['Nama Akun'], row['TB ADJ Kredit'], ''])
    data.append(['TOTAL PENDAPATAN', '', Total_4])
    
    # 2. HARGA POKOK PENJUALAN (Akun 5)
    Total_5, df_5 = get_saldo_and_df(df_is, [5], 'TB ADJ Debit')
    data.append(['HARGA POKOK PENJUALAN', '', ''])
    for index, row in df_5.iterrows():
        data.append([row['Nama Akun'], row['TB ADJ Debit'], ''])
    data.append(['TOTAL COST OF GOODS SOLD', '', Total_5])

    Laba_Kotor = Total_4 - Total_5
    data.append(['LABA KOTOR', '', Laba_Kotor])

    # 3. BEBAN OPERASIONAL (Akun 6)
    Total_6, df_6 = get_saldo_and_df(df_is, [6], 'TB ADJ Debit')
    data.append(['BEBAN OPERASIONAL', '', ''])
    for index, row in df_6.iterrows():
        data.append([row['Nama Akun'], row['TB ADJ Debit'], ''])
    data.append(['TOTAL BEBAN OPERASIONAL', '', Total_6])
    
    Laba_Operasi = Laba_Kotor - Total_6
    data.append(['LABA OPERASI', '', Laba_Operasi])

    # 4. PENDAPATAN DAN BEBAN LAIN-LAIN (Akun 8 dan 9)
    data.append(['PENDAPATAN DAN BEBAN LAIN-LAIN', '', ''])
    
    # Pendapatan Lain-lain (Akun 8)
    Total_8, df_8 = get_saldo_and_df(df_is, [8], 'TB ADJ Kredit')
    for index, row in df_8.iterrows():
        data.append([row['Nama Akun'], row['TB ADJ Kredit'], ''])
    
    # Beban Lain-lain (Akun 9)
    Total_9, df_9 = get_saldo_and_df(df_is, [9], 'TB ADJ Debit')
    for index, row in df_9.iterrows():
        data.append([row['Nama Akun'], -row['TB ADJ Debit'], '']) # Tampilkan sebagai negatif

    Net_Lain_Lain = Total_8 - Total_9
    data.append(['TOTAL PENDAPATAN DAN BEBAN LAIN-LAIN', '', Net_Lain_Lain])
    
    Laba_Bersih_Hitung = Laba_Operasi + Net_Lain_Lain

    data.append(['LABA BERSIH', '', Laba_Bersih_Hitung])

    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah', 'Total'])


def create_balance_sheet_df(df_tb_adj, Modal_Akhir):
    """Membuat DataFrame yang rapi untuk Laporan Posisi Keuangan sesuai format Excel."""
    data = []
    
    # Hanya ambil akun Aset (1) dan Liabilitas (2)
    df_bs = df_tb_adj[df_tb_adj['Tipe_Num'].isin([1, 2])].copy()
    
    # -- ASET --
    data.append(['ASET', '', ''])
    
    # Aset Lancar (Kode Akun 1-1XXX)
    data.append(['Aset Lancar', '', ''])
    df_current_asset = df_bs[df_bs['Kode Akun'].astype(str).str.startswith('1-1')].copy()
    for index, row in df_current_asset.iterrows():
        # Aset dihitung sebagai Debit - Kredit
        balance = row['TB ADJ Debit'] - row['TB ADJ Kredit']
        data.append([row['Nama Akun'], balance, ''])
    Total_Aset_Lancar = df_current_asset['TB ADJ Debit'].sum() - df_current_asset['TB ADJ Kredit'].sum()
    data.append(['TOTAL ASET LANCAR', '', Total_Aset_Lancar])
    
    # Aset Tetap (Kode Akun 1-2XXX)
    data.append(['Aset Tetap', '', ''])
    df_fixed_asset = df_bs[df_bs['Kode Akun'].astype(str).str.startswith('1-2')].copy()
    for index, row in df_fixed_asset.iterrows():
        balance = row['TB ADJ Debit'] - row['TB ADJ Kredit']
        data.append([row['Nama Akun'], balance, ''])
    Total_Aset_Tetap = df_fixed_asset['TB ADJ Debit'].sum() - df_fixed_asset['TB ADJ Kredit'].sum()
    data.append(['TOTAL ASET TETAP', '', Total_Aset_Tetap])

    Total_Aset = Total_Aset_Lancar + Total_Aset_Tetap
    data.append(['TOTAL ASET', '', Total_Aset])
    
    # --- LIABILITAS & EKUITAS ---
    data.append(['LIABILITAS & EKUITAS', '', ''])
    
    # Liabilitas Lancar (Kode Akun 2-1XXX)
    data.append(['Liabilitas Lancar', '', ''])
    df_current_liab = df_bs[df_bs['Kode Akun'].astype(str).str.startswith('2-1')].copy()
    for index, row in df_current_liab.iterrows():
        # Liabilitas dihitung sebagai Kredit - Debit
        balance = row['TB ADJ Kredit'] - row['TB ADJ Debit']
        data.append([row['Nama Akun'], balance, ''])
    Total_Liabilitas_Lancar = df_current_liab['TB ADJ Kredit'].sum() - df_current_liab['TB ADJ Debit'].sum()
    data.append(['TOTAL LIABILITAS LANCAR', '', Total_Liabilitas_Lancar])

    # Liabilitas Jangka Panjang (Kode Akun 2-2XXX)
    data.append(['Liabilitas Jangka Panjang', '', ''])
    df_long_liab = df_bs[df_bs['Kode Akun'].astype(str).str.startswith('2-2')].copy()
    for index, row in df_long_liab.iterrows():
        balance = row['TB ADJ Kredit'] - row['TB ADJ Debit']
        data.append([row['Nama Akun'], balance, ''])
    Total_Liabilitas_Jangka_Panjang = df_long_liab['TB ADJ Kredit'].sum() - df_long_liab['TB ADJ Debit'].sum()
    data.append(['TOTAL LIABILITAS JANGKA PANJANG', '', Total_Liabilitas_Jangka_Panjang])
    
    Total_Liabilitas = Total_Liabilitas_Lancar + Total_Liabilitas_Jangka_Panjang
    data.append(['TOTAL LIABILITAS', '', Total_Liabilitas])

    # EKUITAS
    data.append(['EKUITAS', '', ''])
    data.append(['Modal Pemilik Akhir', '', Modal_Akhir])
    
    Total_Liabilitas_Ekuitas = Total_Liabilitas + Modal_Akhir
    data.append(['TOTAL LIABILITAS & EKUITAS', '', Total_Liabilitas_Ekuitas])

    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah 1', 'Jumlah 2'])


def create_cash_flow_statement_df(df_journal_merged, Net_Income):
    """
    Membuat DataFrame Arus Kas (Sistem Langsung/Direct Method).
    Data disimulasikan/disederhanakan berdasarkan SIKLUS EXCEL.xlsx - CASH FLOW.csv
    """
    
    # Nilai dari SIKLUS EXCEL.xlsx - GL.csv / NS AWAL.csv
    CASH_OPENING_BALANCE = 168765000 
    
    # Total Cash In/Out Operasi (dari CASH FLOW.csv)
    Total_Cash_In_Op = 39575000 
    Total_Cash_Out_Op = 3315000 
    
    # Total Cash Out Financing (dari CASH FLOW.csv)
    Total_Cash_Financing_Out = -6000000 
    
    data = []
    
    # 1. ARUS KAS DARI KEGIATAN OPERASI
    data.append(['ARUS KAS DARI KEGIATAN OPERASI', '', ''])
    data.append(['Penerimaan Kas', '', ''])
    data.append(['Total Penerimaan Kas Operasi (Simulasi)', Total_Cash_In_Op, ''])
    data.append(['Pengeluaran Kas', '', ''])
    data.append(['Total Pengeluaran Kas Operasi (Simulasi)', -Total_Cash_Out_Op, ''])

    Net_Cash_Op = Total_Cash_In_Op - Total_Cash_Out_Op
    data.append(['NET ARUS KAS DARI KEGIATAN OPERASI', '', Net_Cash_Op])
    
    # 2. ARUS KAS DARI KEGIATAN INVESTASI
    data.append(['ARUS KAS DARI KEGIATAN INVESTASI', '', ''])
    Net_Cash_Inv = 0
    data.append(['NET ARUS KAS DARI KEGIATAN INVESTASI', '', Net_Cash_Inv])

    # 3. ARUS KAS DARI KEGIATAN PENDANAAN
    data.append(['ARUS KAS DARI KEGIATAN PENDANAAN', '', ''])
    Net_Cash_Fin = Total_Cash_Financing_Out
    data.append(['NET ARUS KAS DARI KEGIATAN PENDANAAN', '', Net_Cash_Fin])
    
    # KENA/TURUN KAS
    Kenaikan_Kas = Net_Cash_Op + Net_Cash_Inv + Net_Cash_Fin
    data.append(['KENAIKAN/PENURUNAN KAS', '', Kenaikan_Kas])
    
    # Saldo Kas Awal
    data.append(['SALDO KAS AWAL', '', CASH_OPENING_BALANCE])
    
    # Saldo Kas Akhir
    Saldo_Kas_Akhir = CASH_OPENING_BALANCE + Kenaikan_Kas
    data.append(['SALDO KAS AKHIR', '', Saldo_Kas_Akhir])

    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah 1', 'Jumlah 2'])


def create_inventory_movement_report(df_movements):
    """
    Membuat laporan pergerakan persediaan (Kartu Persediaan).
    Menampilkan log pergerakan terperinci dengan saldo kumulatif (Qty dan Nilai).
    """
    if df_movements.empty or 'products' not in df_movements.columns:
        return pd.DataFrame(columns=['Produk', 'Tanggal', 'Jenis', 'Referensi', 'Masuk Qty', 'Keluar Qty', 'Harga Satuan', 'Total Mutasi', 'Saldo Qty', 'Saldo Nilai'])

    # Expand nested product name
    df_movements['product_name'] = df_movements['products'].apply(lambda x: x.get('name') if isinstance(x, dict) else 'Unknown')
    
    # Sort data by date and product
    df_movements = df_movements.sort_values(by=['movement_date', 'product_name'], ascending=[True, True])
    
    report_data = []
    
    for product_id, group in df_movements.groupby('product_id'):
        
        product_name = group['product_name'].iloc[0]
        
        # Initialize running balance
        running_qty = 0
        running_value = 0
        
        for index, row in group.iterrows():
            qty_change = row['quantity_change']
            unit_cost = row['unit_cost']
            
            # Update running balance
            running_qty += qty_change
            running_value += (qty_change * unit_cost)
            
            report_data.append({
                "Nama Produk": product_name,
                "Tanggal": row['movement_date'],
                "Jenis Pergerakan": row['movement_type'],
                "Referensi": row['reference_id'],
                "Qty Masuk (RECEIPT)": qty_change if row['movement_type'] == 'RECEIPT' else 0,
                "Qty Keluar (ISSUE)": abs(qty_change) if row['movement_type'] == 'ISSUE' else 0,
                "Unit Cost": unit_cost,
                "Total Cost": abs(qty_change * unit_cost),
                "Balance Qty": running_qty,
                "Balance Value (Kumulatif)": running_value,
            })

    df_report = pd.DataFrame(report_data)
    
    # Clean up column names for display
    df_report.columns = ['Produk', 'Tanggal', 'Jenis', 'Referensi', 'Masuk Qty', 'Keluar Qty', 'Harga Satuan', 'Total Mutasi', 'Saldo Qty', 'Saldo Nilai']
    
    return df_report.sort_values(by=['Produk', 'Tanggal'])


def to_excel_bytes(reports):
    """Menyimpan semua laporan ke dalam satu file Excel (BytesIO)"""
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for sheet_name, df in reports.items():
            if isinstance(df, pd.DataFrame):
                clean_name = sheet_name.replace(" ", "_").replace("(", "").replace(")", "")[:30]
                df.to_excel(writer, sheet_name=clean_name, index=False)
            
    processed_data = output.getvalue()
    return processed_data


def generate_reports():
    """Definisi Fungsi Utama untuk menghasilkan laporan."""
    
    # Tanggal Filter
    today = date.today()
    if "end_date" not in st.session_state:
        st.session_state.end_date = today
    if "start_date" not in st.session_state:
        st.session_state.start_date = today.replace(day=1)
    
    start_date = st.sidebar.date_input("Tanggal Mulai", value=st.session_state.start_date)
    end_date = st.sidebar.date_input("Tanggal Akhir", value=st.session_state.end_date)
    
    df_journal_merged, df_coa, df_movements = get_base_data_and_filter(start_date, end_date)
    
    # --- 1. NERACA SALDO SEBELUM PENYESUAIAN (TB BEFORE ADJ) ---
    df_tb_before_adj = calculate_trial_balance(df_journal_merged, df_coa)

    # --- 2. WORKSHEET (NS ADJ) ---
    df_ws = df_coa[['account_code', 'account_name', 'account_type', 'normal_balance']].copy()
    df_ws.columns = ['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Saldo Normal']
    
    # Merge TB Before Adj
    df_ws = df_ws.merge(df_tb_before_adj[['Kode Akun', 'Debit', 'Kredit', 'Tipe_Num']], on='Kode Akun', how='left').fillna(0)
    df_ws.columns = ['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Saldo Normal', 'TB Debit', 'TB Kredit', 'Tipe_Num']
    
    # Merge Jurnal Penyesuaian (MJ)
    # Kolom hasil merge akan dinamai 'Debit' dan 'Kredit' karena tidak konflik dengan 'TB Debit'/'TB Kredit'
    df_ws = df_ws.merge(df_adjustments[['Kode Akun', 'Debit', 'Kredit']], on='Kode Akun', how='left').fillna(0)
    
    # FIX: Rename kolom yang baru masuk ('Debit' dan 'Kredit') menjadi nama yang diharapkan ('MJ Debit' dan 'MJ Kredit')
    df_ws.rename(columns={'Debit': 'MJ Debit', 'Kredit': 'MJ Kredit'}, inplace=True) 

    # TB AFTER ADJUSTMENT (TB ADJ)
    def calculate_tb_adj_final(row):
        # Perhitungan menggunakan kolom yang sudah diubah namanya: 'MJ Debit' dan 'MJ Kredit'
        net_change = (row['TB Debit'] - row['TB Kredit']) + (row['MJ Debit'] - row['MJ Kredit'])
        if row['Saldo Normal'] == 'Debit':
            return max(0, net_change), max(0, -net_change)
        else:
            return max(0, -net_change), max(0, net_change)

    df_ws[['TB ADJ Debit', 'TB ADJ Kredit']] = df_ws.apply(lambda row: calculate_tb_adj_final(row), axis=1, result_type='expand')

    # --- 3. SIKLUS PELAPORAN UTAMA (DARI TB ADJ) ---
    net_income, df_laba_rugi, df_re, df_laporan_posisi_keuangan, df_ws_with_is_bs = calculate_closing_and_reporting_data(df_ws)
    
    # Finalize df_ws display columns
    df_ws_final = df_ws_with_is_bs[['Kode Akun', 'Nama Akun', 'TB Debit', 'TB Kredit', 'MJ Debit', 'MJ Kredit', 'TB ADJ Debit', 'TB ADJ Kredit', 'IS Debit', 'IS Kredit', 'BS Debit', 'BS Kredit']]

    # --- Tambahkan Laporan Arus Kas ---
    df_cash_flow = create_cash_flow_statement_df(df_journal_merged, net_income)
    
    # --- Tambahkan Kartu Persediaan ---
    df_inventory_card = create_inventory_movement_report(df_movements)

    return {
        "Laba Bersih": net_income,
        "Neraca Saldo Sebelum Penyesuaian": df_tb_before_adj,
        "Worksheet (Kertas Kerja)": df_ws_final,
        "Laporan Laba Rugi": df_laba_rugi,
        "Laporan Perubahan Modal": df_re,
        "Laporan Posisi Keuangan": df_laporan_posisi_keuangan,
        "Laporan Arus Kas": df_cash_flow,
        "Kartu Persediaan": df_inventory_card, # Ditambahkan
    }


def show_reports_page():
    st.title("📊 Laporan Keuangan & Akuntansi Lengkap")
    
    st.sidebar.header("Filter Tanggal Laporan")
    
    reports = generate_reports()
    net_income = reports.get("Laba Bersih", 0)
    
    # Fungsi format Rupiah untuk tampilan
    def format_rupiah(amount):
        if pd.isna(amount) or amount == '':
            return ''
        if amount < 0:
            return f"(Rp {-amount:,.0f})".replace(",", "_").replace(".", ",").replace("_", ".")
        return f"Rp {amount:,.0f}".replace(",", "_").replace(".", ",").replace("_", ".")

    def display_formatted_df(df, columns_to_format=['Debit', 'Kredit', 'TB Debit', 'TB Kredit', 'MJ Debit', 'MJ Kredit', 'TB ADJ Debit', 'TB ADJ Kredit', 'IS Debit', 'IS Kredit', 'BS Debit', 'BS Kredit', 'Jumlah', 'Total', 'Jumlah 1', 'Jumlah 2', 'Harga Satuan', 'Total Mutasi', 'Saldo Nilai']):
        df_display = df.copy()
        for col in columns_to_format:
            if col in df_display.columns and col not in ['Saldo Qty', 'Masuk Qty', 'Keluar Qty']:
                # Handling for negative numbers in certain columns (e.g., Jumlah, Jumlah 1, Jumlah 2)
                if col in ['Jumlah', 'Jumlah 1', 'Jumlah 2'] and any(df_display[col].apply(lambda x: isinstance(x, (int, float)) and x < 0)):
                     df_display[col] = df_display[col].apply(lambda x: format_rupiah(x))
                elif col in ['Jumlah', 'Jumlah 1', 'Jumlah 2']:
                    df_display[col] = df_display[col].apply(format_rupiah)
                else: 
                    df_display[col] = df_display[col].apply(format_rupiah)
        return df_display

    st.markdown("---")
    
    # Tampilkan Ringkasan Laba Bersih
    if net_income >= 0:
        st.success(f"**Laba Bersih (Net Income): {format_rupiah(net_income)}**")
    else:
        st.error(f"**Rugi Bersih (Net Loss): {format_rupiah(net_income)}**")
    st.markdown("---")
    
    # Tampilkan Worksheet (Urutan 1)
    st.header("1. Kertas Kerja (Worksheet)")
    st.info("Worksheet mencakup Neraca Saldo, Jurnal Penyesuaian, Neraca Saldo Setelah Penyesuaian, Laba Rugi, dan Posisi Keuangan.")
    st.dataframe(display_formatted_df(reports["Worksheet (Kertas Kerja)"]), use_container_width=True)
    
    # Tampilkan Laporan Keuangan Utama (Urutan 2, 3, 4, 5)
    st.header("2. Laporan Laba Rugi (Income Statement)")
    st.dataframe(display_formatted_df(reports["Laporan Laba Rugi"], columns_to_format=['Jumlah', 'Total']), use_container_width=True)
    
    st.header("3. Laporan Perubahan Modal (Retained Earnings)")
    st.dataframe(display_formatted_df(reports["Laporan Perubahan Modal"], columns_to_format=['Jumlah']), use_container_width=True)
    
    st.header("4. Laporan Posisi Keuangan (Balance Sheet)")
    st.dataframe(display_formatted_df(reports["Laporan Posisi Keuangan"], columns_to_format=['Jumlah 1', 'Jumlah 2']), use_container_width=True)
    
    st.header("5. Laporan Arus Kas (Cash Flow Statement)")
    st.warning("Perhitungan Arus Kas ini disederhanakan/disimulasikan dari Jurnal Umum dan data historis Excel.")
    st.dataframe(display_formatted_df(reports["Laporan Arus Kas"], columns_to_format=['Jumlah 1', 'Jumlah 2']), use_container_width=True)
    
    # Tampilkan Kartu Persediaan (Urutan 6)
    st.header("6. Kartu Persediaan (Inventory Card)")
    st.info("Menampilkan log pergerakan unit (Masuk/Keluar) beserta biaya per unit, diambil dari tabel 'inventory_movements'.")
    st.dataframe(display_formatted_df(reports["Kartu Persediaan"]), use_container_width=True)
    
    st.markdown("---")

    # Tombol Download
    st.subheader("Unduh Semua Laporan")
    
    excel_data = to_excel_bytes(reports)

    st.download_button(
        label="📥 Unduh Semua Laporan sebagai Excel",
        data=excel_data,
        file_name=f'Laporan_Akuntansi_Siklus_Lengkap_{date.today().strftime("%Y%m%d")}.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


if __name__ == "__main__":
    show_reports_page()
