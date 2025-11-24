import streamlit as st
import pandas as pd
from supabase_client import supabase
from io import BytesIO
from datetime import date, timedelta
import numpy as np

# --- FORMATTING UTILITY ---
def format_rupiah(amount):
    """Format angka ke Rupiah. Negatif menggunakan kurung (Rp xxx)."""
    if pd.isna(amount) or amount == '': return ''
    if amount < 0:
        return f"(Rp {-amount:,.0f})".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"Rp {amount:,.0f}".replace(",", "_").replace(".", ",").replace("_", ".")

# --- DATA FETCHING ---
@st.cache_data(ttl=60)
def fetch_all_accounting_data():
    try:
        inv = supabase.table("inventory_movements").select("*, products(name)").execute()
        lines = supabase.table("journal_lines").select("*").execute()
        entries = supabase.table("journal_entries").select("id, transaction_date, description, order_id").execute()
        coa = supabase.table("chart_of_accounts").select("*").execute()
        
        df_ent = pd.DataFrame(entries.data)
        if not df_ent.empty:
            df_ent['transaction_date'] = pd.to_datetime(df_ent['transaction_date'], errors='coerce')
            df_ent['transaction_date'] = df_ent['transaction_date'].dt.normalize()
        else:
            df_ent = pd.DataFrame(columns=['id', 'transaction_date', 'description', 'order_id'])

        df_lines = pd.DataFrame(lines.data).fillna(0)
        if df_lines.empty:
             df_lines = pd.DataFrame(columns=['journal_id', 'account_code', 'debit_amount', 'credit_amount'])

        return {"lines": df_lines, "entries": df_ent, "coa": pd.DataFrame(coa.data), "mov": pd.DataFrame(inv.data)}
    except Exception as e:
        st.error(f"Error: {e}"); return {}

