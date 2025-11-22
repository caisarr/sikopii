import streamlit as st
from supabase import create_client, Client
from dotenv import load_dotenv
import os

# ===============================================
# KONSTANTA KEAMANAN
# ===============================================
# GANTI DENGAN EMAIL KHUSUS PENJUAL/ADMIN ANDA
ALLOWED_SELLER_EMAIL = "c4isar@gmail.com" 

# Memuat klien Supabase yang sudah ada
load_dotenv()
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

# --- SHARED ON ALL PAGES ---
st.logo("assets/lobster.png")
st.sidebar.markdown("Dibuat oleh Kelompok 13")

# --- FUNGSI AUTENTIKASI ---
def sign_up(email, password):
    try:
        user = supabase.auth.sign_up({"email": email, "password": password})
        return user
    except Exception as e:
        st.error(f"Pendaftaran Gagal: {e}")

def sign_in(email, password):
    try:
        user = supabase.auth.sign_in_with_password({"email": email, "password": password})
        return user
    except Exception as e:
        st.error(f"Masuk Gagal: {e}")

def sign_out():
    try:
        supabase.auth.sign_out()
        st.session_state.user_email = None
        st.session_state.user_role = None 
        st.rerun()
    except Exception as e:
        st.error(f"Keluar Gagal: {e}")

# --- FUNGSI NAVIGASI UNTUK PEMBELI ---
def buyer_app():
    # Definisikan semua halaman e-commerce untuk Pembeli
    Informasi_page = st.Page(
        "views/Tentang_kami.py",
        title="Tentang Kami",
        icon=":material/account_circle:",
    )

    produk_page = st.Page(
        "views/info_produk.py",
        title="Lebih banyak tentang Produk",
        icon=":material/smart_toy:",
    )
    
    pemesanan_page = st.Page(
        "views/pemesanan.py",
        title="Pemesanan",
        icon=":material/shopping_cart:",
        default=True, 
    )

    pg = st.navigation(
        {
        "Info": [Informasi_page],
        "Projects": [produk_page, pemesanan_page],
        }
    )
    pg.run()


# --- FUNGSI NAVIGASI UNTUK PENJUAL (AKUNTANSI) ---
def seller_app(user_email):
    # JIKA BERHASIL MELEWATI CHECK EMAIL, TAMPILKAN NAVIGASI AKUNTANSI
    
    # [Fase 3] Jurnal Umum Manual
    jurnal_umum_page = st.Page(
        "views/jurnal_umum.py", 
        title="Jurnal Umum Manual", 
        icon=":material/edit_document:",
        default=True, 
    )
    
    # [Fase 4] Laporan Keuangan
    laporan_page = st.Page(
        "views/laporan_keuangan.py", # Kita akan buat file ini selanjutnya
        title="Laporan Keuangan", 
        icon=":material/analytics:",
    )

    pg = st.navigation(
        {
        f"Accounting - {user_email}": [jurnal_umum_page, laporan_page],
        }
    )
    pg.run()


# --- SCREEN UTAMA (PENGARAHAN BERDASARKAN ROLE & EMAIL) ---
def main_app(user_email, user_role):
    # LOGIKA BARU: MEMBATASI AKSES PENJUAL
    if user_role == "Penjual":
        if user_email != ALLOWED_SELLER_EMAIL:
            st.error("Akses Penjual dibatasi. Hanya akun administrator yang diizinkan.")
            st.warning(f"Anda masuk sebagai {user_email}. Silakan logout.")
            
            # Tampilkan tombol logout dan hentikan eksekusi
            if st.button("Logout"):
                sign_out()
            return # Hentikan fungsi agar navigasi Penjual tidak dijalankan
        
        # Jika lolos pemeriksaan, jalankan aplikasi Penjual
        seller_app(user_email)
        
    else:
        # Role Pembeli (akses semua orang)
        buyer_app()
    
    # Tombol Logout selalu ada di sidebar
    st.sidebar.divider()
    if st.sidebar.button("Logout", key="logout_main"):
     sign_out()


# --- LOGIN SCREEN DENGAN PILIHAN ROLE ---
def auth_screen():
    st.title("Login untuk Mengakses Lobster ID")
    
    option = st.selectbox("Pilih Tindakan:", ["Masuk", "Buat Akun"])
    # Pilihan role untuk otentikasi
    selected_role = st.radio("Masuk Sebagai:", ["Pembeli", "Penjual"])
    
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if option == "Buat Akun" and st.button("Daftar"):
        user = sign_up(email, password)
        if user and user.user:
            st.success("Pendaftaran Berhasil. Cek Email Anda.")

    if option == "Masuk" and st.button("Masuk"):
        user = sign_in(email, password)
        if user and user.user:
            st.session_state.user_email = user.user.email
            st.session_state.user_role = selected_role 
            st.success(f"Selamat Datang Kembali, {email} ({selected_role})!")
            st.rerun()


# --- INITIALISASI SESSION STATE & JALANKAN APP ---
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "user_role" not in st.session_state:
    st.session_state.user_role = None


if st.session_state.user_email:
    main_app(st.session_state.user_email, st.session_state.user_role)
else:
    auth_screen()
