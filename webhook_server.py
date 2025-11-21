import os
import json
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from supabase_client import supabase # Menggunakan klien yang sudah ada
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Midtrans Webhook Listener & Accounting Processor")

# ===============================================
# FUNGSI AKUNTANSI (FASE 2)
# ===============================================

def record_sales_journal(order_id: int):
    """
    Mencatat Jurnal Penjualan dan HPP berdasarkan order_id.
    Ini adalah implementasi Double-Entry Bookkeeping.
    """
    try:
        # 1. Ambil Detail Pesanan dan Barang (Order Items)
        order_response = supabase.table("orders").select("*, order_items(*, products(cost_price))").eq("id", order_id).execute()
        
        if not order_response.data:
            print(f"ERROR: Order ID {order_id} tidak ditemukan untuk Jurnal.")
            return False

        order = order_response.data[0]
        total_revenue = order["total_amount"]
        total_cost = 0

        # Hitung Total HPP dari semua item
        for item in order["order_items"]:
            # Perhitungan HPP: quantity * cost_price
            cost_price = item["products"]["cost_price"] if item["products"] and item["products"]["cost_price"] else 0
            total_cost += item["quantity"] * cost_price

        # --- JURNAL 1: PENJUALAN (CASH vs REVENUE) ---
        
        # 2. Buat Entri Jurnal Utama (Header)
        journal = supabase.table("journal_entries").insert({
            "order_id": order_id,
            "description": f"Jurnal Penjualan Tunai Order ID: {order_id}",
            "user_id": order.get("user_id") # Ambil user_id dari tabel orders
        }).execute().data[0]
        journal_id = journal["id"]

        # 3. Catat Garis Jurnal (Lines)
        lines = []
        
        # DEBIT: KAS (1100) - Penerimaan Uang
        lines.append({
            "journal_id": journal_id, "account_code": "1100", 
            "debit_amount": total_revenue, "credit_amount": 0
        })
        
        # KREDIT: PENJUALAN KOPI (4000) - Pengakuan Pendapatan
        lines.append({
            "journal_id": journal_id, "account_code": "4000", 
            "debit_amount": 0, "credit_amount": total_revenue
        })

        # --- JURNAL 2: HPP (COST OF GOODS SOLD vs INVENTORY) ---
        if total_cost > 0:
            
            # DEBIT: HPP (5000) - Pengakuan Beban
            lines.append({
                "journal_id": journal_id, "account_code": "5000", 
                "debit_amount": total_cost, "credit_amount": 0
            })
            
            # KREDIT: PERSEDIAAN (1300) - Pengurangan Aset
            lines.append({
                "journal_id": journal_id, "account_code": "1300", 
                "debit_amount": 0, "credit_amount": total_cost
            })

        # 4. Simpan Semua Garis Jurnal ke Supabase
        supabase.table("journal_lines").insert(lines).execute()
        print(f"SUCCESS: Jurnal untuk Order {order_id} berhasil dicatat.")
        return True

    except Exception as e:
        print(f"FATAL ERROR PENCATATAN JURNAL untuk Order {order_id}: {e}")
        return False

# ===============================================
# FUNGSI MIDTRANS WEBHOOK
# ===============================================

@app.post("/midtrans/notification")
async def midtrans_notification(request: Request):
    """Endpoint Midtrans Notification URL."""
    try:
        payload = await request.json()
        
        order_id = payload.get("order_id")
        transaction_status = payload.get("transaction_status")
        transaction_id = payload.get("transaction_id")
        
        if not order_id:
            raise HTTPException(status_code=400, detail="Missing order_id in payload")

        print(f"Notifikasi diterima untuk Order ID: {order_id}. Status: {transaction_status}")

        new_status = ""
        journal_recorded = False

        if transaction_status == "capture" or transaction_status == "settlement":
            new_status = "settle"
            
            # PENTING: Panggil fungsi pencatatan jurnal
            # Perlu dipastikan order_id yang diterima dari Midtrans adalah tipe data int (dari Supabase order ID)
            journal_recorded = record_sales_journal(int(order_id)) 
            
        elif transaction_status == "pending":
            new_status = "pending"
        elif transaction_status in ["deny", "expire", "cancel"]:
            new_status = "failed"
        else:
            # Status lain (refund, partial, dll.)
            new_status = transaction_status
            
        # 1. UPDATE STATUS ORDER DI SUPABASE
        update_response = supabase.table("orders").update({
            "status": new_status,
            "midtrans_order_id": transaction_id # Simpan ID Transaksi Midtrans yang sebenarnya
        }).eq("id", int(order_id)).execute()

        if not update_response.data:
            print(f"ERROR: Gagal memperbarui status order {order_id} di Supabase.")
            # Kirim 500 jika update database gagal, agar Midtrans retry
            raise HTTPException(status_code=500, detail="Supabase update failed")

        return {"status": "ok", "journal_recorded": journal_recorded}

    except Exception as e:
        print(f"ERROR Processing Webhook: {e}")
        # Kirim 500 agar Midtrans mengulang notifikasi
        raise HTTPException(status_code=500, detail="Internal Server Error")


if __name__ == "__main__":
    uvicorn.run("webhook_server:app", host="0.0.0.0", port=8000, reload=True)
