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
    
    # Mengambil data jurnal, COA, dan inventory movements
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
        # Kembalikan DataFrame kosong jika gagal
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
    
    # Cek data utama
    if df_entries.empty or df_lines.empty:
        st.warning("Tidak ada entri jurnal atau baris jurnal yang ditemukan di database.")
        empty_merged = pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount'])
        return empty_merged, df_coa, df_movements
        
    try:
        # Konversi tanggal dengan penanganan error
        df_entries['transaction_date'] = pd.to_datetime(df_entries['transaction_date'], errors='coerce').dt.normalize()
    except KeyError:
        st.error("Error: Kolom 'transaction_date' hilang atau salah nama di tabel journal_entries.")
        return pd.DataFrame(), df_coa, df_movements
    
    # 1. Filter entri jurnal berdasarkan rentang tanggal
    df_filtered_entries = df_entries[
        (df_entries['transaction_date'] >= pd.to_datetime(start_date)) & 
        (df_entries['transaction_date'] <= pd.to_datetime(end_date))
    ].copy()

    # 2. Sisakan Saldo Awal (Asumsi Jurnal ID 5 adalah Saldo Awal) terlepas dari rentang tanggal
    df_saldo_awal = df_entries[df_entries['id'] == 5].copy()

    # Gabungkan entri yang difilter dengan Saldo Awal
    df_journal_entries_final = pd.concat([df_filtered_entries, df_saldo_awal]).drop_duplicates(subset=['id'], keep='first')
        
    # Pencegahan Merge Error
    if df_journal_entries_final.empty:
        st.warning(f"Tidak ada transaksi yang ditemukan antara {start_date.strftime('%Y-%m-%d')} dan {end_date.strftime('%Y-%m-%d')}.")
        empty_merged = pd.DataFrame(columns=df_lines.columns.tolist() + df_entries.columns.tolist() + df_coa.columns.tolist())
        return empty_merged, df_coa, df_movements

    # 3. Gabungan Jurnal Utama yang sudah difilter
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
        
        df_tb = df_tb.merge(df_coa, on='account_code', how='right').fillna(0) # Join ke COA untuk semua akun
        
        # Logika perhitungan Saldo Bersih dan Saldo Normal
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


def calculate_financial_statements(df_ws):
    """Menghitung Laba Rugi, Perubahan Modal, dan Posisi Keuangan dari Worksheet (TB ADJ)."""
    
    # Mengambil nilai TB ADJ untuk perhitungan
    df_tb_adj = df_ws[['Kode Akun', 'Nama Akun', 'Tipe Akun', 'TB ADJ Debit', 'TB ADJ Kredit']].copy()
    df_tb_adj['Saldo Bersih'] = df_tb_adj['TB ADJ Debit'] - df_tb_adj['TB ADJ Kredit']

    # --- 1. LAPORAN LABA RUGI (INCOME STATEMENT) ---
    # Akun Tipe 4 (Pendapatan), 5 (HPP), 6 (Beban), 8 (Pend. Lain), 9 (Beban Lain)
    df_is = df_tb_adj[df_tb_adj['Tipe Akun'].astype(str).str.startswith(('4', '5', '6', '8', '9'))].copy()
    
    Total_Pendapatan = df_is[df_is['Tipe Akun'].astype(str).str.startswith('4')]['Saldo Bersih'].sum() * -1
    Total_HPP = df_is[df_is['Tipe Akun'].astype(str).str.startswith('5')]['Saldo Bersih'].sum()
    Total_Beban_Op = df_is[df_is['Tipe Akun'].astype(str).str.startswith('6')]['Saldo Bersih'].sum()
    
    Laba_Kotor = Total_Pendapatan - Total_HPP
    Laba_Operasi = Laba_Kotor - Total_Beban_Op
    
    # Pendapatan/Beban Lain-lain
    Pendapatan_Lain = df_is[df_is['Tipe Akun'].astype(str).str.startswith('8')]['Saldo Bersih'].sum() * -1
    Beban_Lain = df_is[df_is['Tipe Akun'].astype(str).str.startswith('9')]['Saldo Bersih'].sum()
    
    Laba_Bersih = Laba_Operasi + Pendapatan_Lain - Beban_Lain
    
    # --- 2. LAPORAN PERUBAHAN MODAL (
