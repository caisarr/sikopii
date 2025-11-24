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
        # Pre-process tanggal di level fetch untuk keamanan
        if not df_ent.empty:
            df_ent['transaction_date'] = pd.to_datetime(df_ent['transaction_date'], errors='coerce')
            # Hapus timezone jika ada
            if df_ent['transaction_date'].dt.tz is not None:
                 df_ent['transaction_date'] = df_ent['transaction_date'].dt.tz_localize(None)
            df_ent['transaction_date'] = df_ent['transaction_date'].dt.normalize()
        else:
            df_ent = pd.DataFrame(columns=['id', 'transaction_date', 'description', 'order_id'])

        df_lines = pd.DataFrame(lines.data).fillna(0)
        if df_lines.empty:
             df_lines = pd.DataFrame(columns=['journal_id', 'account_code', 'debit_amount', 'credit_amount'])

        return {"lines": df_lines, "entries": df_ent, "coa": pd.DataFrame(coa.data), "mov": pd.DataFrame(inv.data)}
    except Exception as e:
        st.error(f"Error fetching data: {e}"); return {}

# --- CORE LOGIC ---
def get_data(start, end):
    d = fetch_all_accounting_data()
    if not d: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    
    ent = d["entries"].copy(); lines = d["lines"]
    if ent.empty or lines.empty:
        return pd.DataFrame(columns=['account_code', 'account_name', 'transaction_date', 'debit_amount', 'credit_amount', 'journal_id', 'description_entry', 'id']), d["coa"], d["mov"]
    
    # [FIX] Konversi Tanggal yang Aman dari Timezone Error
    ent['transaction_date'] = pd.to_datetime(ent['transaction_date'])
    if ent['transaction_date'].dt.tz is not None:
        ent['transaction_date'] = ent['transaction_date'].dt.tz_localize(None)
    
    filt = ent.loc[ent['transaction_date'] <= pd.to_datetime(end)].copy()
    
    if 'description' in filt.columns: filt.rename(columns={'description': 'description_entry'}, inplace=True)
    merged = lines.merge(filt, left_on='journal_id', right_on='id', suffixes=('_line', '_entry'))
    merged = merged.merge(d["coa"], on='account_code')
    return merged.sort_values(['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False]), d["coa"], d["mov"]

def calc_tb(df, coa):
    """Menghitung Neraca Saldo (TB) berdasarkan Saldo Netto (Debit/Kredit murni)"""
    if df.empty:
        tb = coa[['account_code', 'account_name', 'account_type']].copy()
        tb['Debit'] = 0.0; tb['Kredit'] = 0.0
        tb['Tipe_Num'] = tb['account_code'].str[0].apply(lambda x: int(x) if x.isdigit() else 0)
    else:
        tb = df.groupby('account_code').agg(D=('debit_amount', 'sum'), C=('credit_amount', 'sum')).reset_index()
        tb = tb.merge(coa, on='account_code', how='right').fillna(0)
        tb['Tipe_Num'] = tb['account_code'].str[0].astype(int)
        tb['Net'] = tb['D'] - tb['C']
        
        # Logika: Jika Net Positif, itu Debit. Jika Net Negatif, itu Kredit.
        tb['Debit'] = tb['Net'].apply(lambda x: x if x > 0 else 0)
        tb['Kredit'] = tb['Net'].apply(lambda x: abs(x) if x < 0 else 0)
    
    return tb[['account_code', 'account_name', 'account_type', 'Debit', 'Kredit', 'Tipe_Num']].rename(columns={'account_code':'Kode Akun', 'account_name':'Nama Akun', 'account_type':'Tipe'}).sort_values('Kode Akun')

def report_gj(df):
    if df.empty: return pd.DataFrame(columns=['Tanggal', 'Deskripsi', 'Kode Akun', 'Nama Akun', 'Debit', 'Kredit'])
    df = df.sort_values(['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False]).copy()
    df['Tanggal'] = df['transaction_date'].dt.strftime('%Y-%m-%d')
    if 'description_entry' not in df.columns: df['description_entry'] = ''
    df['Nama Akun'] = df.apply(lambda r: f"       {r['account_name']}" if r['debit_amount']==0 else r['account_name'], axis=1)
    is_first = df.groupby('journal_id').cumcount() == 0
    df['Tanggal'] = np.where(is_first, df['Tanggal'], '')
    df['description_entry'] = np.where(is_first, df['description_entry'], '')
    return df[['Tanggal', 'account_code', 'Nama Akun', 'description_entry', 'debit_amount', 'credit_amount']].rename(columns={'account_code':'Kode Akun', 'description_entry':'Deskripsi', 'debit_amount':'Debit', 'credit_amount':'Kredit'})

def report_gl(df, coa):
    if df.empty: return pd.DataFrame()
    data = []
    for ac, grp in df.groupby('account_code'):
        if coa[coa['account_code']==ac].empty: continue
        info = coa[coa['account_code']==ac].iloc[0]
        grp = grp.sort_values(['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False])
        bal = 0
        for _, r in grp.iterrows():
            d = r['debit_amount']; c = r['credit_amount']
            bal += (d - c) if info['normal_balance']=='Debit' else (c - d)
            data.append({'Kode': ac, 'Nama': info['account_name'], 'Tgl': r['transaction_date'], 'Ket': r.get('description_entry',''), 'Debit': d, 'Kredit': c, 'Saldo': bal})
    
    res = pd.DataFrame(data)
    if res.empty: return res
    res['Tgl'] = res['Tgl'].dt.strftime('%Y-%m-%d')
    res = res.merge(coa[['account_code', 'normal_balance']], left_on='Kode', right_on='account_code', how='left')
    
    res['Saldo D'] = res.apply(lambda r: r['Saldo'] if r['normal_balance']=='Debit' and r['Saldo']>=0 else (-r['Saldo'] if r['normal_balance']=='Credit' and r['Saldo']<0 else 0), axis=1)
    res['Saldo K'] = res.apply(lambda r: r['Saldo'] if r['normal_balance']=='Credit' and r['Saldo']>=0 else (-r['Saldo'] if r['normal_balance']=='Debit' and r['Saldo']<0 else 0), axis=1)
    
    fin = res[['Kode', 'Nama', 'Tgl', 'Ket', 'Debit', 'Kredit', 'Saldo D', 'Saldo K']].sort_values(['Kode', 'Tgl'])
    is_fst = fin.groupby('Kode').cumcount() == 0
    fin['Kode'] = np.where(is_fst, fin['Kode'], '')
    fin['Nama'] = np.where(is_fst, fin['Nama'], '')
    return fin

def report_inv(df):
    """Laporan Kartu Persediaan (QTY TIDAK RP)"""
    if df.empty: return pd.DataFrame()
    if 'movement_date' in df.columns: df['movement_date'] = pd.to_datetime(df['movement_date']).dt.strftime('%Y-%m-%d')
    df['p_name'] = df['products'].apply(lambda x: x.get('name') if isinstance(x, dict) else 'Unknown')
    data = []
    for pid, grp in df.groupby('product_id'):
        pname = grp['p_name'].iloc[0]; qty = 0; val = 0
        for _, r in grp.sort_values(['movement_date']).iterrows():
            q = r['quantity_change']; c = r['unit_cost']
            qty += q; val += (q * c)
            data.append({'Produk': pname, 'Tanggal': r['movement_date'], 'Jenis': r['movement_type'], 'Ref': r['reference_id'], 
                         'Masuk': q if r['movement_type']=='RECEIPT' else 0, 'Keluar': abs(q) if r['movement_type']=='ISSUE' else 0,
                         'Biaya': c, 'Total': abs(q*c), 'Sisa Qty': qty, 'Sisa Nilai': val})
    res = pd.DataFrame(data)
    if res.empty: return res
    is_fst = res.groupby('Produk').cumcount() == 0
    res['Produk'] = np.where(is_fst, res['Produk'], '')
    return res

def generate_reports():
    if "end_date" not in st.session_state: st.session_state.end_date = date(2025, 12, 31)
    if "start_date" not in st.session_state: st.session_state.start_date = date(2025, 10, 31)
    s = st.sidebar.date_input("Mulai", st.session_state.start_date); e = st.sidebar.date_input("Akhir", st.session_state.end_date)
    
    df, coa, mov = get_data(s, e)
    
    if not df.empty and 'journal_id' in df:
        pre = df[df['journal_id'] < 200]; ajp = df[df['journal_id'] >= 200]
    else: pre = df; ajp = df[0:0]
    
    tb_pre = calc_tb(pre, coa); tb_ajp = calc_tb(ajp, coa)
    
    ws = coa[['account_code', 'account_name', 'account_type', 'normal_balance']].copy()
    ws.columns = ['Kode Akun', 'Nama Akun', 'Tipe', 'Normal']
    ws = ws.merge(tb_pre[['Kode Akun', 'Debit', 'Kredit']], on='Kode Akun', how='left').fillna(0).rename(columns={'Debit':'TB D', 'Kredit':'TB K'})
    ws = ws.merge(tb_ajp[['Kode Akun', 'Debit', 'Kredit']], on='Kode Akun', how='left').fillna(0).rename(columns={'Debit':'MJ D', 'Kredit':'MJ K'})
    
    # Calculate Adjusted TB (TB ADJ)
    def calc_adj(row):
        net_tb = row['TB D'] - row['TB K']
        net_mj = row['MJ D'] - row['MJ K']
        net_final = net_tb + net_mj
        return (net_final, 0) if net_final >= 0 else (0, abs(net_final))
        
    ws[['Adj D', 'Adj K']] = ws.apply(lambda x: pd.Series(calc_adj(x)), axis=1)
    
    df_calc = ws[['Kode Akun', 'Nama Akun', 'Tipe', 'Adj D', 'Adj K']].rename(columns={'Adj D':'Debit', 'Adj K':'Kredit'})
    df_calc['Tipe_Num'] = df_calc['Kode Akun'].str[0].astype(int)
    
    inc, is_df, re_df, bs_df, ws_fin = calculate_closing_and_reporting_data(df_calc)
    
    # Final Worksheet Display
    ws_disp = ws.merge(ws_fin[['Kode Akun', 'IS Debit', 'IS Kredit', 'BS Debit', 'BS Kredit']], on='Kode Akun', how='left')
    ws_disp.rename(columns={'Adj D': 'TB ADJ D', 'Adj K': 'TB ADJ K'}, inplace=True)
    
    return {
        "JU": report_gj(df), "BB": report_gl(df, coa), "WS": ws_disp.drop(columns=['Tipe', 'Normal']),
        "IS": is_df, "RE": re_df, "BS": bs_df,
        "CF": create_cashflow(df), "Kartu": report_inv(mov), "Laba Bersih": inc
    }

def calculate_closing_and_reporting_data(df_tb_adj):
    AKUN_MODAL = '3-1100'; AKUN_PRIVE = '3-1200'
    Total_Revenue = df_tb_adj[df_tb_adj['Tipe_Num'].isin([4, 8])]['Kredit'].sum()
    Total_Expense = df_tb_adj[df_tb_adj['Tipe_Num'].isin([5, 6, 9])]['Debit'].sum()
    prive_val = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_PRIVE]['Debit'].sum()
    
    # Modal Awal (Ambil dari TB Adj Kredit, sebelum ditambah Laba)
    modal_awal = df_tb_adj[df_tb_adj['Kode Akun'] == AKUN_MODAL]['Kredit'].sum()
    
    Net_Income = Total_Revenue - Total_Expense
    Modal_Baru = modal_awal + Net_Income - prive_val
    
    df_ws_final = df_tb_adj.copy()
    df_ws_final['IS Debit'] = 0.0; df_ws_final['IS Kredit'] = 0.0; df_ws_final['BS Debit'] = 0.0; df_ws_final['BS Kredit'] = 0.0
    
    IS = df_ws_final['Tipe_Num'].isin([4, 5, 6, 8, 9])
    BS = df_ws_final['Tipe_Num'].isin([1, 2, 3])
    
    df_ws_final.loc[IS, 'IS Debit'] = df_ws_final['Debit']
    df_ws_final.loc[IS, 'IS Kredit'] = df_ws_final['Kredit']
    
    df_ws_final.loc[BS, 'BS Debit'] = df_ws_final['Debit']
    df_ws_final.loc[BS, 'BS Kredit'] = df_ws_final['Kredit'] 
    
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
    # Breakdown
    data.append(['Pendapatan Lain-lain:', '', ''])
    for _, r in get_rows([8], 'Kredit').iterrows(): data.append([f"  {r['Nama Akun']}", r['Kredit'], ''])
    data.append(['Beban Lain-lain:', '', ''])
    for _, r in get_rows([9], 'Debit').iterrows(): data.append([f"  {r['Nama Akun']}", -r['Debit'], ''])
    
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
    
    data.append(['LIABILITAS & EKUITAS', '', '']); data.append(['Liabilitas Lancar', '', ''])
    df_cl = df_bs[df_bs['Kode Akun'].str.startswith('2-1')]
    for _, r in df_cl.iterrows(): data.append([r['Nama Akun'], r['Kredit'] - r['Debit'], ''])
    total_cl = df_cl['Kredit'].sum() - df_cl['Debit'].sum(); data.append(['TOTAL LIABILITAS LANCAR', '', total_cl])
    
    data.append(['Liabilitas Jangka Panjang', '', ''])
    df_ll = df_bs[df_bs['Kode Akun'].str.startswith('2-2')]
    for _, r in df_ll.iterrows(): data.append([r['Nama Akun'], r['Kredit'] - r['Debit'], ''])
    total_ll = df_ll['Kredit'].sum() - df_ll['Debit'].sum(); data.append(['TOTAL LIABILITAS JANGKA PANJANG', '', total_ll])
    
    total_liab = total_cl + total_ll
    data.append(['TOTAL LIABILITAS', '', total_liab])
    
    data.append(['Ekuitas', '', ''])
    data.append(['Modal Pemilik Akhir', '', Modal_Akhir])
    data.append(['TOTAL LIABILITAS & EKUITAS', '', total_liab + Modal_Akhir])
    return pd.DataFrame(data, columns=['Deskripsi', 'Jumlah 1', 'Jumlah 2'])

def create_cashflow(df_journal):
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
    data.append(['ARUS KAS DARI AKTIVITAS OPERASI', '', '']); data.append(['Penerimaan Kas:', '', '']); t_op_in = 0
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

def show_reports_page():
    st.title("📊 Laporan Keuangan")
    st.sidebar.header("Filter")
    if st.sidebar.button("🔄 Refresh"): st.cache_data.clear(); st.rerun()
    rep = generate_reports()
    
    def fmt(df):
        d = df.copy()
        for c in d.columns:
            # Format Rupiah untuk kolom uang (KECUALI Qty)
            if any(x in c for x in ['Debit','Kredit','D','K','J','T','Nilai','Biaya']) and 'Qty' not in c:
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
    st.download_button("📥 Download Excel", data=to_excel_bytes(rep), file_name="Laporan_Keuangan.xlsx")

def to_excel_bytes(reports):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for name, df in reports.items():
            if isinstance(df, pd.DataFrame): df.to_excel(writer, sheet_name=name[:30], index=False)
    return output.getvalue()

if __name__ == "__main__": show_reports_page()
