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
        inv = supabase.table("inventory_movements").select("*, products(name)").execute()
        lines = supabase.table("journal_lines").select("*").execute()
        entries = supabase.table("journal_entries").select("id, transaction_date, description, order_id").execute()
        coa = supabase.table("chart_of_accounts").select("*").execute()
        
        df_ent = pd.DataFrame(entries.data)
        if not df_ent.empty:
            df_ent['transaction_date'] = pd.to_datetime(df_ent['transaction_date'], errors='coerce')
            if df_ent['transaction_date'].dt.tz is not None:
                 df_ent['transaction_date'] = df_ent['transaction_date'].dt.tz_localize(None)
            df_ent['transaction_date'] = df_ent['transaction_date'].dt.normalize()
        else: df_ent = pd.DataFrame(columns=['id', 'transaction_date', 'description', 'order_id'])

        df_lines = pd.DataFrame(lines.data).fillna(0)
        if df_lines.empty: df_lines = pd.DataFrame(columns=['journal_id', 'account_code', 'debit_amount', 'credit_amount'])

        return {"lines": df_lines, "entries": df_ent, "coa": pd.DataFrame(coa.data), "mov": pd.DataFrame(inv.data)}
    except Exception as e:
        st.error(f"Error fetching data: {e}"); return {}

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
    if df.empty:
        tb = coa[['account_code', 'account_name', 'account_type']].copy()
        tb['Debit'] = 0.0; tb['Kredit'] = 0.0
        tb['Tipe_Num'] = tb['account_code'].str[0].apply(lambda x: int(x) if x.isdigit() else 0)
    else:
        tb = df.groupby('account_code').agg(D=('debit_amount', 'sum'), C=('credit_amount', 'sum')).reset_index()
        tb = tb.merge(coa, on='account_code', how='right').fillna(0)
        tb['Tipe_Num'] = tb['account_code'].str[0].astype(int)
        tb['Net'] = tb['D'] - tb['C']
        tb['Debit'] = tb.apply(lambda r: r['Net'] if r['normal_balance']=='Debit' and r['Net']>=0 else -r['Net'] if r['normal_balance']=='Credit' and r['Net']<0 else 0, axis=1)
        tb['Kredit'] = tb.apply(lambda r: r['Net'] if r['normal_balance']=='Credit' and r['Net']>=0 else -r['Net'] if r['normal_balance']=='Debit' and r['Net']<0 else 0, axis=1)
    
    return tb[['account_code', 'account_name', 'account_type', 'Debit', 'Kredit', 'Tipe_Num']].rename(columns={'account_code':'Kode Akun', 'account_name':'Nama Akun', 'account_type':'Tipe'}).sort_values('Kode Akun')

def report_gj(df):
    if df.empty: return pd.DataFrame(columns=['Tanggal', 'Deskripsi', 'Kode Akun', 'Nama Akun', 'Debit', 'Kredit'])
    df = df.sort_values(['transaction_date', 'journal_id', 'debit_amount'], ascending=[True, True, False]).copy()
    df['Tanggal'] = df['transaction_date'].dt.strftime('%Y-%m-%d')
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
    fin['Kode'] = np.where(is_fst, fin['Kode'], '')
    fin['Nama'] = np.where(is_fst, fin['Nama'], '')
    return fin

