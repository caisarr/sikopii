from midtransclient import Snap
import os
import time  # <--- Tambahkan ini
from dotenv import load_dotenv
import streamlit as st 

load_dotenv()

# ... (Kode inisialisasi server key Anda tetap sama) ...

def create_transaction(order_id, gross_amount):
    if not snap:
        raise Exception("Midtrans Server Key belum dikonfigurasi.")
    
    # [PERBAIKAN] Buat ID unik dengan menambahkan timestamp
    # Format: "ID_DATABASE-TIMESTAMP" (Contoh: "12-1732512345")
    unique_order_id = f"{order_id}-{int(time.time())}"

    transaction_details = {
        "transaction_details": {
            "order_id": unique_order_id, 
            "gross_amount": int(gross_amount) 
        }
    }
    
    # Gunakan try-except untuk menangkap error detail dari Midtrans
    try:
        response = snap.create_transaction(transaction_details)
        return response['token']
    except Exception as e:
        print(f"Midtrans Error: {e}")
        raise e