# --- CORE LOGIC ---
def get_data(start, end):
    d = fetch_all_accounting_data()
    if not d: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    
    ent = d["entries"].copy(); lines = d["lines"]
    if ent.empty or lines.empty:
        return pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount', 'journal_id', 'description_entry', 'id']), d["coa"], d["mov"]
    
    ent['transaction_date'] = ent['transaction_date'].astype('datetime64[ns]')
    filt = ent.loc[ent['transaction_date'] <= pd.to_datetime(end)].copy()
    
    if 'description' in filt.columns: filt.rename(columns={'description': 'description_entry'}, inplace=True)
    merged = lines.merge(filt, left_on='journal_id', right_on='id', suffixes=('_line', '_entry'))
    merged = merged.merge(d["coa"], on='account_code')
    return merged.sort_values(['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False]), d["coa"], d["mov"]

def calc_tb(df, coa):
    """Menghitung Neraca Saldo (TB)"""
    if df.empty:
        tb = coa[['account_code', 'account_name', 'account_type']].copy()
        tb['Debit'] = 0.0; tb['Kredit'] = 0.0
        tb['Tipe_Num'] = tb['account_code'].str[0].apply(lambda x: int(x) if x.isdigit() else 0)
    else:
        tb = df.groupby('account_code').agg(D=('debit_amount', 'sum'), C=('credit_amount', 'sum')).reset_index()
        tb = tb.merge(coa, on='account_code', how='right').fillna(0)
        tb['Tipe_Num'] = tb['account_code'].str[0].astype(int)
        
        # Hitung Net Saldo
        tb['Net'] = tb['D'] - tb['C']
        
        # Assign ke kolom Debit/Kredit murni berdasarkan positif/negatif (tanpa mempedulikan normal balance untuk posisi kolom)
        # Ini agar Akum Penyusutan (Kredit) masuk kolom Kredit, bukan Debit minus.
        tb['Debit'] = tb['Net'].apply(lambda x: x if x > 0 else 0)
        tb['Kredit'] = tb['Net'].apply(lambda x: abs(x) if x < 0 else 0)
    
    return tb[['account_code', 'account_name', 'account_type', 'Debit', 'Kredit', 'Tipe_Num']].rename(columns={'account_code':'Kode Akun', 'account_name':'Nama Akun', 'account_type':'Tipe'}).sort_values('Kode Akun')

def calculate_ws_columns(row):
    """Helper untuk menghitung kolom Adjusted TB"""
    net_tb = row['TB D'] - row['TB K']
    net_mj = row['MJ D'] - row['MJ K']
    net_final = net_tb + net_mj
    return (net_final, 0) if net_final >= 0 else (0, abs(net_final))

def generate_reports():
    # SETTING TANGGAL DEFAULT
    if "end_date" not in st.session_state: st.session_state.end_date = date(2025, 12, 31)
    if "start_date" not in st.session_state: st.session_state.start_date = date(2025, 10, 31)
    
    s = st.sidebar.date_input("Mulai", st.session_state.start_date)
    e = st.sidebar.date_input("Akhir", st.session_state.end_date)
    
    df, coa, mov = get_data(s, e)
    
    # PEMISAHAN DATA (BEFORE AJP vs AJP)
    # Asumsi: Transaksi AJP memiliki ID >= 200
    if not df.empty and 'journal_id' in df:
        pre = df[df['journal_id'] < 200]
        ajp = df[df['journal_id'] >= 200]
    else: 
        pre = df; ajp = df[0:0]
    
    # HITUNG TB
    tb_pre = calc_tb(pre, coa)
    tb_ajp = calc_tb(ajp, coa)
    
    # SUSUN WORKSHEET (WS)
    ws = coa[['account_code', 'account_name', 'account_type', 'normal_balance']].copy()
    ws.columns = ['Kode Akun', 'Nama Akun', 'Tipe', 'Normal']
    
    # Merge TB Awal
    ws = ws.merge(tb_pre[['Kode Akun', 'Debit', 'Kredit']], on='Kode Akun', how='left').fillna(0).rename(columns={'Debit':'TB D', 'Kredit':'TB K'})
    # Merge AJP (MJ)
    ws = ws.merge(tb_ajp[['Kode Akun', 'Debit', 'Kredit']], on='Kode Akun', how='left').fillna(0).rename(columns={'Debit':'MJ D', 'Kredit':'MJ K'})
    
    # Hitung TB Setelah Penyesuaian (TB ADJ)
    ws[['Adj D', 'Adj K']] = ws.apply(lambda x: pd.Series(calculate_ws_columns(x)), axis=1)
    
    # Dataframe bersih untuk kalkulasi laporan lanjutan
    df_calc = ws[['Kode Akun', 'Nama Akun', 'Tipe', 'Adj D', 'Adj K']].rename(columns={'Adj D':'Debit', 'Adj K':'Kredit'})
    df_calc['Tipe_Num'] = df_calc['Kode Akun'].str[0].astype(int)
    
    # Kalkulasi Laporan Keuangan
    inc, is_df, re_df, bs_df, ws_fin = process_financial_statements(df_calc, ws)
    
    # Merge hasil kolom IS/BS kembali ke Worksheet utama untuk display
    ws_disp = ws.merge(ws_fin[['Kode Akun', 'IS D', 'IS K', 'BS D', 'BS K']], on='Kode Akun', how='left')
    ws_disp.rename(columns={'Adj D': 'TB ADJ D', 'Adj K': 'TB ADJ K'}, inplace=True)
    
    return {
        "JU": create_journal_report(df),
        "BB": create_ledger_report(df, coa),
        "WS": ws_disp.drop(columns=['Tipe', 'Normal']),
        "IS": is_df,
        "RE": re_df,
        "BS": bs_df,
        "CF": create_cashflow(df),
        "Kartu": create_inventory_report(mov),
        "Laba Bersih": inc
    }

def process_financial_statements(df_tb_adj, ws_raw):
    AKUN_MODAL = '3-1100'; AKUN_PRIVE = '3-1200'
    
    # Hitung Laba Bersih
    rev = df_tb_adj[df_tb_adj['Tipe_Num'].isin([4, 8])]['Kredit'].sum()
    exp = df_tb_adj[df_tb_adj['Tipe_Num'].isin([5, 6, 9])]['Debit'].sum()
    net_income = rev - exp
    
    # Modal Akhir
    prive = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_PRIVE]['Debit'].sum()
    # Modal Awal diambil dari Kredit TB Adj (sebelum ditambah Laba Bersih)
    modal_awal = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_MODAL]['Kredit'].sum()
    modal_akhir = modal_awal + net_income - prive
    
    # Isi Kolom Worksheet (IS/BS)
    ws_fin = ws_raw.copy()
    ws_fin['IS D'] = 0.0; ws_fin['IS K'] = 0.0; ws_fin['BS D'] = 0.0; ws_fin['BS K'] = 0.0
    
    # Mapping ke Kolom IS
    is_mask = df_tb_adj['Tipe_Num'].isin([4, 5, 6, 8, 9])
    ws_fin.loc[is_mask, 'IS D'] = df_tb_adj.loc[is_mask, 'Debit']
    ws_fin.loc[is_mask, 'IS K'] = df_tb_adj.loc[is_mask, 'Kredit']
    
    # Mapping ke Kolom BS
    bs_mask = df_tb_adj['Tipe_Num'].isin([1, 2, 3])
    ws_fin.loc[bs_mask, 'BS D'] = df_tb_adj.loc[bs_mask, 'Debit']
    ws_fin.loc[bs_mask, 'BS K'] = df_tb_adj.loc[bs_mask, 'Kredit']
    
    # Generate Laporan Spesifik
    df_is = generate_income_statement(df_tb_adj, rev, exp, net_income)
    df_re = pd.DataFrame({
        'Deskripsi': ['Modal Awal', 'Laba Bersih Periode', 'Prive', 'Modal Akhir'],
        'Jumlah': [modal_awal, net_income, -prive, modal_akhir]
    })
    df_bs = generate_balance_sheet(df_tb_adj, modal_akhir)
    
    return net_income, df_is, df_re, df_bs, ws_fin

