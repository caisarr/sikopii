import streamlit as st
import pandas as pd
from supabase_client import supabase
from io import BytesIO
from datetime import date, timedelta
import numpy as np

# --- UTILITY FUNCTIONS ---

@st.cache_data
def fetch_all_accounting_data():
    """Mengambil semua data yang diperlukan dari Supabase."""
    
    try:
        journal_lines_response = supabase.table("journal_lines").select("*").execute()
        journal_entries_response = supabase.table("journal_entries").select("id, transaction_date, description, order_id").execute()
        coa_response = supabase.table("chart_of_accounts").select("*").execute()
        inventory_response = supabase.table("inventory_movements").select("*, products(name)").execute()
        
        return {
            "journal_lines": pd.DataFrame(journal_lines_response.data).fillna(0),
            "journal_entries": pd.DataFrame(journal_entries_response.data),
            "coa": pd.DataFrame(coa_response.data),
            "inventory_movements": pd.DataFrame(inventory_response.data),
        }
    except Exception as e:
        st.error(f"Gagal mengambil data dari Supabase: {e}")
        empty_merged = pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount'])
        return {
            "journal_lines": pd.DataFrame(),
            "journal_entries": pd.DataFrame(),
            "coa": pd.DataFrame(columns=['account_code', 'account_name', 'account_type', 'normal_balance']),
            "inventory_movements": pd.DataFrame(),
        }


def get_base_data_and_filter(start_date, end_date):
    """Mengambil dan menggabungkan data jurnal, lalu memfilter berdasarkan tanggal."""
    data = fetch_all_accounting_data()
    df_lines = data["journal_lines"]
    df_entries = data["journal_entries"]
    df_coa = data["coa"]
    df_movements = data["inventory_movements"]
    
    if df_entries.empty or df_lines.empty:
        empty_merged = pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount'])
        return empty_merged, df_coa, df_movements
        
    try:
        df_entries['transaction_date'] = pd.to_datetime(df_entries['transaction_date'], errors='coerce').dt.normalize()
    except KeyError:
        return pd.DataFrame(), df_coa, df_movements
    
    # 1. Filter entri jurnal berdasarkan rentang tanggal
    df_filtered_entries = df_entries[
        (df_entries['transaction_date'] >= pd.to_datetime(start_date)) & 
        (df_entries['transaction_date'] <= pd.to_datetime(end_date))
    ].copy()

    # 2. Sisakan Saldo Awal (Asumsi Jurnal ID 5 adalah Saldo Awal)
    df_saldo_awal = df_entries[df_entries['id'] == 5].copy()

    df_journal_entries_final = pd.concat([df_filtered_entries, df_saldo_awal]).drop_duplicates(subset=['id'], keep='first')
        
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
        
        df_tb['Saldo Bersih'] = df_tb['Total_Debit'] - df_tb['Total_Kredit']
        
        df_tb['Debit'] = df_tb.apply(
            lambda row: row['Saldo Bersih'] if row['normal_balance'] == 'Debit' and row['Saldo Bersih'] >= 0 else 
                        -row['Saldo Bersih'] if row['normal_balance'] == 'Credit' and row['Saldo Bersih'] < 0 else 0, axis=1
        )
        df_tb['Kredit'] = df_tb.apply(
            lambda row: row['Saldo Bersih'] if row['normal_balance'] == 'Credit' and row['Saldo Bersih'] >= 0 else 
                        -row['Saldo Bersih'] if row['normal_balance'] == 'Debit' and row['Saldo Bersih'] < 0 else 0, axis=1
        )
        
    df_tb = df_tb[['account_code', 'account_name', 'account_type', 'Debit', 'Kredit']].sort_values(by='account_code')
    df_tb.columns = ['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Debit', 'Kredit']
    
    return df_tb


