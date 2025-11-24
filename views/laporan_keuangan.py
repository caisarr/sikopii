import streamlit as st
import pandas as pd
from supabase_client import supabase
from io import BytesIO
from datetime import date, timedelta
import numpy as np

def format_rupiah(amount):
    """Mengubah angka menjadi string format Rp. X.XXX.XXX. Negatif pakai kurung (Rp ...)."""
    if pd.isna(amount) or amount == '': return ''
    if amount < 0:
        return f"(Rp {-amount:,.0f})".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"Rp {amount:,.0f}".replace(",", "_").replace(".", ",").replace("_", ".")

@st.cache_data(ttl=60)
def fetch_all_accounting_data():
    try:
        inventory_response = supabase.table("inventory_movements").select("*, products(name)").execute()
        journal_lines_response = supabase.table("journal_lines").select("*").execute()
        journal_entries_response = supabase.table("journal_entries").select("id, transaction_date, description, order_id").execute()
        coa_response = supabase.table("chart_of_accounts").select("*").execute()
        
        df_entries = pd.DataFrame(journal_entries_response.data)
        if not df_entries.empty:
            df_entries['transaction_date'] = pd.to_datetime(df_entries['transaction_date'], errors='coerce')
            if df_entries['transaction_date'].dt.tz is not None:
                 df_entries['transaction_date'] = df_entries['transaction_date'].dt.tz_localize(None)
            df_entries['transaction_date'] = df_entries['transaction_date'].dt.normalize()
        else:
            df_entries = pd.DataFrame(columns=['id', 'transaction_date', 'description', 'order_id'])

        df_lines = pd.DataFrame(journal_lines_response.data).fillna(0)
        if df_lines.empty:
             df_lines = pd.DataFrame(columns=['journal_id', 'account_code', 'debit_amount', 'credit_amount'])

        return {
            "journal_lines": df_lines, "journal_entries": df_entries,
            "coa": pd.DataFrame(coa_response.data), "inventory_movements": pd.DataFrame(inventory_response.data),
        }
    except Exception as e:
        st.error(f"Gagal mengambil data dari Supabase: {e}")
        return {
            "journal_lines": pd.DataFrame(), "journal_entries": pd.DataFrame(),
            "coa": pd.DataFrame(columns=['account_code', 'account_name', 'account_type', 'normal_balance']),
            "inventory_movements": pd.DataFrame(),
        }