def generate_income_statement(df, rev, exp, net_inc):
    data = []
    def get_rows(prefix): return df[df['Kode Akun'].str.startswith(prefix)].sort_values('Kode Akun')
    def get_sum(prefix, col): return df[df['Kode Akun'].str.startswith(prefix)][col].sum()

    data.append(['PENDAPATAN', '', ''])
    for _, r in get_rows('4'): data.append([r['Nama Akun'], r['Kredit'], ''])
    data.append(['Total Pendapatan', '', get_sum('4', 'Kredit')])
    
    data.append(['HARGA POKOK PENJUALAN', '', ''])
    for _, r in get_rows('5'): data.append([r['Nama Akun'], r['Debit'], ''])
    hpp = get_sum('5', 'Debit')
    data.append(['Total HPP', '', hpp])
    data.append(['LABA KOTOR', '', get_sum('4', 'Kredit') - hpp])
    
    data.append(['BEBAN OPERASIONAL', '', ''])
    for _, r in get_rows('6'): data.append([r['Nama Akun'], r['Debit'], ''])
    ops = get_sum('6', 'Debit')
    data.append(['Total Beban Ops', '', ops])
    data.append(['LABA OPERASI', '', (get_sum('4', 'Kredit') - hpp) - ops])
    
    data.append(['PENDAPATAN & BEBAN LAIN-LAIN', '', ''])
    data.append(['Pendapatan Lain:', '', ''])
    for _, r in get_rows('8'): data.append([f"  {r['Nama Akun']}", r['Kredit'], ''])
    data.append(['Beban Lain:', '', ''])
    for _, r in get_rows('9'): data.append([f"  {r['Nama Akun']}", -r['Debit'], ''])
    
    net_lain = get_sum('8', 'Kredit') - get_sum('9', 'Debit')
    data.append(['Total Lain-lain', '', net_lain])
    data.append(['LABA BERSIH', '', net_inc])
    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah', 'Total'])

def generate_balance_sheet(df, modal_akhir):
    data = []
    def get_rows(prefix): return df[df['Kode Akun'].str.startswith(prefix)].sort_values('Kode Akun')
    
    data.append(['ASET', '', '']); data.append(['Aset Lancar', '', ''])
    ca_df = get_rows('1-1'); ca_tot = ca_df['Debit'].sum() - ca_df['Kredit'].sum()
    for _, r in ca_df.iterrows(): data.append([r['Nama Akun'], r['Debit']-r['Kredit'], ''])
    data.append(['Total Aset Lancar', '', ca_tot])
    
    data.append(['Aset Tetap', '', ''])
    fa_df = get_rows('1-2'); fa_tot = fa_df['Debit'].sum() - fa_df['Kredit'].sum()
    for _, r in fa_df.iterrows(): data.append([r['Nama Akun'], r['Debit']-r['Kredit'], ''])
    data.append(['Total Aset Tetap', '', fa_tot])
    data.append(['TOTAL ASET', '', ca_tot + fa_tot])
    
    data.append(['LIABILITAS', '', '']); data.append(['Liabilitas Lancar', '', ''])
    cl_df = get_rows('2-1'); cl_tot = cl_df['Kredit'].sum() - cl_df['Debit'].sum()
    for _, r in cl_df.iterrows(): data.append([r['Nama Akun'], r['Kredit']-r['Debit'], ''])
    data.append(['Total Liabilitas Lancar', '', cl_tot])
    
    data.append(['Liabilitas Jangka Panjang', '', ''])
    ll_df = get_rows('2-2'); ll_tot = ll_df['Kredit'].sum() - ll_df['Debit'].sum()
    for _, r in ll_df.iterrows(): data.append([r['Nama Akun'], r['Kredit']-r['Debit'], ''])
    data.append(['Total Liabilitas Jk. Panjang', '', ll_tot])
    data.append(['TOTAL LIABILITAS', '', cl_tot + ll_tot])
    
    data.append(['EKUITAS', '', ''])
    data.append(['Modal Pemilik Akhir', '', modal_akhir])
    data.append(['TOTAL LIABILITAS & EKUITAS', '', (cl_tot + ll_tot) + modal_akhir])
    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah 1', 'Jumlah 2'])

