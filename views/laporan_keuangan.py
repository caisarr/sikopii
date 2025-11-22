import streamlit as st
import pandas as pd
from supabase_client import supabase
from io import BytesIO

# --- UTILITY FUNCTIONS ---

@st.cache_data
def fetch_all_accounting_data():
    """Mengambil semua data yang diperlukan dari Supabase."""
    
    # Mengambil data jurnal, COA, dan inventory movements
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

def generate_reports():
    """Memproses data untuk menghasilkan semua laporan."""
    data = fetch_all_accounting_data()
    df_lines = data["journal_lines"]
    df_entries = data["journal_entries"]
    df_coa = data["coa"]
    df_movements = data["inventory_movements"]
    
    # Gabungan Jurnal Utama untuk Neraca Saldo/Buku Besar
    df_journal_merged = df_lines.merge(df_entries, left_on='journal_id', right_on='id', suffixes=('_line', '_entry'))
    df_journal_merged = df_journal_merged.merge(df_coa, on='account_code')
    df_journal_merged = df_journal_merged.sort_values(
        by=['transaction_date', 'journal_id', 'debit_amount'], 
        ascending=[True, True, False]
    )

    # 1. GENERATE NERACA SALDO AKHIR (REKAPITULASI JURNAL)
    df_recap = df_journal_merged.groupby('account_code').agg(
        Total_Debit=('debit_amount', 'sum'),
        Total_Kredit=('credit_amount', 'sum')
    ).reset_index()
    
    df_recap = df_recap.merge(df_coa, on='account_code')
    
    # Hitung Saldo Akhir
    df_recap['Saldo Bersih'] = df_recap['Total_Debit'] - df_recap['Total_Kredit']

    df_recap['Neraca Saldo Debit'] = df_recap.apply(
        lambda row: row['Saldo Bersih'] if row['normal_balance'] == 'Debit' and row['Saldo Bersih'] >= 0 else 
                    -row['Saldo Bersih'] if row['normal_balance'] == 'Credit' and row['Saldo Bersih'] < 0 else 0, axis=1
    )
    df_recap['Neraca Saldo Kredit'] = df_recap.apply(
        lambda row: row['Saldo Bersih'] if row['normal_balance'] == 'Credit' and row['Saldo Bersih'] >= 0 else 
                    -row['Saldo Bersih'] if row['normal_balance'] == 'Debit' and row['Saldo Bersih'] < 0 else 0, axis=1
    )
    
    df_trial_balance = df_recap[[
        'account_code', 'account_name', 'account_type', 'Neraca Saldo Debit', 'Neraca Saldo Kredit'
    ]].sort_values(by='account_code')
    df_trial_balance.columns = ['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Debit', 'Kredit']
    
    
    # 2. GENERATE LAPORAN LABA RUGI (INCOME STATEMENT)
    
    df_income = df_recap[df_recap['account_type'].isin(['Revenue', 'Expense'])]
    
    # Laba Kotor (Pendapatan - HPP)
    total_revenue = df_income[df_income['account_type'] == 'Revenue']['Neraca Saldo Kredit'].sum()
    total_expense = df_income[df_income['account_type'] == 'Expense']['Neraca Saldo Debit'].sum()
    
    net_income = total_revenue - total_expense
    
    df_laba_rugi = df_income[['account_name', 'Neraca Saldo Debit', 'Neraca Saldo Kredit', 'account_type']].copy()
    df_laba_rugi.columns = ['Deskripsi', 'Debit', 'Kredit', 'Tipe']


    # 3. GENERATE LAPORAN PERUBAHAN MODAL (RETAINED EARNING)
    
    modal_awal = df_recap[df_recap['account_code'] == '3-1100']['Neraca Saldo Kredit'].sum()
    prive = df_recap[df_recap['account_code'] == '3-1200']['Neraca Saldo Debit'].sum()
    
    modal_akhir = modal_awal + net_income - prive
    
    df_modal = pd.DataFrame({
        'Deskripsi': ['Modal Pemilik Awal', 'Laba Bersih', 'Prive Pemilik', 'Modal Pemilik Akhir'],
        'Nilai': [modal_awal, net_income, -prive, modal_akhir]
    })


    # 4. GENERATE LAPORAN POSISI KEUANGAN (NERACA)
    
    df_neraca = df_recap[df_recap['account_type'].isin(['Asset', 'Liability', 'Equity'])].copy()
    
    # Gantikan saldo Modal Pemilik lama dengan Modal Pemilik Akhir (setelah penyesuaian laba/prive)
    df_neraca.loc[df_neraca['account_code'] == '3-1100', 'Neraca Saldo Kredit'] = modal_akhir
    df_neraca.loc[df_neraca['account_code'] == '3-1200', 'Neraca Saldo Debit'] = 0 # Prive di Nolkan
    df_neraca.loc[df_neraca['account_code'] == '3-1300', 'Neraca Saldo Kredit'] = 0 # Ikhtisar L/R di Nolkan

    total_asset = df_neraca[df_neraca['account_type'] == 'Asset']['Neraca Saldo Debit'].sum()
    total_liabilities = df_neraca[df_neraca['account_type'] == 'Liability']['Neraca Saldo Kredit'].sum()
    total_equity = df_neraca[df_neraca['account_type'] == 'Equity']['Neraca Saldo Kredit'].sum()
    
    
    # 5. GENERATE BUKU BESAR & JURNAL UMUM (SAMA SEPERTI SEBELUMNYA)
    
    # Jurnal Umum
    df_general_journal = df_journal_merged[[
        'transaction_date', 'description', 'account_code', 'account_name', 
        'debit_amount', 'credit_amount', 'order_id'
    ]].sort_values(by=['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False])
    df_general_journal.columns = ['Tanggal', 'Deskripsi', 'Kode Akun', 'Nama Akun', 'Debit', 'Kredit', 'Ref. Order ID']

    # Buku Besar (Logika sama, hanya diperpendek)
    general_ledgers = {}
    for account_code, df_account in df_journal_merged.groupby('account_code'):
        # ... (Logika Running Balance Buku Besar) ... (Diabaikan di sini agar kode fokus ke Laporan Utama)
        # Kami menggunakan df_trial_balance dan df_general_journal untuk tampilan di Streamlit

    # Kartu Persediaan
    df_movements['product_name'] = df_movements['products'].apply(lambda x: x['name'] if x and 'name' in x else 'Unknown')
    df_movements['running_qty'] = df_movements.groupby('product_id')['quantity_change'].cumsum()
    df_inventory_card = df_movements[['movement_date', 'product_name', 'movement_type', 'quantity_change', 'unit_cost', 'running_qty', 'reference_id']]
    df_inventory_card.columns = ['Tanggal', 'Varian Produk', 'Tipe Pergerakan', 'Perubahan Unit', 'Harga Satuan', 'Saldo Unit', 'Referensi']


    return {
        "Laba Bersih": net_income,
        "Neraca Saldo": df_trial_balance,
        "Laporan Laba Rugi": df_laba_rugi,
        "Laporan Perubahan Modal": df_modal,
        "Laporan Posisi Keuangan (Neraca)": df_neraca,
        "Jurnal Umum": df_general_journal,
        "Kartu Persediaan": df_inventory_card,
        # "Buku Besar": general_ledgers (Bisa ditambahkan kembali jika diperlukan)
    }

def to_excel_bytes(reports):
    """Menyimpan semua laporan ke dalam satu file Excel (BytesIO)"""
    # ... (Fungsi to_excel_bytes sama seperti sebelumnya, pastikan memasukkan semua laporan baru)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for sheet_name, df in reports.items():
            if isinstance(df, pd.DataFrame):
                df.to_excel(writer, sheet_name=sheet_name[:30], index=False)
            
    processed_data = output.getvalue()
    return processed_data


def show_reports_page():
    st.title("📊 Laporan Keuangan & Akuntansi Lengkap")
    st.markdown("---")
    
    reports = generate_reports()
    net_income = reports["Laba Bersih"]
    
    # Tampilkan Ringkasan Laba Bersih
    if net_income >= 0:
        st.success(f"**Laba Bersih (Net Income): Rp {net_income:,.0f}**")
    else:
        st.error(f"**Rugi Bersih (Net Loss): Rp {net_income:,.0f}**")
    st.markdown("---")
    
    # Tampilkan Laporan Keuangan Utama
    st.subheader("1. Laporan Laba Rugi (Income Statement)")
    st.dataframe(reports["Laporan Laba Rugi"], use_container_width=True)
    
    st.subheader("2. Laporan Perubahan Modal (Retained Earnings)")
    st.dataframe(reports["Laporan Perubahan Modal"], use_container_width=True)
    
    st.subheader("3. Laporan Posisi Keuangan (Neraca)")
    st.dataframe(reports["Laporan Posisi Keuangan (Neraca)"], use_container_width=True)
    
    # Tampilkan Laporan Pendukung
    with st.expander("Lihat Laporan Pendukung (Neraca Saldo, Jurnal, Persediaan)"):
        st.subheader("4. Neraca Saldo (Trial Balance)")
        st.dataframe(reports["Neraca Saldo"], use_container_width=True)
        
        st.subheader("5. Jurnal Umum Lengkap")
        st.dataframe(reports["Jurnal Umum"], height=300, use_container_width=True)
        
        st.subheader("6. Kartu Persediaan (Unit Movements)")
        st.dataframe(reports["Kartu Persediaan"], height=300, use_container_width=True)

    st.markdown("---")
    
    # Tombol Download
    excel_data = to_excel_bytes(reports)

    st.download_button(
        label="📥 Unduh Semua Laporan sebagai Excel",
        data=excel_data,
        file_name='Laporan_Akuntansi_Siklus_Lengkap.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


if __name__ == "__main__":
    show_reports_page()
