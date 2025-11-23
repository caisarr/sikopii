from midtransclient import Snap
import os
import time
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# Coba ambil key dari Environment Variable (.env)
SERVER_KEY = os.getenv("MIDTRANS_SERVER_KEY")

# Jika tidak ada di .env, coba ambil dari Streamlit Secrets (untuk Cloud)
if not SERVER_KEY:
    try:
        SERVER_KEY = st.secrets["MIDTRANS_SERVER_KEY"]
    except Exception:
        pass

# Inisialisasi Snap hanya jika key ditemukan
if SERVER_KEY:
    snap = Snap(
        is_production=False,
        server_key=SERVER_KEY
    )
else:
    snap = None
    # Jangan print error di sini agar tidak spam log saat import, 
    # error akan muncul saat fungsi dipanggil.

def create_transaction(order_id, gross_amount):
    """
    Membuat transaksi Midtrans dengan Order ID unik.
    Format Order ID yang dikirim: {DATABASE_ID}-{TIMESTAMP}
    """
    if not snap:
        raise Exception("Midtrans Server Key belum dikonfigurasi di .env atau Streamlit Secrets.")

    # Membuat Order ID unik agar tidak ditolak Midtrans (Sandbox issue)
    # Contoh: Order ID 15 di DB -> Dikirim sebagai "15-1732412345"
    unique_order_id = f"{order_id}-{int(time.time())}"

    transaction_details = {
        "transaction_details": {
            "order_id": unique_order_id,
            "gross_amount": int(gross_amount)  # Wajib Integer
        }
    }

    try:
        response = snap.create_transaction(transaction_details)
        return response['token']
    except Exception as e:
        print(f"Error creating Midtrans transaction: {e}")
        raise e