def report_inv(df):
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
    # [PERBAIKAN] Set Default Filter Tanggal: 31 Okt 2025 s/d 31 Des 2025
    if "end_date" not in st.session_state: st.session_state.end_date = date(2025, 12, 31)
    if "start_date" not in st.session_state: st.session_state.start_date = date(2025, 10, 31)
    
    s = st.sidebar.date_input("Mulai", st.session_state.start_date)
    e = st.sidebar.date_input("Akhir", st.session_state.end_date)
    
    df, coa, mov = get_data(s, e)
    
    # Split Data
    if not df.empty and 'journal_id' in df:
        pre = df[df['journal_id'] < 200]; ajp = df[df['journal_id'] >= 200]
    else: pre = df; ajp = df[0:0]
    
    # Calc TBs
    tb_pre = calc_tb(pre, coa); tb_ajp = calc_tb(ajp, coa)
    
    # Worksheet Logic
    ws = coa[['account_code', 'account_name', 'account_type', 'normal_balance']].copy()
    ws.columns = ['Kode Akun', 'Nama Akun', 'Tipe', 'Normal']
    ws = ws.merge(tb_pre[['Kode Akun', 'Debit', 'Kredit']], on='Kode Akun', how='left').fillna(0).rename(columns={'Debit':'TB D', 'Kredit':'TB K'})
    ws = ws.merge(tb_ajp[['Kode Akun', 'Debit', 'Kredit']], on='Kode Akun', how='left').fillna(0).rename(columns={'Debit':'MJ D', 'Kredit':'MJ K'})
    
    ws['Adj D'] = ws.apply(lambda r: max(0, (r['TB D']-r['TB K']) + (r['MJ D']-r['MJ K'])) if r['Normal']=='Debit' else max(0, -((r['TB D']-r['TB K']) + (r['MJ D']-r['MJ K']))), axis=1)
    ws['Adj K'] = ws.apply(lambda r: max(0, -((r['TB D']-r['TB K']) + (r['MJ D']-r['MJ K']))) if r['Normal']=='Debit' else max(0, ((r['TB D']-r['TB K']) + (r['MJ D']-r['MJ K']))), axis=1)
    
    # Financials
    # Income Statement (4,5,6,8,9)
    is_rows = ws[ws['Kode Akun'].str[0].isin(['4','5','6','8','9'])].copy()
    rev = is_rows[is_rows['Kode Akun'].str[0].isin(['4','8'])]['Adj K'].sum()
    exp = is_rows[is_rows['Kode Akun'].str[0].isin(['5','6','9'])]['Adj D'].sum()
    net_inc = rev - exp
    
    # Retained Earnings
    modal = ws[ws['Kode Akun']=='3-1100']['TB K'].sum() # Modal Awal dari TB
    prive = ws[ws['Kode Akun']=='3-1200']['Adj D'].sum()
    modal_akhir = modal + net_inc - prive
    
    # Balance Sheet (1,2,3)
    ws['IS D'] = 0.0; ws['IS K'] = 0.0; ws['BS D'] = 0.0; ws['BS K'] = 0.0
    
    ws.loc[ws['Kode Akun'].str[0].isin(['4','5','6','8','9']), 'IS D'] = ws['Adj D']
    ws.loc[ws['Kode Akun'].str[0].isin(['4','5','6','8','9']), 'IS K'] = ws['Adj K']
    
    ws.loc[ws['Kode Akun'].str[0].isin(['1','2','3']), 'BS D'] = ws['Adj D']
    ws.loc[ws['Kode Akun'].str[0].isin(['1','2']), 'BS K'] = ws['Adj K']
    ws.loc[ws['Kode Akun']=='3-1100', 'BS K'] = modal_akhir # Override Modal di BS Display
    ws.loc[ws['Kode Akun']=='3-1200', 'BS D'] = 0 # Prive sudah masuk modal akhir
    
    # Reports DataFrames
    # IS
    is_data = [['PENDAPATAN', '', '']]
    for _, r in is_rows[is_rows['Kode Akun'].str.startswith('4')].iterrows(): is_data.append([r['Nama Akun'], r['Adj K'], ''])
    is_data.append(['Total Pendapatan', '', is_rows[is_rows['Kode Akun'].str.startswith('4')]['Adj K'].sum()])
    is_data.append(['HPP', '', ''])
    for _, r in is_rows[is_rows['Kode Akun'].str.startswith('5')].iterrows(): is_data.append([r['Nama Akun'], r['Adj D'], ''])
    hpp = is_rows[is_rows['Kode Akun'].str.startswith('5')]['Adj D'].sum()
    is_data.append(['Total HPP', '', hpp])
    is_data.append(['LABA KOTOR', '', is_rows[is_rows['Kode Akun'].str.startswith('4')]['Adj K'].sum() - hpp])
    is_data.append(['BEBAN', '', ''])
    for _, r in is_rows[is_rows['Kode Akun'].str.startswith('6')].iterrows(): is_data.append([r['Nama Akun'], r['Adj D'], ''])
    is_data.append(['Total Beban', '', is_rows[is_rows['Kode Akun'].str.startswith('6')]['Adj D'].sum()])
    is_data.append(['LAIN-LAIN', '', ''])
    other_rev = is_rows[is_rows['Kode Akun'].str.startswith('8')]['Adj K'].sum()
    other_exp = is_rows[is_rows['Kode Akun'].str.startswith('9')]['Adj D'].sum()
    is_data.append(['Total Lain-lain', '', other_rev - other_exp])
    is_data.append(['LABA BERSIH', '', net_inc])
    
    # BS
    bs_rows = ws[ws['Kode Akun'].str[0].isin(['1','2','3'])].copy()
    bs_data = [['ASET', '', '']]
    for _, r in bs_rows[bs_rows['Kode Akun'].str.startswith('1')].iterrows(): 
        val = r['Adj D'] - r['Adj K']
        bs_data.append([r['Nama Akun'], val, ''])
    bs_data.append(['TOTAL ASET', '', bs_rows[bs_rows['Kode Akun'].str.startswith('1')]['Adj D'].sum() - bs_rows[bs_rows['Kode Akun'].str.startswith('1')]['Adj K'].sum()])
    
    bs_data.append(['LIABILITAS & EKUITAS', '', ''])
    liab = bs_rows[bs_rows['Kode Akun'].str.startswith('2')]
    for _, r in liab.iterrows(): bs_data.append([r['Nama Akun'], r['Adj K'] - r['Adj D'], ''])
    bs_data.append(['Total Liabilitas', '', liab['Adj K'].sum() - liab['Adj D'].sum()])
    bs_data.append(['Modal Akhir', '', modal_akhir])
    bs_data.append(['TOTAL LIAB & EKUITAS', '', (liab['Adj K'].sum() - liab['Adj D'].sum()) + modal_akhir])

    return {
        "JU": report_gj(df), "BB": report_gl(df, coa), "Kartu": report_inv(mov),
        "WS": ws.drop(columns=['Tipe', 'Normal']),
        "IS": pd.DataFrame(is_data, columns=['D', 'J', 'T']),
        "BS": pd.DataFrame(bs_data, columns=['D', 'J', 'T']),
        "RE": pd.DataFrame({'D': ['Modal Awal', 'Laba Bersih', 'Prive', 'Modal Akhir'], 'J': [modal, net_inc, -prive, modal_akhir]})
    }

def show_reports_page():
    st.title("📊 Laporan Keuangan")
    if st.sidebar.button("🔄 Refresh"): st.cache_data.clear(); st.rerun()
    rep = generate_reports()
    
    def fmt(df):
        d = df.copy()
        for c in d.columns:
            # Filter kolom yang harus format Rupiah (Saldo Qty excluded)
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
    st.header("7. Arus Kas"); st.dataframe(fmt(create_cashflow(get_data(st.session_state.start_date, st.session_state.end_date)[0])), hide_index=True, use_container_width=True)
    st.header("8. Kartu Persediaan"); st.dataframe(fmt(rep["Kartu"]), hide_index=True, use_container_width=True)
    
    out = BytesIO()
    with pd.ExcelWriter(out, engine='xlsxwriter') as writer:
        for k, v in rep.items(): v.to_excel(writer, sheet_name=k, index=False)
    st.download_button("📥 Excel", data=out.getvalue(), file_name="Laporan.xlsx")

# Helper for CF needed here because it wasn't in the main return
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

if __name__ == "__main__": show_reports_page()
