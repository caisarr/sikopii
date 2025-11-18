import streamlit as st
from supabase import create_client, Client
from dotenv import load_dotenv
import os

load_dotenv()
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

  # --- SHARED ON ALL PAGES ---
st.logo("assets/sikopii.png")
st.sidebar.markdown("Dibuat oleh Kelompok 13")

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
        st.rerun()
    except Exception as e:
        st.error(f"Keluar Gagal: {e}")

def main_app(user_email):
        # --- PAGE SETUP ---
        Informasi_page = st.Page(
            "views/Tentang_kami.py",
            title="Tentang Kami",
            icon=":material/account_circle:",
            default=True,
        )

        produk_page = st.Page(
            "views/info_produk.py",
            title="Lebih banyak tentang Produk",
            icon=":material/smart_toy:",
        )
        
        pemesanan_page = st.Page(
            "views/pemesanan.py",
            title="Pemesanan",
            icon=":material/smart_toy:",
        )



        # --- NAVIGATION SETUP [WITH SECTIONS]---
        pg = st.navigation(
            {

            "Info": [Informasi_page],
            "Projects": [produk_page, pemesanan_page],

            }
        )



        # --- RUN NAVIGATION ---
        pg.run()
        if st.button("Logout"):
         sign_out()

def auth_screen():
    st.title("Login untuk Mengakses Lobster ID")
    option = st.selectbox("Pilih Tindakan:", ["Masuk", "Buat Akun"])
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
            st.success(f"Selamat Datang Kembali, {email}!")
            st.rerun()

if "user_email" not in st.session_state:
    st.session_state.user_email = None

if st.session_state.user_email:
    main_app(st.session_state.user_email)
else:
    auth_screen()


    
