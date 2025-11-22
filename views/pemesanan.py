import streamlit as st
from supabase_client import supabase
from midtrans_client import create_transaction
import os

# mendapatkan data produk (memastikan semua kolom diambil)
def get_products():
    # Mengambil semua kolom, termasuk kolom akuntansi yang baru ditambahkan
    return supabase.table("products").select("*, cost_price, inventory_account_code, hpp_account_code").execute().data

# membuat order
def create_order(total_amount, address):
    # Dapatkan user_id dari session state jika ada (untuk melengkapi tabel orders)
    user_email = st.session_state.get('user_email')
    
    # Keterangan: midtrans_order_id diisi NULL di sini, akan diisi oleh webhook
    return supabase.table("orders").insert({
        "total_amount": total_amount,
        "address": address,
        "status": "pending",
        # Anda dapat menambahkan kolom user_email jika ada di tabel orders
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
    products = get_products()
    st.title(" Pemesanan ")

    if "cart" not in st.session_state:
        st.session_state.cart = {}

    for p in products:
        # Perbaikan Error Gambar (Image URL check)
        if p.get("image_url"):
            st.image(p["image_url"], width=150)
        else:
            st.warning(f"Gambar untuk {p['name']} tidak ditemukan.")

        st.write(f"**{p['name']}**")
        st.write(p["description"])
        st.write(f"Rp {p['price']:,}")
        qty = st.number_input(f"Jumlah ({p['name']})", min_value=0, max_value=10, key=f"qty_{p['id']}")

        if st.button(f"Tambah ke Keranjang", key=f"add_{p['id']}"):
            if qty > 0:
                if p["id"] in st.session_state.cart:
                    st.session_state.cart[p["id"]]["qty"] += qty
                else:
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

    st.write("### Alamat Pengiriman")
    address = st.text_area("Masukkan alamat lengkap Anda", placeholder="Jl. Kopi No. 1, Jakarta")

    if st.button("Bayar Sekarang"):
        if not address:
            st.error("Alamat tidak boleh kosong!")
            return

        # 1. Buat order awal di Supabase untuk mendapatkan Order ID
        order = create_order(total, address)

        # 2. Simpan semua item pesanan ke database
        for item in cart.values():
            add_order_item(order["id"], item["product"]["id"], item["qty"], item["product"]["price"] * item["qty"])

        # 3. Buat transaksi Midtrans menggunakan Supabase Order ID
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
