# views/jurnal_umum.py
import streamlit as st
import pandas as pd
from supabase_client import supabase
from datetime import date

# Akun-akun Persediaan yang memerlukan pencatatan unit (sesuai COA kompleks Anda)
INVENTORY_ACCOUNTS = ['1-1200', '1-1400', '1-1500'] 

# 1. Ambil Chart of Accounts (COA) dan Produk Inventori
@st.cache_data
def get_coa_and_products():
    # Ambil COA
    coa_response = supabase.table("chart_of_accounts").select("account_code, account_name").order("account_code").execute()
    coa_list = coa_response.data
    coa_map = {f"{a['account_code']} - {a['account_name']}": a['account_code'] for a in coa_list}
    coa_options = list(coa_map.keys())

    # Ambil Produk Inventori yang dipetakan ke akun Persediaan
    products_response = supabase.table("products").select("id, name, inventory_account_code").in_("inventory_account_code", INVENTORY_ACCOUNTS).execute()
    products_list = products_response.data
    
    # Buat mapping dari nama produk unik ke ID
    product_mapping = {f"{p['name']} (Akun: {p['inventory_account_code']})": p['id'] for p in products_list}
    
    return coa_map, coa_options, products_list, product_mapping

def jurnal_umum_form():
    coa_map, coa_options, products_list, product_mapping = get_coa_and_products()
    product_keys = list(product_mapping.keys())

    # Inisialisasi session state untuk menyimpan baris jurnal
    if "journal_lines_manual" not in st.session_state:
        st.session_state.journal_lines_manual = []

    with st.form("general_journal_form"):
        st.title("Jurnal Umum (Pencatatan Transaksi Manual)")
        st.subheader("Detail Transaksi")
        
        # Header Jurnal
        jurnal_date = st.date_input("Tanggal Transaksi", value=date.today())
        description = st.text_area("Deskripsi Jurnal", placeholder="Contoh: Pembelian 100 unit Bibit 2'' secara tunai")

        st.subheader("Baris Jurnal")
        
        # Tampilkan baris yang sudah ditambahkan dalam DataFrame
        if st.session_state.journal_lines_manual:
            # Hanya tampilkan kolom utama ke pengguna
            display_df = pd.DataFrame(st.session_state.journal_lines_manual)
            st.dataframe(display_df[['Akun', 'Debit', 'Kredit']], use_container_width=True, hide_index=True)
            
            total_debit = display_df['Debit'].sum()
            total_credit = display_df['Kredit'].sum()
            
            st.markdown("---")
            st.markdown(f"**Total Debit:** Rp {total_debit:,.0f} | **Total Kredit:** Rp {total_credit:,.0f}")
            st.markdown("---")
            
            if total_debit != total_credit and total_debit > 0:
                st.error(f"Jurnal Tidak Seimbang! Selisih: Rp {abs(total_debit - total_credit):,.0f}")
        
        # Input Baris Baru
        col1, col2, col3 = st.columns(3)
        with col1:
            selected_account_name = st.selectbox("Akun", coa_options, key="new_line_account")
        with col2:
            debit_input = st.number_input("Debit", min_value=0.0, step=1.0, key="new_line_debit")
        with col3:
            credit_input = st.number_input("Kredit", min_value=0.0, step=1.0, key="new_line_credit")

        selected_account_code = coa_map.get(selected_account_name)
        
        # --- LOGIKA INPUT INVENTORI KHUSUS ---
        is_inventory_purchase = False
        
        # Cek: Apakah Akun Persediaan di-Debit (Pembelian/Masuk)?
        if selected_account_code in INVENTORY_ACCOUNTS and debit_input > 0 and credit_input == 0:
            is_inventory_purchase = True
            
            st.markdown("---")
            st.subheader(f"Detail Pembelian Unit ({selected_account_code})")
            
            # Filter produk yang mapping inventory_account_code-nya sesuai
            relevant_product_keys = [k for k, v in product_mapping.items() 
                                     if v in [p['id'] for p in products_list if p['inventory_account_code'] == selected_account_code]]
            
            col_inv1, col_inv2, col_inv3 = st.columns(3)
            with col_inv1:
                selected_product_name = st.selectbox("Pilih Varian Produk", relevant_product_keys, key="inv_product")
            with col_inv2:
                unit_qty = st.number_input("Jumlah Unit Masuk", min_value=1, step=1, key="inv_qty")
            with col_inv3:
                # Harga Pokok Satuan (Cost)
                unit_cost = st.number_input("Harga Pokok Satuan", min_value=0.01, step=1.0, key="inv_cost")

            # Hitung total biaya yang seharusnya
            calculated_cost = unit_qty * unit_cost
            
            # Peringatan Validasi
            if unit_qty > 0 and unit_cost > 0 and abs(calculated_cost - debit_input) > 0.01:
                st.warning(f"Total Biaya Unit (Rp {calculated_cost:,.0f}) TIDAK cocok dengan Debit Jurnal (Rp {debit_input:,.0f}). Harap sesuaikan salah satunya.")


        # Tombol untuk menambahkan baris
        if st.form_submit_button("Tambahkan Baris"):
            if not selected_account_name or (debit_input == 0 and credit_input == 0):
                st.error("Pilih Akun dan masukkan nilai Debit/Kredit.")
                st.stop()

            if debit_input > 0 and credit_input > 0:
                st.error("Masukkan hanya salah satu: Debit atau Kredit.")
                st.stop()
            
            # Validasi Inventori
            if is_inventory_purchase:
                if unit_qty <= 0 or unit_cost <= 0:
                    st.error("Gagal: Unit dan Harga Pokok Satuan wajib diisi untuk Pembelian Persediaan.")
                    st.stop()
                
                calculated_cost = unit_qty * unit_cost
                if abs(calculated_cost - debit_input) > 0.01:
                    st.error("Gagal: Total Debit harus sama persis dengan Unit * Harga Pokok Satuan.")
                    st.stop()
            
            # Tambahkan ke session state
            new_line = {
                "Kode Akun": selected_account_code,
                "Akun": selected_account_name,
                "Debit": debit_input,
                "Kredit": credit_input,
                
                # Metadata Inventory (akan diabaikan jika is_inventory=False)
                "is_inventory": is_inventory_purchase, 
                "product_id": product_mapping.get(selected_product_name) if is_inventory_purchase else None,
                "quantity": unit_qty if is_inventory_purchase else None,
                "unit_cost": unit_cost if is_inventory_purchase else None,
            }
            st.session_state.journal_lines_manual.append(new_line)
            st.rerun()

        st.divider()

        # Tombol untuk menyimpan seluruh jurnal
        if st.form_submit_button("Simpan Jurnal"):
            df_final = pd.DataFrame(st.session_state.journal_lines_manual)
            
            # Validasi Final Jurnal
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
                }).execute().data[0]
                journal_id = journal_header["id"]

                # 2. Siapkan dan Masukkan Baris Jurnal dan Inventory Movements
                lines_to_insert = []
                movements_to_insert = []
                
                for index, row in df_final.iterrows():
                    # Tambahkan ke journal_lines
                    lines_to_insert.append({
                        "journal_id": journal_id,
                        "account_code": row["Kode Akun"],
                        "debit_amount": row["Debit"],
                        "credit_amount": row["Kredit"],
                    })

                    # Cek apakah ini adalah baris pembelian persediaan (is_inventory=True)
                    if row["is_inventory"]:
                        movements_to_insert.append({
                            "product_id": int(row["product_id"]), # Pastikan tipe data benar
                            "movement_date": str(jurnal_date),
                            "movement_type": "RECEIPT", 
                            "quantity_change": int(row["quantity"]), # Unit Masuk
                            "unit_cost": float(row["unit_cost"]),
                            "reference_id": f"JURNAL-{journal_id}",
                        })


                # 3. Masukkan Baris Jurnal
                supabase.table("journal_lines").insert(lines_to_insert).execute()
                
                # 4. Masukkan Inventory Movements (Jika ada)
                if movements_to_insert:
                    supabase.table("inventory_movements").insert(movements_to_insert).execute()


                st.success(f"Jurnal Umum (ID: {journal_id}) dan Pergerakan Persediaan berhasil dicatat!")
                
                # Kosongkan state setelah berhasil
                st.session_state.journal_lines_manual = [] 
                st.rerun()

            except Exception as e:
                st.error(f"Pencatatan Jurnal Gagal: {e}")


if __name__ == "__main__":
    jurnal_umum_form()
