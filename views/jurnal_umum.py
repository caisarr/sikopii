# views/jurnal_umum.py
import streamlit as st
import pandas as pd
from supabase_client import supabase
from datetime import date

# 1. Ambil Chart of Accounts (COA) untuk dropdown
# Data COA harus diambil dari database untuk memastikan akun kompleks Anda tersedia
@st.cache_data
def get_coa():
    # Mengambil Kode dan Nama Akun dari tabel chart_of_accounts
    response = supabase.table("chart_of_accounts").select("account_code, account_name").order("account_code").execute()
    return response.data

def jurnal_umum_form():
    st.title("Jurnal Umum (Pencatatan Transaksi Manual)")
    
    coa_list = get_coa()
    # Buat mapping untuk memudahkan konversi nama akun ke kode akun saat penyimpanan
    coa_map = {f"{a['account_code']} - {a['account_name']}": a['account_code'] for a in coa_list}
    coa_options = list(coa_map.keys())

    # Inisialisasi session state untuk menyimpan baris jurnal yang sedang diinput
    if "journal_lines_manual" not in st.session_state:
        st.session_state.journal_lines_manual = []

    with st.form("general_journal_form"):
        st.subheader("Detail Transaksi")
        
        # Header Jurnal
        jurnal_date = st.date_input("Tanggal Transaksi", value=date.today())
        description = st.text_area("Deskripsi Jurnal", placeholder="Contoh: Pembayaran Beban Gaji Karyawan Bulan November")

        st.subheader("Baris Jurnal (Minimal 2 Baris)")
        
        # Tampilkan baris yang sudah ditambahkan dalam DataFrame
        if st.session_state.journal_lines_manual:
            df = pd.DataFrame(st.session_state.journal_lines_manual)
            st.dataframe(df, use_container_width=True, hide_index=True)
            
            total_debit = df['Debit'].sum()
            total_credit = df['Kredit'].sum()
            
            st.markdown("---")
            st.markdown(f"**Total Debit:** Rp {total_debit:,.0f} | **Total Kredit:** Rp {total_credit:,.0f}")
            st.markdown("---")
            
            if total_debit != total_credit and total_debit > 0:
                st.error(f"Jurnal Tidak Seimbang! Selisih: Rp {abs(total_debit - total_credit):,.0f}. Jurnal harus seimbang (Debit = Kredit) sebelum disimpan.")
        
        # Input Baris Baru
        col1, col2, col3 = st.columns(3)
        with col1:
            selected_account_name = st.selectbox("Akun", coa_options, key="new_line_account")
        with col2:
            # Pastikan input berupa float untuk memudahkan perhitungan
            debit_input = st.number_input("Debit", min_value=0.0, step=1.0, key="new_line_debit")
        with col3:
            credit_input = st.number_input("Kredit", min_value=0.0, step=1.0, key="new_line_credit")

        # Tombol untuk menambahkan baris
        if st.form_submit_button("Tambahkan Baris"):
            if selected_account_name and (debit_input > 0 or credit_input > 0):
                if debit_input > 0 and credit_input > 0:
                    st.error("Masukkan hanya salah satu: Debit atau Kredit.")
                else:
                    st.session_state.journal_lines_manual.append({
                        "Kode Akun": coa_map[selected_account_name],
                        "Akun": selected_account_name,
                        "Debit": debit_input,
                        "Kredit": credit_input,
                    })
                    st.rerun()

        st.divider()

        # Tombol untuk menyimpan seluruh jurnal
        if st.form_submit_button("Simpan Jurnal"):
            df_final = pd.DataFrame(st.session_state.journal_lines_manual)
            
            # Validasi Akhir
            if df_final['Debit'].sum() != df_final['Kredit'].sum() or df_final['Debit'].sum() == 0:
                 st.error("Gagal: Jurnal harus seimbang (Debit = Kredit) dan tidak boleh kosong.")
                 st.stop()
            
            if not description:
                st.error("Deskripsi jurnal wajib diisi.")
                st.stop()
            
            # --- LOGIKA PENYIMPANAN KE SUPABASE ---
            try:
                # 1. Buat Header Jurnal
                journal_header = supabase.table("journal_entries").insert({
                    "transaction_date": str(jurnal_date),
                    "description": description,
                    # order_id akan NULL (karena ini jurnal manual), yang sekarang diizinkan oleh schema
                }).execute().data[0]
                journal_id = journal_header["id"]

                # 2. Siapkan dan Masukkan Baris Jurnal
                lines_to_insert = []
                for index, row in df_final.iterrows():
                    lines_to_insert.append({
                        "journal_id": journal_id,
                        "account_code": row["Kode Akun"],
                        "debit_amount": row["Debit"],
                        "credit_amount": row["Kredit"],
                    })

                supabase.table("journal_lines").insert(lines_to_insert).execute()
                
                st.success(f"Jurnal Umum (ID: {journal_id}) berhasil dicatat!")
                
                # Kosongkan state setelah berhasil
                st.session_state.journal_lines_manual = [] 
                st.rerun()

            except Exception as e:
                st.error(f"Pencatatan Jurnal Gagal: {e}")


if __name__ == "__main__":
    jurnal_umum_form()