# --- REPORT GENERATORS ---
def create_journal_report(df):
    if df.empty: return pd.DataFrame(columns=['Tanggal', 'Deskripsi', 'Kode Akun', 'Nama Akun', 'Debit', 'Kredit'])
    df = df.sort_values(['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False]).copy()
    df['Tanggal'] = df['transaction_date'].dt.strftime('%Y-%m-%d')
    if 'description_entry' not in df: df['description_entry'] = ''
    df['Nama Akun'] = df.apply(lambda r: f"       {r['account_name']}" if r['debit_amount']==0 else r['account_name'], axis=1)
    
    is_first = df.groupby('journal_id').cumcount() == 0
    df['Tanggal'] = np.where(is_first, df['Tanggal'], '')
    df['description_entry'] = np.where(is_first, df['description_entry'], '')
    return df[['Tanggal', 'account_code', 'Nama Akun', 'description_entry', 'debit_amount', 'credit_amount']].rename(columns={'account_code':'Kode', 'description_entry':'Deskripsi', 'debit_amount':'Debit', 'credit_amount':'Kredit'})

def create_ledger_report(df, coa):
    if df.empty: return pd.DataFrame()
    data = []
    for ac, grp in df.groupby('account_code'):
        if coa[coa['account_code']==ac].empty: continue
        info = coa[coa['account_code']==ac].iloc[0]; bal = 0
        grp = grp.sort_values(['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False])
        for _, r in grp.iterrows():
            bal += (r['debit_amount'] - r['credit_amount']) if info['normal_balance']=='Debit' else (r['credit_amount'] - r['debit_amount'])
            data.append({'Kode': ac, 'Nama': info['account_name'], 'Tgl': r['transaction_date'], 'Ket': r.get('description_entry',''), 'Debit': r['debit_amount'], 'Kredit': r['credit_amount'], 'Saldo': bal})
    res = pd.DataFrame(data)
    if res.empty: return res
    res['Tgl'] = res['Tgl'].dt.strftime('%Y-%m-%d')
    res = res.merge(coa[['account_code', 'normal_balance']], left_on='Kode', right_on='account_code', how='left')
    res['Saldo D'] = res.apply(lambda r: r['Saldo'] if r['normal_balance']=='Debit' and r['Saldo']>=0 else (-r['Saldo'] if r['normal_balance']=='Credit' and r['Saldo']<0 else 0), axis=1)
    res['Saldo K'] = res.apply(lambda r: r['Saldo'] if r['normal_balance']=='Credit' and r['Saldo']>=0 else (-r['Saldo'] if r['normal_balance']=='Debit' and r['Saldo']<0 else 0), axis=1)
    fin = res[['Kode', 'Nama', 'Tgl', 'Ket', 'Debit', 'Kredit', 'Saldo D', 'Saldo K']].sort_values(['Kode', 'Tgl'])
    is_fst = fin.groupby('Kode').cumcount() == 0
    fin['Kode'] = np.where(is_fst, fin['Kode'], ''); fin['Nama'] = np.where(is_fst, fin['Nama'], '')
    return fin

def create_cashflow(df):
    if df.empty: return pd.DataFrame(columns=['Deskripsi', 'Jumlah', 'Total'])
    cash = df[df['account_code'] == '1-1100'].copy()
    op_in, op_out, inv, fin = [], [], [], []
    for _, r in cash.iterrows():
        d_low = str(r.get('description_entry','')).lower(); amt = r['debit_amount'] if r['debit_amount']>0 else -r['credit_amount']
        disp = r.get('description_entry', '-')
        if 'prive' in d_low or 'utang' in d_low or 'pinjaman' in d_low or 'angsuran' in d_low: fin.append((disp, amt))
        elif 'aset' in d_low or 'tanah' in d_low or 'bangunan' in d_low: inv.append((disp, amt))
        else: 
            if amt > 0: op_in.append((disp, amt))
            else: op_out.append((disp, amt))
            
    data = [['ARUS KAS OPERASI', '', ''], ['Penerimaan:', '', '']]; t_op = 0
    for d, v in op_in: data.append([f"  {d}", v, '']); t_op+=v
    data.append(['Pengeluaran:', '', ''])
    for d, v in op_out: data.append([f"  {d}", v, '']); t_op+=v
    data.append(['Net Operasi', '', t_op])
    
    data.append(['ARUS KAS INVESTASI', '', '']); t_inv=0
    for d, v in inv: data.append([f"  {d}", v, '']); t_inv+=v
    data.append(['Net Investasi', '', t_inv])
    
    data.append(['ARUS KAS PENDANAAN', '', '']); t_fin=0
    for d, v in fin: data.append([f"  {d}", v, '']); t_fin+=v
    data.append(['Net Pendanaan', '', t_fin])
    data.append(['KENAIKAN KAS', '', t_op+t_inv+t_fin])
    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah', 'Total'])

def create_inventory_report(df):
    if df.empty: return pd.DataFrame()
    if 'movement_date' in df: df['movement_date'] = pd.to_datetime(df['movement_date']).dt.strftime('%Y-%m-%d')
    df['p_name'] = df['products'].apply(lambda x: x.get('name') if isinstance(x, dict) else 'Unknown')
    data = []
    for _, grp in df.groupby('product_id'):
        qty = 0; val = 0
        for _, r in grp.sort_values(['movement_date']).iterrows():
            q = r['quantity_change']; c = r['unit_cost']
            qty += q; val += (q * c)
            data.append({'Produk': grp['p_name'].iloc[0], 'Tanggal': r['movement_date'], 'Jenis': r['movement_type'], 'Ref': r['reference_id'],
                         'Masuk': q if q>0 else 0, 'Keluar': abs(q) if q<0 else 0, 'Biaya': c, 'Total': abs(q*c), 'Saldo Qty': qty, 'Saldo Nilai': val})
    res = pd.DataFrame(data)
    if res.empty: return res
    is_fst = res.groupby('Produk').cumcount() == 0
    res['Produk'] = np.where(is_fst, res['Produk'], '')
    return res

def to_excel(reports):
    out = BytesIO()
    with pd.ExcelWriter(out, engine='xlsxwriter') as writer:
        for k, v in reports.items(): 
            if isinstance(v, pd.DataFrame): v.to_excel(writer, sheet_name=k[:30], index=False)
    return out.getvalue()

# --- MAIN UI ---
def show_reports_page():
    st.title("📊 Laporan Keuangan")
    if st.sidebar.button("🔄 Refresh"): st.cache_data.clear(); st.rerun()
    rep = generate_reports()
    
    def fmt(df):
        d = df.copy()
        for c in d.columns:
            # Format Rupiah untuk kolom uang, KECUALI kolom Qty di Kartu Persediaan
            if any(x in c for x in ['Debit','Kredit','D','K','J','T','Nilai','Biaya','Saldo']) and 'Qty' not in c:
                d[c] = d[c].apply(lambda x: format_rupiah(x) if isinstance(x, (int,float)) else x)
        return d

    st.header("1. Jurnal Umum"); st.dataframe(fmt(rep["JU"]), hide_index=True, use_container_width=True)
    st.header("2. Buku Besar"); st.dataframe(fmt(rep["BB"]), hide_index=True, use_container_width=True)
    st.header("3. Worksheet"); st.dataframe(fmt(rep["WS"]), hide_index=True, use_container_width=True)
    
    c1, c2 = st.columns(2)
    with c1: st.header("4. Laba Rugi"); st.dataframe(fmt(rep["IS"]), hide_index=True, use_container_width=True)
    with c2: st.header("5. Perubahan Modal"); st.dataframe(fmt(rep["RE"]), hide_index=True, use_container_width=True)
    
    st.header("6. Posisi Keuangan"); st.dataframe(fmt(rep["BS"]), hide_index=True, use_container_width=True)
    st.header("7. Arus Kas"); st.dataframe(fmt(rep["CF"]), hide_index=True, use_container_width=True)
    st.header("8. Kartu Persediaan"); st.dataframe(fmt(rep["Kartu"]), hide_index=True, use_container_width=True)
    
    st.download_button("📥 Download Excel", data=to_excel(rep), file_name="Laporan_Keuangan.xlsx")

if __name__ == "__main__": show_reports_page()