def get_base_data_and_filter(start_date, end_date):
    data = fetch_all_accounting_data()
    df_lines = data["journal_lines"]; df_entries = data["journal_entries"].copy()
    df_coa = data["coa"]; df_movements = data["inventory_movements"]
    
    if df_entries.empty or df_lines.empty:
        return pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount', 'journal_id', 'description_entry', 'id']), df_coa, df_movements
        
    df_entries['transaction_date'] = df_entries['transaction_date'].astype('datetime64[ns]')
    filter_end = pd.to_datetime(end_date)
    
    df_filtered_entries = df_entries.loc[(df_entries['transaction_date'] <= filter_end)].copy()
    if df_filtered_entries.empty:
        return pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount', 'journal_id', 'description_entry', 'id']), df_coa, df_movements

    if 'description' in df_filtered_entries.columns:
        df_filtered_entries.rename(columns={'description': 'description_entry'}, inplace=True)

    df_journal_merged = df_lines.merge(df_filtered_entries, left_on='journal_id', right_on='id', suffixes=('_line', '_entry'))
    df_journal_merged = df_journal_merged.merge(df_coa, on='account_code')
    
    return df_journal_merged.sort_values(by=['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False]), df_coa, df_movements

def calculate_trial_balance(df_journal, df_coa):
    if df_journal.empty:
        df_tb = df_coa[['account_code', 'account_name', 'account_type']].copy()
        df_tb['Debit'] = 0.0; df_tb['Kredit'] = 0.0
        df_tb['Tipe_Num'] = df_tb['account_code'].astype(str).str[0].apply(lambda x: int(x) if x.isdigit() else 0)
    else:
        df_tb = df_journal.groupby('account_code').agg(Total_Debit=('debit_amount', 'sum'), Total_Kredit=('credit_amount', 'sum')).reset_index()
        df_tb = df_tb.merge(df_coa, on='account_code', how='right').fillna(0)
        df_tb['Tipe_Num'] = df_tb['account_code'].astype(str).str[0].astype(int) 
        df_tb['Saldo Bersih'] = df_tb['Total_Debit'] - df_tb['Total_Kredit']
        df_tb['Debit'] = df_tb.apply(lambda row: row['Saldo Bersih'] if row['normal_balance'] == 'Debit' and row['Saldo Bersih'] >= 0 else -row['Saldo Bersih'] if row['normal_balance'] == 'Credit' and row['Saldo Bersih'] < 0 else 0, axis=1)
        df_tb['Kredit'] = df_tb.apply(lambda row: row['Saldo Bersih'] if row['normal_balance'] == 'Credit' and row['Saldo Bersih'] >= 0 else -row['Saldo Bersih'] if row['normal_balance'] == 'Debit' and row['Saldo Bersih'] < 0 else 0, axis=1)
        
    df_tb = df_tb[['account_code', 'account_name', 'account_type', 'Debit', 'Kredit', 'Tipe_Num']].sort_values(by='account_code')
    df_tb.columns = ['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Debit', 'Kredit', 'Tipe_Num']
    return df_tb

def create_general_journal_report(df_journal):
    if df_journal.empty: return pd.DataFrame(columns=['Tanggal', 'Kode Akun', 'Nama Akun', 'Deskripsi Transaksi', 'Debit', 'Kredit'])
    df_ju = df_journal.sort_values(by=['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False]).copy()
    df_ju['Tanggal'] = df_ju['transaction_date'].dt.strftime('%Y-%m-%d')
    if 'description_entry' not in df_ju.columns: df_ju['description_entry'] = ''
    df_ju = df_ju[['Tanggal', 'description_entry', 'account_code', 'account_name', 'debit_amount', 'credit_amount', 'journal_id']].copy()
    df_ju.columns = ['Tanggal', 'Deskripsi Transaksi', 'Kode Akun', 'Nama Akun', 'Debit', 'Kredit', 'Ref Jurnal']
    df_ju['Nama Akun'] = df_ju.apply(lambda row: f"       {row['Nama Akun']}" if row['Debit'] == 0 else row['Nama Akun'], axis=1)
    is_first_row = df_ju.groupby('Ref Jurnal').cumcount() == 0
    df_ju['Tanggal'] = np.where(is_first_row, df_ju['Tanggal'], '')
    df_ju['Deskripsi Transaksi'] = np.where(is_first_row, df_ju['Deskripsi Transaksi'], '')
    return df_ju[['Tanggal', 'Kode Akun', 'Nama Akun', 'Deskripsi Transaksi', 'Debit', 'Kredit']].reset_index(drop=True)

def create_general_ledger_report(df_journal, df_coa):
    if df_journal.empty: return pd.DataFrame(columns=['Kode Akun', 'Nama Akun', 'Tanggal', 'Keterangan', 'Ref', 'Debit', 'Kredit', 'Saldo Debet', 'Saldo Kredit'])
    df_gl_data = []
    for account_code, group in df_journal.groupby('account_code'):
        coa_rows = df_coa[df_coa['account_code'] == account_code]
        if coa_rows.empty: continue
        coa_info = coa_rows.iloc[0]
        group = group.sort_values(by=['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False]).copy()
        running_balance = 0.0
        for _, row in group.iterrows():
            debit = row['debit_amount']; credit = row['credit_amount']
            running_balance += (debit - credit) if coa_info['normal_balance'] == 'Debit' else (credit - debit)
            df_gl_data.append({'Kode Akun': account_code, 'Nama Akun': coa_info['account_name'], 'Tanggal': row['transaction_date'], 'Keterangan': row.get('description_entry', 'Detail'), 'Ref': row['journal_id'], 'Debit': debit, 'Kredit': credit, 'Saldo': running_balance})
    df_gl = pd.DataFrame(df_gl_data)
    if df_gl.empty: return pd.DataFrame()
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
    AKUN_MODAL = '3-1100'; AKUN_PRIVE = '3-1200'
    Total_Revenue = df_tb_adj[df_tb_adj['Tipe_Num'].isin([4, 8])]['Kredit'].sum()
    Total_Expense = df_tb_adj[df_tb_adj['Tipe_Num'].isin([5, 6, 9])]['Debit'].sum()
    prive_val = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_PRIVE]['Debit'].sum()
    modal_awal = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_MODAL]['Kredit'].sum()
    Net_Income = Total_Revenue - Total_Expense
    Modal_Baru = modal_awal + Net_Income - prive_val
    
    df_ws_final = df_tb_adj.copy()
    df_ws_final['IS Debit'] = 0.0; df_ws_final['IS Kredit'] = 0.0; df_ws_final['BS Debit'] = 0.0; df_ws_final['BS Kredit'] = 0.0
    df_ws_final.loc[df_ws_final['Tipe_Num'].isin([4, 5, 6, 8, 9]), 'IS Debit'] = df_ws_final['Debit']
    df_ws_final.loc[df_ws_final['Tipe_Num'].isin([4, 5, 6, 8, 9]), 'IS Kredit'] = df_ws_final['Kredit']
    df_ws_final.loc[df_ws_final['Tipe_Num'].isin([1, 2, 3]), 'BS Debit'] = df_ws_final['Debit']
    df_ws_final.loc[df_ws_final['Kode Akun'] == AKUN_MODAL, 'BS Kredit'] = Modal_Baru
    df_ws_final.loc[(df_ws_final['Tipe_Num'].isin([1, 2])) | (df_ws_final['Kode Akun'] == AKUN_PRIVE), 'BS Kredit'] = df_ws_final['Kredit']
    
    df_is = create_income_statement_df(df_tb_adj, Total_Revenue, Total_Expense, Net_Income)
    df_re = pd.DataFrame({'Deskripsi': ['Modal Awal', 'Laba Bersih Periode', 'Prive', 'Modal Akhir'], 'Jumlah': [modal_awal, Net_Income, -prive_val, Modal_Baru]})
    df_bs = create_balance_sheet_df(df_tb_adj, Modal_Baru)
    return Net_Income, df_is, df_re, df_bs, df_ws_final

def create_income_statement_df(df_tb_adj, Total_Revenue, Total_Expense, Net_Income):
    data = []
    df_is = df_tb_adj[df_tb_adj['Tipe_Num'].isin([4, 5, 6, 8, 9])].copy()
    def get_sum(tipe, col): return df_is[df_is['Tipe_Num'].isin(tipe)][col].sum()
    def get_rows(tipe, col): return df_is[df_is['Tipe_Num'].isin(tipe)].sort_values(by='Kode Akun')

    data.append(['PENDAPATAN', '', '']); total_rev = get_sum([4], 'Kredit')
    for _, r in get_rows([4], 'Kredit').iterrows(): data.append([r['Nama Akun'], r['Kredit'], ''])
    data.append(['TOTAL PENDAPATAN', '', total_rev])
    
    data.append(['HARGA POKOK PENJUALAN', '', '']); total_hpp = get_sum([5], 'Debit')
    for _, r in get_rows([5], 'Debit').iterrows(): data.append([r['Nama Akun'], r['Debit'], ''])
    data.append(['TOTAL COST OF GOODS SOLD', '', total_hpp])
    data.append(['LABA KOTOR', '', total_rev - total_hpp])
    
    data.append(['BEBAN OPERASIONAL', '', '']); total_ops = get_sum([6], 'Debit')
    for _, r in get_rows([6], 'Debit').iterrows(): data.append([r['Nama Akun'], r['Debit'], ''])
    data.append(['TOTAL BEBAN OPERASIONAL', '', total_ops])
    data.append(['LABA OPERASI', '', (total_rev - total_hpp) - total_ops])
    
    data.append(['PENDAPATAN DAN BEBAN LAIN-LAIN', '', ''])
    for _, r in get_rows([8], 'Kredit').iterrows(): data.append([r['Nama Akun'], r['Kredit'], ''])
    for _, r in get_rows([9], 'Debit').iterrows(): data.append([r['Nama Akun'], -r['Debit'], ''])
    net_lain = get_sum([8], 'Kredit') - get_sum([9], 'Debit')
    data.append(['TOTAL PENDAPATAN & BEBAN LAIN', '', net_lain])
    data.append(['LABA BERSIH', '', Net_Income])
    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah', 'Total'])

def create_balance_sheet_df(df_tb_adj, Modal_Akhir):
    data = []; df_bs = df_tb_adj[df_tb_adj['Tipe_Num'].isin([1, 2])].copy()
    data.append(['ASET', '', '']); data.append(['Aset Lancar', '', ''])
    df_ca = df_bs[df_bs['Kode Akun'].str.startswith('1-1')]
    for _, r in df_ca.iterrows(): data.append([r['Nama Akun'], r['Debit'] - r['Kredit'], ''])
    total_ca = df_ca['Debit'].sum() - df_ca['Kredit'].sum(); data.append(['TOTAL ASET LANCAR', '', total_ca])
    
    data.append(['Aset Tetap', '', ''])
    df_fa = df_bs[df_bs['Kode Akun'].str.startswith('1-2')]
    for _, r in df_fa.iterrows(): data.append([r['Nama Akun'], r['Debit'] - r['Kredit'], ''])
    total_fa = df_fa['Debit'].sum() - df_fa['Kredit'].sum(); data.append(['TOTAL ASET TETAP', '', total_fa])
    data.append(['TOTAL ASET', '', total_ca + total_fa])
    
    data.append(['LIABILITAS & EKUITAS', '', '']); data.append(['Liabilitas', '', ''])
    df_liab = df_bs[df_bs['Kode Akun'].str.startswith('2')]
    for _, r in df_liab.iterrows(): data.append([r['Nama Akun'], r['Kredit'] - r['Debit'], ''])
    total_liab = df_liab['Kredit'].sum() - df_liab['Debit'].sum(); data.append(['TOTAL LIABILITAS', '', total_liab])
    data.append(['Ekuitas', '', '']); data.append(['Modal Pemilik Akhir', '', Modal_Akhir])
    data.append(['TOTAL LIABILITAS & EKUITAS', '', total_liab + Modal_Akhir])
    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah 1', 'Jumlah 2'])

def create_cash_flow_detailed(df_journal):
    if df_journal.empty: return pd.DataFrame(columns=['Deskripsi', 'Jumlah', 'Total'])
    df_cash = df_journal[df_journal['account_code'] == '1-1100'].copy()
    op_in, op_out, inv, fin = [], [], [], []
    for _, r in df_cash.iterrows():
        desc = str(r.get('description_entry', '')).lower()
        amt = r['debit_amount'] if r['debit_amount'] > 0 else -r['credit_amount']
        disp = r.get('description_entry', 'Transaksi')
        if 'prive' in desc or 'utang' in desc or 'pinjaman' in desc or 'angsuran' in desc: fin.append((disp, amt))
        elif 'aset' in desc or 'tanah' in desc or 'bangunan' in desc: inv.append((disp, amt))
        else:
            if amt > 0: op_in.append((disp, amt))
            else: op_out.append((disp, amt))
            
    data = []
    data.append(['ARUS KAS DARI AKTIVITAS OPERASI', '', ''])
    data.append(['Penerimaan Kas:', '', '']); t_op_in = 0
    for d, v in op_in: data.append([f"  - {d}", v, '']); t_op_in += v
    data.append(['Pengeluaran Kas:', '', '']); t_op_out = 0
    for d, v in op_out: data.append([f"  - {d}", v, '']); t_op_out += v
    data.append(['Arus Kas Bersih dari Operasi', '', t_op_in + t_op_out])
    
    data.append(['ARUS KAS DARI AKTIVITAS INVESTASI', '', '']); t_inv = 0
    for d, v in inv: data.append([f"  - {d}", v, '']); t_inv += v
    data.append(['Arus Kas Bersih dari Investasi', '', t_inv])
    
    data.append(['ARUS KAS DARI AKTIVITAS PENDANAAN', '', '']); t_fin = 0
    for d, v in fin: data.append([f"  - {d}", v, '']); t_fin += v
    data.append(['Arus Kas Bersih dari Pendanaan', '', t_fin])
    data.append(['KENAIKAN (PENURUNAN) BERSIH KAS', '', (t_op_in + t_op_out) + t_inv + t_fin])
    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah', 'Total'])

def create_inventory_movement_report(df_movements):
    if df_movements.empty: return pd.DataFrame()
    if 'movement_date' in df_movements.columns: df_movements['movement_date'] = pd.to_datetime(df_movements['movement_date']).dt.strftime('%Y-%m-%d')
    df_movements['product_name'] = df_movements['products'].apply(lambda x: x.get('name') if isinstance(x, dict) else 'Unknown')
    df_movements = df_movements.sort_values(by=['movement_date', 'product_name'], ascending=[True, True])
    
    report_data = []
    for product_id, group in df_movements.groupby('product_id'):
        product_name = group['product_name'].iloc[0]
        running_qty = 0; running_value = 0
        for _, row in group.iterrows():
            qty = row['quantity_change']; cost = row['unit_cost']
            running_qty += qty; running_value += (qty * cost)
            report_data.append({
                "Nama Produk": product_name, "Tanggal": row['movement_date'], "Jenis": row['movement_type'],
                "Ref": row['reference_id'], "Masuk": qty if row['movement_type'] == 'RECEIPT' else 0,
                "Keluar": abs(qty) if row['movement_type'] == 'ISSUE' else 0,
                "Biaya": cost, "Total": abs(qty * cost), "Saldo Qty": running_qty, "Saldo Nilai": running_value
            })
    df_rep = pd.DataFrame(report_data)
    if df_rep.empty: return df_rep
    df_rep.columns = ['Produk', 'Tanggal', 'Jenis', 'Ref', 'Masuk', 'Keluar', 'Biaya Satuan', 'Total', 'Saldo Qty', 'Saldo Nilai']
    is_first = df_rep.groupby('Produk').cumcount() == 0
    df_rep['Produk'] = np.where(is_first, df_rep['Produk'], '')
    return df_rep

def to_excel_bytes(reports):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for name, df in reports.items():
            if isinstance(df, pd.DataFrame): df.to_excel(writer, sheet_name=name[:30], index=False)
    return output.getvalue()

def generate_reports():
    today = date.today()
    if "end_date" not in st.session_state: st.session_state.end_date = today
    if "start_date" not in st.session_state: st.session_state.start_date = date(2025, 10, 31)
    start_date = st.sidebar.date_input("Tanggal Mulai", value=st.session_state.start_date)
    end_date = st.sidebar.date_input("Tanggal Akhir", value=st.session_state.end_date)
    
    df_merged, df_coa, df_moves = get_base_data_and_filter(start_date, end_date)
    df_gj = create_general_journal_report(df_merged)
    df_gl = create_general_ledger_report(df_merged, df_coa)
    df_tb = calculate_trial_balance(df_merged, df_coa)
    
    if not df_merged.empty and 'journal_id' in df_merged.columns:
        df_before = df_merged[df_merged['journal_id'] < 200]
        df_ajp = df_merged[df_merged['journal_id'] >= 200]
    else: df_before = df_merged; df_ajp = pd.DataFrame(columns=df_merged.columns)
    
    df_tb_before = calculate_trial_balance(df_before, df_coa)
    df_adj_only = calculate_trial_balance(df_ajp, df_coa)
    
    df_ws = df_coa[['account_code', 'account_name', 'account_type', 'normal_balance']].copy()
    df_ws.columns = ['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Saldo Normal']
    df_ws = df_ws.merge(df_tb_before[['Kode Akun', 'Debit', 'Kredit', 'Tipe_Num']], on='Kode Akun', how='left', suffixes=('', '_Before')).fillna(0)
    df_ws.rename(columns={'Debit': 'TB Debit', 'Kredit': 'TB Kredit'}, inplace=True)
    df_ws = df_ws.merge(df_adj_only[['Kode Akun', 'Debit', 'Kredit']], on='Kode Akun', how='left', suffixes=('', '_Adj')).fillna(0)
    df_ws.rename(columns={'Debit': 'MJ Debit', 'Kredit': 'MJ Kredit'}, inplace=True)
    
    def calc_adj(row):
        net = (row['TB Debit'] - row['TB Kredit']) + (row['MJ Debit'] - row['MJ Kredit'])
        if row['Saldo Normal'] == 'Debit': return max(0, net), max(0, -net)
        else: return max(0, -net), max(0, net)
    df_ws[['Debit', 'Kredit']] = df_ws.apply(lambda x: calc_adj(x), axis=1, result_type='expand')
    
    df_final_rep_source = df_ws[['Kode Akun', 'Nama Akun', 'Tipe Akun', 'Debit', 'Kredit', 'Tipe_Num']].copy()
    net_inc, df_is, df_re, df_bs, _ = calculate_closing_and_reporting_data(df_final_rep_source)
    
    df_ws_display = df_ws[['Kode Akun', 'Nama Akun', 'TB Debit', 'TB Kredit', 'MJ Debit', 'MJ Kredit', 'Debit', 'Kredit']]
    df_ws_display.rename(columns={'Debit': 'TB ADJ Debit', 'Kredit': 'TB ADJ Kredit'}, inplace=True)
    
    return {
        "Jurnal Umum": df_gj, "Buku Besar": df_gl, "Kertas Kerja": df_ws_display,
        "Laporan Laba Rugi": df_is, "Laporan Perubahan Modal": df_re,
        "Laporan Posisi Keuangan": df_bs, "Laporan Arus Kas": create_cash_flow_detailed(df_merged),
        "Kartu Persediaan": create_inventory_movement_report(df_moves), "Laba Bersih": net_inc
    }

def show_reports_page():
    st.title("📊 Laporan Keuangan")
    st.sidebar.header("Filter")
    if st.sidebar.button("🔄 Refresh Data Real-time"): st.cache_data.clear(); st.rerun()
    reports = generate_reports()
    
    def fmt_df(df):
        d = df.copy()
        for c in d.columns:
            if any(x in c for x in ['Debit', 'Kredit', 'Jumlah', 'Total', 'Saldo Nilai', 'Biaya']):
                d[c] = d[c].apply(lambda x: format_rupiah(x) if isinstance(x, (int, float)) else x)
        return d

    st.header("1. Jurnal Umum"); st.dataframe(fmt_df(reports["Jurnal Umum"]), use_container_width=True, hide_index=True)
    st.header("2. Buku Besar"); st.dataframe(fmt_df(reports["Buku Besar"]), use_container_width=True, hide_index=True)
    st.header("3. Kertas Kerja (Worksheet)"); st.dataframe(fmt_df(reports["Kertas Kerja"]), use_container_width=True, hide_index=True)
    c1, c2 = st.columns(2)
    with c1: st.header("4. Laporan Laba Rugi"); st.dataframe(fmt_df(reports["Laporan Laba Rugi"]), use_container_width=True, hide_index=True)
    with c2: st.header("5. Laporan Perubahan Modal"); st.dataframe(fmt_df(reports["Laporan Perubahan Modal"]), use_container_width=True, hide_index=True)
    st.header("6. Laporan Posisi Keuangan"); st.dataframe(fmt_df(reports["Laporan Posisi Keuangan"]), use_container_width=True, hide_index=True)
    st.header("7. Laporan Arus Kas"); st.dataframe(fmt_df(reports["Laporan Arus Kas"]), use_container_width=True, hide_index=True)
    st.header("8. Kartu Persediaan"); st.dataframe(fmt_df(reports["Kartu Persediaan"]), use_container_width=True, hide_index=True)
    st.download_button("📥 Download Excel", data=to_excel_bytes(reports), file_name="Laporan_Keuangan.xlsx")

if __name__ == "__main__":
    show_reports_page()
