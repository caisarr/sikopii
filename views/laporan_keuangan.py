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
        # Normalisasi ke tengah malam
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
    Mengambil SEMUA transaksi HINGGA tanggal akhir laporan.
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
    
    # Filter transaksi
    df_journal_entries_final = df_entries.loc[
        (df_entries['transaction_date'] <= filter_end)
    ].copy()
        
    if df_journal_entries_final.empty:
        empty_merged = pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount'])
        return empty_merged, df_coa, df_movements

    # Rename deskripsi untuk konsistensi
    if 'description' in df_journal_entries_final.columns:
        df_journal_entries_final.rename(columns={'description': 'description_entry'}, inplace=True)

    # Merge
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

def create_general_journal_report(df_journal):
    if df_journal.empty:
        return pd.DataFrame(columns=['Tanggal', 'Kode Akun', 'Nama Akun', 'Deskripsi Transaksi', 'Debit', 'Kredit'])
        
    df_ju = df_journal.sort_values(by=['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False]).copy()
    df_ju['Tanggal'] = df_ju['transaction_date'].dt.strftime('%Y-%m-%d')
    
    df_ju = df_ju[['Tanggal', 'description_entry', 'account_code', 'account_name', 'debit_amount', 'credit_amount', 'journal_id']].copy()
    
    df_ju.columns = ['Tanggal', 'Deskripsi Transaksi', 'Kode Akun', 'Nama Akun', 'Debit', 'Kredit', 'Ref Jurnal']

    def format_account_name(row):
        if row['Debit'] == 0:
            return f"       {row['Nama Akun']}"
        return row['Nama Akun']

    df_ju['Nama Akun'] = df_ju.apply(format_account_name, axis=1)

    # Hilangkan duplikasi tanggal dan deskripsi
    is_first_row = df_ju.groupby('Ref Jurnal').cumcount() == 0
    df_ju['Tanggal'] = np.where(is_first_row, df_ju['Tanggal'], '')
    df_ju['Deskripsi Transaksi'] = np.where(is_first_row, df_ju['Deskripsi Transaksi'], '')

    return df_ju[['Tanggal', 'Kode Akun', 'Nama Akun', 'Deskripsi Transaksi', 'Debit', 'Kredit']].reset_index(drop=True)

def create_general_ledger_report(df_journal, df_coa):
    if df_journal.empty:
        return pd.DataFrame(columns=['Kode Akun', 'Nama Akun', 'Tanggal', 'Keterangan', 'Ref', 'Debit', 'Kredit', 'Saldo Debet', 'Saldo Kredit'])

    df_gl_data = []

    for account_code, group in df_journal.groupby('account_code'):
        coa_info = df_coa[df_coa['account_code'] == account_code].iloc[0]
        account_name = coa_info['account_name']
        normal_balance = coa_info['normal_balance']
        
        group = group.sort_values(by=['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False]).copy()
        running_balance = 0.0
        
        # Saldo Awal Logic (Jika ada transaksi saldo awal)
        # ... Logic sederhana: Akumulasi berjalan dari 0 karena data kita sekarang sudah mencakup semua saldo awal di transaksi ID 99
        
        for index, row in group.iterrows():
            debit = row['debit_amount']
            credit = row['credit_amount']
            
            if normal_balance == 'Debit':
                running_balance += (debit - credit)
            else: 
                running_balance += (credit - debit)
            
            desc = row['description_entry'] if row['description_entry'] else 'Detail'

            df_gl_data.append({
                'Kode Akun': account_code,
                'Nama Akun': account_name,
                'Tanggal': row['transaction_date'],
                'Keterangan': desc,
                'Ref': row['journal_id'],
                'Debit': debit,
                'Kredit': credit,
                'Saldo': running_balance
            })

    df_gl = pd.DataFrame(df_gl_data)
    if df_gl.empty:
        return pd.DataFrame()

    df_gl['Tanggal'] = df_gl['Tanggal'].dt.strftime('%Y-%m-%d')
    df_gl = df_gl.merge(df_coa[['account_code', 'normal_balance']], left_on='Kode Akun', right_on='account_code', how='left').drop(columns=['account_code'])
    
    df_gl['Saldo Debet'] = df_gl.apply(lambda row: row['Saldo'] if row['normal_balance'] == 'Debit' and row['Saldo'] >= 0 else (-row['Saldo'] if row['normal_balance'] == 'Credit' and row['Saldo'] < 0 else 0), axis=1)
    df_gl['Saldo Kredit'] = df_gl.apply(lambda row: row['Saldo'] if row['normal_balance'] == 'Credit' and row['Saldo'] >= 0 else (-row['Saldo'] if row['normal_balance'] == 'Debit' and row['Saldo'] < 0 else 0), axis=1)
    
    df_gl_final = df_gl[['Kode Akun', 'Nama Akun', 'Tanggal', 'Keterangan', 'Ref', 'Debit', 'Kredit', 'Saldo Debet', 'Saldo Kredit']].sort_values(by=['Kode Akun', 'Tanggal']).reset_index(drop=True)
    
    is_first = df_gl_final.groupby('Kode Akun').cumcount() == 0
    df_gl_final['Kode Akun'] = np.where(is_first, df_gl_final['Kode Akun'], '')
    df_gl_final['Nama Akun'] = np.where(is_first, df_gl_final['Nama Akun'], '')

    return df_gl_final


def calculate_closing_and_reporting_data(df_tb_adj):
    """
    Menghitung Laba Bersih, Modal Akhir, dan menyiapkan kolom IS/BS.
    """
    AKUN_MODAL = '3-1100'
    AKUN_PRIVE = '3-1200'
    
    # Laba Bersih
    Total_Revenue = df_tb_adj[df_tb_adj['Tipe_Num'].isin([4, 8])]['Kredit'].sum()
    Total_Expense = df_tb_adj[df_tb_adj['Tipe_Num'].isin([5, 6, 9])]['Debit'].sum()
    
    # Prive & Modal Awal
    Prive_Value = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_PRIVE]['Debit'].sum()
    Modal_Awal_Baris = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_MODAL]['Kredit'].sum()
    
    Net_Income = Total_Revenue - Total_Expense
    Modal_Baru = Modal_Awal_Baris + Net_Income - Prive_Value
    
    # Worksheet Columns
    df_ws_final = df_tb_adj.copy()
    df_ws_final['IS Debit'] = 0.0; df_ws_final['IS Kredit'] = 0.0
    df_ws_final['BS Debit'] = 0.0; df_ws_final['BS Kredit'] = 0.0
    
    IS_TYPES = [4, 5, 6, 8, 9]
    BS_TYPES = [1, 2, 3] 

    df_ws_final.loc[df_ws_final['Tipe_Num'].isin(IS_TYPES), 'IS Debit'] = df_ws_final['Debit']
    df_ws_final.loc[df_ws_final['Tipe_Num'].isin(IS_TYPES), 'IS Kredit'] = df_ws_final['Kredit']

    df_ws_final.loc[df_ws_final['Tipe_Num'].isin(BS_TYPES), 'BS Debit'] = df_ws_final['Debit']
    # Modal di BS adalah Modal Akhir
    df_ws_final.loc[df_ws_final['Kode Akun'] == AKUN_MODAL, 'BS Kredit'] = Modal_Baru
    # Akun real lain tetap
    df_ws_final.loc[(df_ws_final['Tipe_Num'].isin([1, 2])) | (df_ws_final['Kode Akun'] == AKUN_PRIVE), 'BS Kredit'] = df_ws_final['Kredit']
    
    df_laba_rugi = create_income_statement_df(df_tb_adj, Total_Revenue, Total_Expense, Net_Income)
    
    df_re = pd.DataFrame({
        'Deskripsi': ['Modal Awal', 'Laba Bersih Periode', 'Prive', 'Modal Akhir'],
        'Jumlah': [Modal_Awal_Baris, Net_Income, -Prive_Value, Modal_Baru]
    })
    
    df_laporan_posisi_keuangan = create_balance_sheet_df(df_tb_adj, Modal_Baru)

    return Net_Income, df_laba_rugi, df_re, df_laporan_posisi_keuangan, df_ws_final


def create_income_statement_df(df_tb_adj, Total_Revenue, Total_Expense, Net_Income):
    data = []
    df_is = df_tb_adj[df_tb_adj['Tipe_Num'].isin([4, 5, 6, 8, 9])].copy()
    
    def get_total(tipe_nums, col): return df_is[df_is['Tipe_Num'].isin(tipe_nums)][col].sum()
    def get_df(tipe_nums, col): return df_is[df_is['Tipe_Num'].isin(tipe_nums)].sort_values(by='Kode Akun')

    # Pendapatan
    data.append(['PENDAPATAN', '', ''])
    Total_4 = get_total([4], 'Kredit')
    for _, row in get_df([4], 'Kredit').iterrows():
        data.append([row['Nama Akun'], row['Kredit'], ''])
    data.append(['TOTAL PENDAPATAN', '', Total_4])
    
    # HPP
    Total_5 = get_total([5], 'Debit')
    data.append(['HARGA POKOK PENJUALAN', '', ''])
    for _, row in get_df([5], 'Debit').iterrows():
        data.append([row['Nama Akun'], row['Debit'], ''])
    data.append(['TOTAL COST OF GOODS SOLD', '', Total_5])

    Laba_Kotor = Total_4 - Total_5
    data.append(['LABA KOTOR', '', Laba_Kotor])

    # Beban Operasional
    Total_6 = get_total([6], 'Debit')
    data.append(['BEBAN OPERASIONAL', '', ''])
    for _, row in get_df([6], 'Debit').iterrows():
        data.append([row['Nama Akun'], row['Debit'], ''])
    data.append(['TOTAL BEBAN OPERASIONAL', '', Total_6])
    
    Laba_Operasi = Laba_Kotor - Total_6
    data.append(['LABA OPERASI', '', Laba_Operasi])

    # Lain-lain
    data.append(['PENDAPATAN DAN BEBAN LAIN-LAIN', '', ''])
    Total_8 = get_total([8], 'Kredit')
    for _, row in get_df([8], 'Kredit').iterrows():
        data.append([row['Nama Akun'], row['Kredit'], ''])
        
    Total_9 = get_total([9], 'Debit')
    for _, row in get_df([9], 'Debit').iterrows():
        data.append([row['Nama Akun'], -row['Debit'], ''])

    Net_Lain = Total_8 - Total_9
    data.append(['TOTAL PENDAPATAN & BEBAN LAIN', '', Net_Lain])
    
    data.append(['LABA BERSIH', '', Laba_Operasi + Net_Lain])

    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah', 'Total'])


def create_balance_sheet_df(df_tb_adj, Modal_Akhir):
    data = []
    df_bs = df_tb_adj[df_tb_adj['Tipe_Num'].isin([1, 2])].copy()
    
    data.append(['ASET', '', ''])
    
    # Aset Lancar (1-1xxx)
    data.append(['Aset Lancar', '', ''])
    df_ca = df_bs[df_bs['Kode Akun'].str.startswith('1-1')]
    for _, row in df_ca.iterrows():
        data.append([row['Nama Akun'], row['Debit'] - row['Kredit'], ''])
    Total_CA = df_ca['Debit'].sum() - df_ca['Kredit'].sum()
    data.append(['TOTAL ASET LANCAR', '', Total_CA])
    
    # Aset Tetap (1-2xxx)
    data.append(['Aset Tetap', '', ''])
    df_fa = df_bs[df_bs['Kode Akun'].str.startswith('1-2')]
    for _, row in df_fa.iterrows():
        val = row['Debit'] - row['Kredit'] # Akumulasi akan otomatis negatif karena Kredit > Debit
        data.append([row['Nama Akun'], val, ''])
    Total_FA = df_fa['Debit'].sum() - df_fa['Kredit'].sum()
    data.append(['TOTAL ASET TETAP', '', Total_FA])
    
    data.append(['TOTAL ASET', '', Total_CA + Total_FA])
    
    data.append(['LIABILITAS & EKUITAS', '', ''])
    
    # Liabilitas (2-xxx)
    data.append(['Liabilitas', '', ''])
    df_liab = df_bs[df_bs['Kode Akun'].str.startswith('2')]
    for _, row in df_liab.iterrows():
        data.append([row['Nama Akun'], row['Kredit'] - row['Debit'], ''])
    Total_Liab = df_liab['Kredit'].sum() - df_liab['Debit'].sum()
    data.append(['TOTAL LIABILITAS', '', Total_Liab])
    
    # Ekuitas
    data.append(['Ekuitas', '', ''])
    data.append(['Modal Pemilik Akhir', '', Modal_Akhir])
    
    data.append(['TOTAL LIABILITAS & EKUITAS', '', Total_Liab + Modal_Akhir])
    
    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah 1', 'Jumlah 2'])


def create_cash_flow_detailed(df_journal_merged):
    """
    Membuat Cash Flow Breakdown berdasarkan transaksi Akun Kas (1-1100).
    """
    # Ambil semua transaksi yang melibatkan Kas
    df_cash = df_journal_merged[df_journal_merged['account_code'] == '1-1100'].copy()
    
    # Pisahkan Masuk dan Keluar
    # Debit di Kas = Masuk, Kredit di Kas = Keluar
    
    # OPERASI
    # Asumsi: Kas Masuk dengan deskripsi 'Penjualan', 'Jasa' -> Operasi
    # Kas Keluar dengan deskripsi 'Beban', 'Gaji', 'Listrik', 'Pakan' -> Operasi
    
    # INVESTASI
    # Beli Aset Tetap -> Investasi
    
    # PENDANAAN
    # Prive, Utang -> Pendanaan
    
    operating_in = []
    operating_out = []
    investing = []
    financing = []
    
    for _, row in df_cash.iterrows():
        desc = str(row['description_entry']).lower()
        amount = row['debit_amount'] if row['debit_amount'] > 0 else -row['credit_amount']
        
        # KATEGORISASI SEDERHANA BERDASARKAN DESKRIPSI
        if 'prive' in desc or 'utang' in desc or 'pinjaman' in desc or 'angsuran' in desc:
            financing.append((row['description_entry'], amount))
        elif 'aset' in desc or 'tanah' in desc or 'bangunan' in desc:
            investing.append((row['description_entry'], amount))
        else:
            # Default ke Operasi (Penjualan, Beban, Gaji, dll)
            if amount > 0:
                operating_in.append((row['description_entry'], amount))
            else:
                operating_out.append((row['description_entry'], amount))
                
    data = []
    
    # 1. OPERASI
    data.append(['ARUS KAS DARI AKTIVITAS OPERASI', '', ''])
    data.append(['Penerimaan Kas:', '', ''])
    total_op_in = 0
    for desc, val in operating_in:
        data.append([f"  - {desc}", val, ''])
        total_op_in += val
        
    data.append(['Pengeluaran Kas:', '', ''])
    total_op_out = 0
    for desc, val in operating_out:
        data.append([f"  - {desc}", val, ''])
        total_op_out += val
        
    net_op = total_op_in + total_op_out
    data.append(['Arus Kas Bersih dari Operasi', '', net_op])
    
    # 2. INVESTASI
    data.append(['ARUS KAS DARI AKTIVITAS INVESTASI', '', ''])
    net_inv = 0
    for desc, val in investing:
        data.append([f"  - {desc}", val, ''])
        net_inv += val
    data.append(['Arus Kas Bersih dari Investasi', '', net_inv])
        
    # 3. PENDANAAN
    data.append(['ARUS KAS DARI AKTIVITAS PENDANAAN', '', ''])
    net_fin = 0
    for desc, val in financing:
        data.append([f"  - {desc}", val, ''])
        net_fin += val
    data.append(['Arus Kas Bersih dari Pendanaan', '', net_fin])
    
    net_increase = net_op + net_inv + net_fin
    data.append(['KENAIKAN (PENURUNAN) BERSIH KAS', '', net_increase])
    
    # Saldo Awal Kas (Harusnya 0 jika ini bulan pertama, atau ambil saldo sebelumnya)
    # Di sistem ini, Saldo Awal Kas sudah termasuk di transaksi 'Saldo Awal', jadi 
    # yang kita hitung diatas sudah termasuk saldo awal jika range tanggal mencakupnya.
    # Namun untuk format standar, kita pisahkan Saldo Awal ID 99.
    
    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah', 'Total'])


def create_inventory_movement_report(df_movements):
    if df_movements.empty:
        return pd.DataFrame()
        
    if 'movement_date' in df_movements.columns:
        df_movements['movement_date'] = pd.to_datetime(df_movements['movement_date']).dt.strftime('%Y-%m-%d')

    df_movements['product_name'] = df_movements['products'].apply(lambda x: x.get('name') if isinstance(x, dict) else 'Unknown')
    df_movements = df_movements.sort_values(by=['movement_date', 'product_name'], ascending=[True, True])
    
    report_data = []
    for product_id, group in df_movements.groupby('product_id'):
        product_name = group['product_name'].iloc[0]
        running_qty = 0
        running_value = 0
        
        for _, row in group.iterrows():
            qty = row['quantity_change']
            cost = row['unit_cost']
            running_qty += qty
            running_value += (qty * cost)
            
            report_data.append({
                "Nama Produk": product_name,
                "Tanggal": row['movement_date'],
                "Jenis": row['movement_type'],
                "Ref": row['reference_id'],
                "Masuk": qty if row['movement_type'] == 'RECEIPT' else 0,
                "Keluar": abs(qty) if row['movement_type'] == 'ISSUE' else 0,
                "Biaya": cost,
                "Total": abs(qty * cost),
                "Saldo Qty": running_qty,
                "Saldo Nilai": running_value
            })
            
    df_rep = pd.DataFrame(report_data)
    if df_rep.empty: return df_rep
    
    df_rep.columns = ['Produk', 'Tanggal', 'Jenis', 'Ref', 'Masuk', 'Keluar', 'Harga', 'Total', 'Saldo Qty', 'Saldo Nilai']
    
    is_first = df_rep.groupby('Produk').cumcount() == 0
    df_rep['Produk'] = np.where(is_first, df_rep['Produk'], '')
    
    return df_rep


def to_excel_bytes(reports):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for sheet_name, df in reports.items():
            if isinstance(df, pd.DataFrame):
                clean_name = sheet_name.replace(" ", "_")[:30]
                df.to_excel(writer, sheet_name=clean_name, index=False)
    return output.getvalue()


def generate_reports():
    today = date.today()
    if "end_date" not in st.session_state: st.session_state.end_date = today
    if "start_date" not in st.session_state: st.session_state.start_date = today.replace(day=1)
    
    start_date = st.sidebar.date_input("Tanggal Mulai", value=st.session_state.start_date)
    end_date = st.sidebar.date_input("Tanggal Akhir", value=st.session_state.end_date)
    
    df_merged, df_coa, df_moves = get_base_data_and_filter(start_date, end_date)
    
    # Generate
    df_gj = create_general_journal_report(df_merged)
    df_gl = create_general_ledger_report(df_merged, df_coa)
    
    # Hitung Neraca Saldo (Adjusted langsung karena AJP sudah di DB)
    df_tb = calculate_trial_balance(df_merged, df_coa)
    
    # Karena AJP sudah masuk sebagai transaksi jurnal biasa di DB (ID 200+),
    # Maka df_tb yang kita hitung sebenarnya SUDAH Adjusted.
    # Untuk keperluan tampilan "Worksheet" klasik (TB Before -> Adj -> TB After),
    # kita perlu memisahkan transaksi AJP.
    
    # Pisahkan AJP (ID >= 200) dan Before AJP (ID < 200)
    df_merged_before = df_merged[df_merged['journal_id'] < 200]
    df_merged_ajp = df_merged[df_merged['journal_id'] >= 200]
    
    df_tb_before = calculate_trial_balance(df_merged_before, df_coa)
    df_adj_only = calculate_trial_balance(df_merged_ajp, df_coa) # Ini adjustment saja
    
    # Gabung ke Worksheet DataFrame
    df_ws = df_coa[['account_code', 'account_name', 'account_type', 'normal_balance']].copy()
    df_ws.columns = ['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Saldo Normal']
    
    df_ws = df_ws.merge(df_tb_before[['Kode Akun', 'Debit', 'Kredit', 'Tipe_Num']], on='Kode Akun', how='left', suffixes=('', '_Before')).fillna(0)
    df_ws.rename(columns={'Debit': 'TB Debit', 'Kredit': 'TB Kredit'}, inplace=True)
    
    df_ws = df_ws.merge(df_adj_only[['Kode Akun', 'Debit', 'Kredit']], on='Kode Akun', how='left', suffixes=('', '_Adj')).fillna(0)
    df_ws.rename(columns={'Debit': 'MJ Debit', 'Kredit': 'MJ Kredit'}, inplace=True)
    
    # Hitung TB Adjusted (Kolom Worksheet)
    # Logika: TB + MJ = TB_Adj
    def calc_adj(row):
        net = (row['TB Debit'] - row['TB Kredit']) + (row['MJ Debit'] - row['MJ Kredit'])
        if row['Saldo Normal'] == 'Debit': return max(0, net), max(0, -net)
        else: return max(0, -net), max(0, net)
        
    df_ws[['Debit', 'Kredit']] = df_ws.apply(lambda x: calc_adj(x), axis=1, result_type='expand')
    # Sekarang df_ws['Debit'] dan ['Kredit'] adalah TB Adjusted yang benar
    
    # Gunakan df_ws (TB Adj) untuk laporan keuangan
    # Kita perlu format standar DataFrame untuk fungsi calc reports
    df_tb_adj_final = df_ws[['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Debit', 'Kredit', 'Tipe_Num']].copy()
    
    net_inc, df_is, df_re, df_bs, _ = calculate_closing_and_reporting_data(df_tb_adj_final)
    
    # Worksheet Final Display
    df_ws_display = df_ws[['Kode Akun', 'Nama Akun', 'TB Debit', 'TB Kredit', 'MJ Debit', 'MJ Kredit', 'Debit', 'Kredit']]
    df_ws_display.rename(columns={'Debit': 'TB ADJ Debit', 'Kredit': 'TB ADJ Kredit'}, inplace=True)
    
    # Cash Flow (Detailed)
    df_cf = create_cash_flow_detailed(df_merged)
    
    # Inventory
    df_inv = create_inventory_movement_report(df_moves)

    return {
        "Laba Bersih": net_inc,
        "Jurnal Umum": df_gj,
        "Buku Besar": df_gl,
        "Kertas Kerja": df_ws_display,
        "Laporan Laba Rugi": df_is,
        "Laporan Perubahan Modal": df_re,
        "Laporan Posisi Keuangan": df_bs,
        "Laporan Arus Kas": df_cf,
        "Kartu Persediaan": df_inv
    }


def show_reports_page():
    st.title("📊 Laporan Keuangan")
    st.sidebar.header("Filter")
    
    reports = generate_reports()
    
    # Formatter
    def fmt_df(df):
        df_show = df.copy()
        cols = [c for c in df_show.columns if 'Debit' in c or 'Kredit' in c or 'Jumlah' in c or 'Total' in c or 'Saldo' in c or 'Biaya' in c or 'Harga' in c]
        for c in cols:
            df_show[c] = df_show[c].apply(lambda x: format_rupiah(x) if isinstance(x, (int, float)) else x)
        return df_show

    # Display
    st.header("1. Jurnal Umum")
    st.dataframe(fmt_df(reports["Jurnal Umum"]), use_container_width=True, hide_index=True)
    
    st.header("2. Buku Besar")
    st.dataframe(fmt_df(reports["Buku Besar"]), use_container_width=True, hide_index=True)
    
    st.header("3. Kertas Kerja (Worksheet)")
    st.dataframe(fmt_df(reports["Kertas Kerja"]), use_container_width=True, hide_index=True)
    
    col1, col2 = st.columns(2)
    with col1:
        st.header("4. Laporan Laba Rugi")
        st.dataframe(fmt_df(reports["Laporan Laba Rugi"]), use_container_width=True, hide_index=True)
        
        st.header("5. Laporan Perubahan Modal")
        st.dataframe(fmt_df(reports["Laporan Perubahan Modal"]), use_container_width=True, hide_index=True)
        
    with col2:
        st.header("6. Laporan Posisi Keuangan")
        st.dataframe(fmt_df(reports["Laporan Posisi Keuangan"]), use_container_width=True, hide_index=True)

    st.header("7. Laporan Arus Kas")
    st.dataframe(fmt_df(reports["Laporan Arus Kas"]), use_container_width=True, hide_index=True)
    
    st.header("8. Kartu Persediaan")
    st.dataframe(fmt_df(reports["Kartu Persediaan"]), use_container_width=True, hide_index=True)
    
    st.download_button("📥 Download Excel", data=to_excel_bytes(reports), file_name="Laporan_Keuangan.xlsx")

if __name__ == "__main__":
    show_reports_page()
