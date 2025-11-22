import streamlit as st
from supabase_client import supabase
from midtrans_client import create_transaction
import os

# mendapatkan data produk
def get_products():
    return supabase.table("products").select("*").execute().data

# membuat order
def create_order(total_amount, address, midtrans_id): # <-- Tambahkan midtrans_id di parameter
    return supabase.table("orders").insert({
        "total_amount": total_amount,
        "address": address,
        "midtrans_order_id": midtrans_id, # <-- Simpan ID Midtrans
        "status": "pending" # Pastikan status default-nya pending
    }).execute().data[0]

# Menambahkan item pesanan
def add_order_item(order_id, product_id, quantity, sub_total):
    supabase.table("order_items").insert({
        "order_id": order_id,
        "product_id": product_id,
        "quantity": quantity,
        "sub_total": sub_total
    }).execute()

# Tampilkan produk kopi
def show_products():
    # Ambil daftar produk dari database Supabase
    products = get_products()
    st.title(" Pemesanan ")

    # Inisialisasi keranjang jika belum ada
    if "cart" not in st.session_state:
        st.session_state.cart = {}

    for p in products:
        st.image(p["image_url"], width=150)
        st.write(f"**{p['name']}**")
        st.write(p["description"])
        st.write(f"Rp {p['price']:,}")
        qty = st.number_input(f"Jumlah ({p['name']})", min_value=0, max_value=100, key=f"qty_{p['id']}")

        if st.button(f"Tambah ke Keranjang", key=f"add_{p['id']}"):
            if qty > 0:
                # Jika produk sudah ada, tambahkan jumlahnya
                if p["id"] in st.session_state.cart:
                    st.session_state.cart[p["id"]]["qty"] += qty
                else:
                    # Jika produk baru, tambahkan ke keranjang
                    st.session_state.cart[p["id"]] = {"product": p, "qty": qty}
                st.success(f"{qty} {p['name']} ditambahkan ke keranjang")


# Halaman keranjang dan pembayaran
def show_cart_and_payment():
    cart = st.session_state.get("cart", {})
    if not cart:
        st.info("Keranjang kosong")
        return

    total = sum(item["product"]["price"] * item["qty"] for item in cart.values())
    st.write("### Keranjang Anda")

    for item_id, item in cart.items():
        st.write(f"{item['product']['name']} x{item['qty']} = Rp {item['product']['price'] * item['qty']:,}")

    st.write(f"**Total: Rp {total:,}**")

    # Form alamat pada halaman yang sama
    st.write("### Alamat Pengiriman")
    address = st.text_area("Masukkan alamat lengkap Anda", placeholder="Jl. Kopi No. 1, Jakarta")

    if st.button("Bayar Sekarang"):
        if not address:
            st.error("Alamat tidak boleh kosong!")
            return

        # Buat order dengan alamat
        order = create_order(total, address)

        # Simpan semua item pesanan ke database
        for item in cart.values():
            add_order_item(order["id"], item["product"]["id"], item["qty"], item["product"]["price"] * item["qty"])

        # Buat transaksi Midtrans
        snap_token = create_transaction(order["id"], total)
        st.write("Klik tombol untuk membayar:")
        st.components.v1.html(f"""
        <script src="https://app.sandbox.midtrans.com/snap/snap.js" data-client-key="{os.getenv('MIDTRANS_CLIENT_KEY')}"></script>
        <button id="pay-button">Bayar Sekarang</button>
        <script>
            document.getElementById('pay-button').onclick = function() {{
                snap.pay('{snap_token}');
            }};
        </script>
        """, height=1000)

# Main aplikasi
def main():
        show_products()
        st.divider()
        show_cart_and_payment()

if __name__ == "__main__":
    main()


