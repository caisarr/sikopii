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
        
        # --- PERBAIKAN: Konversi Tanggal di Sumber ---
        df_entries = pd.DataFrame(journal_entries_response.data)
        # Pastikan kolom transaction_date selalu bertipe datetime64[ns]
        df_entries['transaction_date'] = pd.to_datetime(df_entries['transaction_date'], errors='coerce').dt.normalize()
        
        return {
            "journal_lines": pd.DataFrame(journal_lines_response.data).fillna(0),
            "journal_entries": df_entries, # Gunakan DataFrame yang sudah dikonversi
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
    df_entries = data["journal_entries"]
    df_coa = data["coa"]
    df_movements = data["inventory_movements"]
    
    # Cek data utama
    if df_entries.empty or df_lines.empty:
        empty_merged = pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount'])
        return empty_merged, df_coa, df_movements
        
    # --- PERBAIKAN: Hapus Konversi Tanggal di Sini ---
    # Sekarang, kita hanya perlu membandingkan karena df_entries sudah bertipe datetime

    df_filtered_entries = df_entries[
        (df_entries['transaction_date'] >= pd.to_datetime(start_date)) & 
        (df_entries['transaction_date'] <= pd.to_datetime(end_date))
    ].copy()

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
    data.append(['HARGA POKOK PENJUALAN', '', ''])
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
    data.append(['TOTAL BEBAN OPERASIONAL', '', Total_6])
    
    Laba_Operasi = Laba_Kotor - Total_6
    data.append(['LABA OPERASI', '', Laba_Operasi])

    # BEBAN LAIN-LAIN (Akun 9)
    Total_9, df_9 = get_saldo_and_sum(df_is, [9], 'Debit')
    data.append(['BEBAN LAIN-LAIN', '', ''])
    for index, row in df_9.iterrows():
        data.append([row['Nama Akun'], row['Debit'], ''])
    data.append(['TOTAL BEBAN LAIN-LAIN', '', Total_9])
    
    Laba_Bersih_Hitung = Laba_Operasi + Total_8 - Total_9

    data.append(['LABA BERSIH', '', Laba_Bersih_Hitung])

    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah', 'Total'])


def create_balance_sheet_df(df_tb_adj, Modal_Akhir):
    """Membuat DataFrame yang rapi untuk Laporan Posisi Keuangan."""
    data = []
    
    df_bs = df_tb_adj[df_tb_adj['Tipe_Num'].isin([1, 2])].copy()

    # ASET (Akun 1)
    data.append(['ASET', '', ''])
    
    # Aset Lancar (Kode Akun 1-1XXX)
    data.append(['Aset Lancar', '', ''])
    df_current_asset = df_bs[df_bs['Kode Akun'].astype(str).str.startswith('1-1')].copy()
    for index, row in df_current_asset.iterrows():
        data.append([row['Nama Akun'], row['Debit'] - row['Kredit'], ''])
    Total_Aset_Lancar = df_current_asset['Debit'].sum() - df_current_asset['Kredit'].sum()
    data.append(['TOTAL ASET LANCAR', '', Total_Aset_Lancar])
    
    # Aset Tetap (Kode Akun 1-2XXX)
    data.append(['Aset Tetap', '', ''])
    df_fixed_asset = df_bs[df_bs['Kode Akun'].astype(str).str.startswith('1-2')].copy()
    for index, row in df_fixed_asset.iterrows():
        data.append([row['Nama Akun'], row['Debit'] - row['Kredit'], ''])
    Total_Aset_Tetap = df_fixed_asset['Debit'].sum() - df_fixed_asset['Kredit'].sum()
    data.append(['TOTAL ASET TETAP', '', Total_Aset_Tetap])

    Total_Aset = Total_Aset_Lancar + Total_Aset_Tetap
    data.append(['TOTAL ASET', '', Total_Aset])
    data.append(['', '', ''])
    
    # LIABILITAS & EKUITAS
    data.append(['LIABILITAS & EKUITAS', '', ''])
    
    # Liabilitas Lancar (Kode Akun 2-1XXX)
    data.append(['Liabilitas Lancar', '', ''])
    df_current_liab = df_bs[df_bs['Kode Akun'].astype(str).str.startswith('2-1')].copy()
    for index, row in df_current_liab.iterrows():
        data.append([row['Nama Akun'], row['Kredit'] - row['Debit'], ''])
    Total_Liabilitas_Lancar = df_current_liab['Kredit'].sum() - df_current_liab['Debit'].sum()
    data.append(['TOTAL LIABILITAS LANCAR', '', Total_Liabilitas_Lancar])

    # Liabilitas Jangka Panjang (Kode Akun 2-2XXX)
    data.append(['Liabilitas Jangka Panjang', '', ''])
    df_long_liab = df_bs[df_bs['Kode Akun'].astype(str).str.startswith('2-2')].copy()
    for index, row in df_long_liab.iterrows():
        data.append([row['Nama Akun'], row['Kredit'] - row['Debit'], ''])
    Total_Liabilitas_Jangka_Panjang = df_long_liab['Kredit'].sum() - df_long_liab['Debit'].sum()
    data.append(['TOTAL LIABILITAS JANGKA PANJANG', '', Total_Liabilitas_Jangka_Panjang])
    
    Total_Liabilitas = Total_Liabilitas_Lancar + Total_Liabilitas_Jangka_Panjang
    data.append(['TOTAL LIABILITAS', '', Total_Liabilitas])

    # EKUITAS
    data.append(['EKUITAS', '', ''])
    data.append(['Modal Pemilik Akhir', '', Modal_Akhir])
    
    Total_Liabilitas_Ekuitas = Total_Liabilitas + Modal_Akhir
    data.append(['TOTAL LIABILITAS & EKUITAS', '', Total_Liabilitas_Ekuitas])

    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah 1', 'Jumlah 2'])


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


def generate_reports():
    """Menggabungkan logika laporan dan Worksheet."""
    
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
    
    df_ws = df_ws.merge(df_tb_before_adj[['Kode Akun', 'Debit', 'Kredit', 'Tipe_Num']], on='Kode Akun', how='left').fillna(0)
    df_ws.columns = ['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Saldo Normal', 'TB Debit', 'TB Kredit', 'Tipe_Num']
    
    # LOGIKA JURNAL PENYESUAIAN (MJ DEBIT/KREDIT) - ASUMSI 0 UNTUK SAAT INI
    df_ws['MJ Debit'] = 0.0
    df_ws['MJ Kredit'] = 0.0

    # TB AFTER ADJUSTMENT (TB ADJ)
    df_ws['TB ADJ Debit'] = df_ws['TB Debit'] + df_ws['MJ Debit']
    df_ws['TB ADJ Kredit'] = df_ws['TB Kredit'] + df_ws['MJ Kredit']

    # --- 3. SIKLUS PENUTUPAN & LAPORAN KEUANGAN UTAMA (DARI TB ADJ) ---
    df_tb_closing, net_income, df_laba_rugi, df_re, df_laporan_posisi_keuangan = calculate_closing_and_tb_after_closing(df_ws)
    
    # --- Tambahkan laporan pendukung (Jurnal Umum & Kartu Persediaan) ---
    # Jurnal Umum: Panggil fungsi ini jika Anda ingin menampilkannya
    # df_general_journal = generate_general_journal(df_journal_merged) 
    # df_inventory_card = generate_inventory_card(df_movements)

    return {
        "Laba Bersih": net_income,
        "Neraca Saldo Sebelum Penyesuaian": df_tb_before_adj,
        "Worksheet (Kertas Kerja)": df_ws,
        "Laporan Laba Rugi": df_laba_rugi,
        "Laporan Perubahan Modal": df_re,
        "Laporan Posisi Keuangan": df_laporan_posisi_keuangan,
        "Neraca Saldo Setelah Penutup": df_tb_closing[['Kode Akun', 'Nama Akun', 'TB CLOSING Debit', 'TB CLOSING Kredit']],
        # "Jurnal Umum": df_general_journal,
        # "Kartu Persediaan": df_inventory_card,
    }


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


def show_reports_page():
    st.title("📊 Laporan Keuangan & Akuntansi Lengkap")
    
    st.sidebar.header("Filter Tanggal Laporan")
    
    reports = generate_reports()
    net_income = reports.get("Laba Bersih", 0)
    
    st.markdown("---")
    
    # Tampilkan Ringkasan Laba Bersih
    if net_income >= 0:
        st.success(f"**Laba Bersih (Net Income): Rp {net_income:,.0f}**")
    else:
        st.error(f"**Rugi Bersih (Net Loss): Rp {net_income:,.0f}**")
    st.markdown("---")
    
    # Tampilkan Worksheet
    st.header("1. Kertas Kerja (Worksheet)")
    st.info("Worksheet menampilkan Neraca Saldo Setelah Penyesuaian (TB ADJ).")
    st.dataframe(reports["Worksheet (Kertas Kerja)"], use_container_width=True)
    
    # Tampilkan Laporan Keuangan Utama
    st.header("2. Laporan Laba Rugi (Income Statement)")
    st.dataframe(reports["Laporan Laba Rugi"], use_container_width=True)
    
    st.header("3. Laporan Perubahan Modal (Retained Earnings)")
    st.dataframe(reports["Laporan Perubahan Modal"], use_container_width=True)
    
    st.header("4. Laporan Posisi Keuangan (Balance Sheet)")
    st.dataframe(reports["Laporan Posisi Keuangan"], use_container_width=True)
    
    st.header("5. Neraca Saldo Setelah Penutup (TB CLOSING)")
    st.dataframe(reports["Neraca Saldo Setelah Penutup"], use_container_width=True)

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