def calculate_closing_and_tb_after_closing(df_tb_adj):
    """
    Menghitung Jurnal Penutup (Closing Journal) dan Neraca Saldo Setelah Penutup.
    Ini adalah proses virtual (non-database).
    """
    
    # Kunci Akun
    AKUN_MODAL = '3-1100'
    AKUN_PRIVE = '3-1200'
    AKUN_IKHTISAR_LR = '3-1300'
    
    # 1. HITUNG LABA BERSIH DARI TB ADJ
    df_temp = df_tb_adj.copy()
    
    # Total Pendapatan (Akun 4 dan 8)
    Total_Revenue = df_temp[df_temp['Kode Akun'].astype(str).str.startswith(('4', '8'))]['Kredit'].sum()
    
    # Total Beban (Akun 5, 6, 9)
    Total_Expense = df_temp[df_temp['Kode Akun'].astype(str).str.startswith(('5', '6', '9'))]['Debit'].sum()
    
    Net_Income = Total_Revenue - Total_Expense
    
    # 2. GENERATE JURNAL PENUTUP (VIRTUAL)
    cj_data = []

    # CJ 1: Tutup Pendapatan (Debit Pendapatan, Kredit Ikhtisar L/R)
    for index, row in df_temp[df_temp['Tipe Akun'].astype(str).str.startswith(('4', '8'))].iterrows():
        if row['Kredit'] > 0:
            cj_data.append({'Kode Akun': row['Kode Akun'], 'Nama Akun': row['Nama Akun'], 'Debit': row['Kredit'], 'Kredit': 0.0})
    cj_data.append({'Kode Akun': AKUN_IKHTISAR_LR, 'Nama Akun': 'Ikhtisar laba/rugi', 'Debit': 0.0, 'Kredit': Total_Revenue})

    # CJ 2: Tutup Beban (Debit Ikhtisar L/R, Kredit Beban)
    cj_data.append({'Kode Akun': AKUN_IKHTISAR_LR, 'Nama Akun': 'Ikhtisar laba/rugi', 'Debit': Total_Expense, 'Kredit': 0.0})
    for index, row in df_temp[df_temp['Tipe Akun'].astype(str).str.startswith(('5', '6', '9'))].iterrows():
        if row['Debit'] > 0:
            cj_data.append({'Kode Akun': row['Kode Akun'], 'Nama Akun': row['Nama Akun'], 'Debit': 0.0, 'Kredit': row['Debit']})
            
    # CJ 3: Tutup Ikhtisar L/R ke Modal
    if Net_Income > 0:
        cj_data.append({'Kode Akun': AKUN_IKHTISAR_LR, 'Nama Akun': 'Ikhtisar laba/rugi', 'Debit': Net_Income, 'Kredit': 0.0})
        cj_data.append({'Kode Akun': AKUN_MODAL, 'Nama Akun': 'Modal Pemilik', 'Debit': 0.0, 'Kredit': Net_Income})
    elif Net_Income < 0:
        cj_data.append({'Kode Akun': AKUN_MODAL, 'Nama Akun': 'Modal Pemilik', 'Debit': -Net_Income, 'Kredit': 0.0})
        cj_data.append({'Kode Akun': AKUN_IKHTISAR_LR, 'Nama Akun': 'Ikhtisar laba/rugi', 'Debit': 0.0, 'Kredit': -Net_Income})
        
    # CJ 4: Tutup Prive (Debit Modal, Kredit Prive)
    Prive_Value = df_temp[df_temp['Kode Akun'] == AKUN_PRIVE]['Debit'].sum()
    if Prive_Value > 0:
        cj_data.append({'Kode Akun': AKUN_MODAL, 'Nama Akun': 'Modal Pemilik', 'Debit': Prive_Value, 'Kredit': 0.0})
        cj_data.append({'Kode Akun': AKUN_PRIVE, 'Nama Akun': 'Prive pemilik', 'Debit': 0.0, 'Kredit': Prive_Value})
    
    df_closing_journal = pd.DataFrame(cj_data)

    # 3. GENERATE NERACA SALDO SETELAH PENUTUP (TB CLOSING)
    
    # Mulai dari TB After Adj
    df_tb_closing = df_tb_adj.copy()
    
    # Tutup Akun Temporer (Set Debit/Kredit = 0)
    # Akun Pendapatan, Beban, Ikhtisar L/R, Prive
    df_tb_closing.loc[df_tb_closing['Tipe Akun'].astype(str).str.startswith(('4', '5', '6', '8', '9', '3-1200', '3-1300')), ['TB ADJ Debit', 'TB ADJ Kredit']] = 0.0

    # Sesuaikan Saldo Modal Akhir
    Modal_Lama = df_tb_closing[df_tb_closing['Kode Akun'] == AKUN_MODAL]['TB ADJ Kredit'].sum()
    
    Modal_Baru = Modal_Lama + Net_Income - Prive_Value
    
    df_tb_closing.loc[df_tb_closing['Kode Akun'] == AKUN_MODAL, 'TB ADJ Kredit'] = Modal_Baru
    df_tb_closing.loc[df_tb_closing['Kode Akun'] == AKUN_MODAL, 'TB ADJ Debit'] = 0.0

    df_tb_closing.columns = ['Kode Akun', 'Nama Akun', 'Tipe Akun', 'TB Debit', 'TB Kredit', 'MJ Debit', 'MJ Kredit', 'TB CLOSING Debit', 'TB CLOSING Kredit']

    return df_closing_journal, df_tb_closing, Net_Income

# ... (Sisa fungsi lainnya, seperti to_excel_bytes, show_reports_page, dan General Ledger, perlu disesuaikan untuk menggunakan fungsi baru ini) ...


def show_reports_page():
    st.title("📊 Laporan Keuangan & Akuntansi Lengkap")
    
    st.sidebar.header("Filter Tanggal Laporan")
    
    reports, df_journal_merged, df_coa = generate_reports()
    
    # ... (Tampilan laporan di sini) ...

    # --- Tampilkan Jurnal Penutup dan TB Closing ---
    df_cj, df_tb_closing, net_income = calculate_closing_and_tb_after_closing(reports["Worksheet (Kertas Kerja)"])
    
    st.markdown("---")
    st.header("5. Jurnal Penutup (Closing Journal)")
    st.info(f"Laba Bersih yang ditutup: Rp {net_income:,.0f}")
    st.dataframe(df_cj, use_container_width=True)
    
    st.header("6. Neraca Saldo Setelah Penutup (TB CLOSING)")
    st.dataframe(df_tb_closing[['Kode Akun', 'Nama Akun', 'TB CLOSING Debit', 'TB CLOSING Kredit']], use_container_width=True)

    # ... (Tambahkan kembali tombol download Excel yang disesuaikan) ...

if __name__ == "__main__":
    show_reports_page()
