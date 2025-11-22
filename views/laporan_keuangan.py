import streamlit as st
import pandas as pd
from supabase_client import supabase
from io import BytesIO
from datetime import date, timedelta
import numpy as np

# --- UTILITY FUNCTIONS ---

@st.cache_data
def fetch_all_accounting_data():
    """Mengambil semua data yang diperlukan dari Supabase dan mengonversi tipe data."""
    
    try:
        journal_lines_response = supabase.table("journal_lines").select("*").execute()
        journal_entries_response = supabase.table("journal_entries").select("id, transaction_date, description, order_id").execute()
        coa_response = supabase.table("chart_of_accounts").select("*").execute()
        inventory_response = supabase.table("inventory_movements").select("*, products(name)").execute()
        
        df_entries = pd.DataFrame(journal_entries_response.data)
        # Konversi Tanggal di Sumber (di dalam cache)
        df_entries['transaction_date'] = pd.to_datetime(df_entries['transaction_date'], errors='coerce').dt.normalize()
        
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
    """Mengambil dan menggabungkan data jurnal, lalu memfilter berdasarkan tanggal."""
    data = fetch_all_accounting_data()
    df_lines = data["journal_lines"]
    df_entries = data["journal_entries"].copy()
    df_coa = data["coa"]
    df_movements = data["inventory_movements"]
    
    # Cek data utama
    if df_entries.empty or df_lines.empty:
        empty_merged = pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount'])
        return empty_merged, df_coa, df_movements
        
    # --- PERBAIKAN FINAL (Mengatasi TypeError): Memaksa konversi tipe data DATETIME ---
    # Meskipun sudah dikonversi di cache, kita ulangi untuk memastikan tipe data tetap 'datetime64[ns]'
    df_entries['transaction_date'] = pd.to_datetime(df_entries['transaction_date'], errors='coerce').dt.normalize()
    
    # 1. Filter entri jurnal berdasarkan rentang tanggal
    df_filtered_entries = df_entries.loc[
        (df_entries['transaction_date'] >= pd.to_datetime(start_date)) & 
        (df_entries['transaction_date'] <= pd.to_datetime(end_date))
    ].copy()

    df_saldo_awal = df_entries.loc[df_entries['id'] == 5].copy()

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


def calculate_closing_and_tb_after_closing(df_tb_adj):
    """
    Menghitung Jurnal Penutup (Closing Journal) dan Neraca Saldo Setelah Penutup.
    """
    
    AKUN_MODAL = '3-1100'
    AKUN_PRIVE = '3-1200'
    AKUN_IKHTISAR_LR = '3-1300'
    
    # 1. HITUNG LABA BERSIH DARI TB ADJ
    
    Total_Revenue = df_tb_adj[df_tb_adj['Tipe_Num'].isin([4, 8])]['Kredit'].sum()
    Total_Expense = df_tb_adj[df_tb_adj['Tipe_Num'].isin([5, 6, 9])]['Debit'].sum()
    Prive_Value = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_PRIVE]['Debit'].sum()
    Modal_Awal_Baris = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_MODAL]['Kredit'].sum()
    
    Net_Income = Total_Revenue - Total_Expense
    
    # 2. GENERATE NERACA SALDO SETELAH PENUTUP (TB CLOSING)
    df_tb_closing = df_tb_adj.copy()
    
    # Tutup Akun Temporer (Set Debit/Kredit = 0)
    df_tb_closing.loc[df_tb_closing['Tipe_Num'].isin([4, 5, 6, 8, 9]) | (df_tb_closing['Kode Akun'].isin([AKUN_PRIVE, AKUN_IKHTISAR_LR])), 
                        ['TB ADJ Debit', 'TB ADJ Kredit']] = 0.0

    # Sesuaikan Saldo Modal Akhir
    Modal_Baru = Modal_Awal_Baris + Net_Income - Prive_Value
    
    df_tb_closing.loc[df_tb_closing['Kode Akun'] == AKUN_MODAL, 'TB ADJ Kredit'] = Modal_Baru
    df_tb_closing.loc[df_tb_closing['Kode Akun'] == AKUN_MODAL, 'TB ADJ Debit'] = 0.0
    
    df_tb_closing.columns = ['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Debit', 'Kredit', 'MJ Debit', 'MJ Kredit', 'TB CLOSING Debit', 'TB CLOSING Kredit', 'Tipe_Num']

    # Laporan Keuangan Akhir
    df_laba_rugi = create_income_statement_df(df_tb_adj, Total_Revenue, Total_Expense, Net_Income)
    df_re = pd.DataFrame({
        'Deskripsi': ['Modal Awal', 'Laba Bersih Periode', 'Prive', 'Modal Akhir'],
        'Jumlah': [Modal_Awal_Baris, Net_Income, -Prive_Value, Modal_Baru]
    })
    df_laporan_posisi_keuangan = create_balance_sheet_df(df_tb_adj, Modal_Baru)


    return df_tb_closing, Net_Income, df_laba_rugi, df_re, df_laporan_posisi_keuangan


def create_income_statement_df(df_tb_adj, Total_Revenue, Total_Expense, Net_Income):
    """Membuat DataFrame yang rapi untuk Laporan Laba Rugi."""
    data = []
    
    df_is = df_tb_adj[df_tb_adj['Tipe_Num'].isin([4, 5, 6, 8, 9])].copy()
    
    def get_saldo_and_sum(df, tipe_nums, saldo_col='Debit'):
        df_filtered = df[df['Tipe_Num'].isin(tipe_nums)]
        total = df_filtered[saldo_col].sum()
        return total, df_filtered

    # PENDAPATAN (Akun 4, 8)
    Total_4, df_4 = get_saldo_and_sum(df_is, [4], 'Kredit')
    Total_8, df_8 = get_saldo_and_sum(df_is, [8], 'Kredit')
    Total_Revenue = Total_4 + Total_8

    data.append(['PENDAPATAN', '', ''])
    for index, row in df_4.iterrows():
        data.append([row['Nama Akun'], row['Kredit'], ''])
    data.append(['TOTAL PENDAPATAN LAIN-LAIN', Total_8, ''])
    data.append(['TOTAL PENDAPATAN', '', Total_Revenue])

    # HPP (Akun 5)
    Total_5, df_5 = get_saldo_and_sum(df_is, [5], 'Debit')
    data.append(['HARGA POKOK PENJUALan', '', ''])
    for index, row in df_5.iterrows():
        data.append([row['Nama Akun'], row['Debit'], ''])
    data.append(['TOTAL HPP', '', Total_5])

    Laba_Kotor = Total_Revenue - Total_5
    data.append(['LABA KOTOR', '', Laba_Kotor])

    # BEBAN OPERASIONAL (Akun 6)
    Total_6, df_6 = get_saldo_and_sum(df_is, [6], 'Debit')
    data.append(['BEBAN OPERASIONAL', '', ''])
    for index, row in df_6.iterrows():
        data.append([row['Nama Akun'], row['Debit'], ''])
