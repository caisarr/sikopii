import streamlit as st
from pathlib import Path

import streamlit as st  # pip install streamlit
from PIL import Image  # pip install pillow

# --- PATH SETTINGS ---
THIS_DIR = Path(__file__).parent if "__file__" in locals() else Path.cwd()
ASSETS_DIR = THIS_DIR / "assets"

# --- GENERAL SETTINGS ---
CONTACT_EMAIL = "caisarmaldinianwar@gmail.com"
PRODUCT_NAME = "Lobster ID"
PRODUCT_TAGLINE = "Suplier Lobster favoritmu!"
PRODUCT_DESCRIPTION = """
Kelebihan dari Kopi Kami:

- Dipanen sampai biji berwarna merah, menghasilkan kopi yang benar benar matang
- Kopi pilihan dari Wirogomo, Jawa Tengah
- Rasa yang pas dan berkualitas

**Apa lagi yang harus diragukan? disini kami hanya menjual kualitas terbaik!**
"""



# --- MAIN SECTION ---
st.header(PRODUCT_NAME)
st.subheader(PRODUCT_TAGLINE)
left_col, right_col = st.columns((2, 1))
with left_col:
    st.text("")
    st.write(PRODUCT_DESCRIPTION)
with right_col:
    product_image = Image.open(ASSETS_DIR / "produkk.png")
    st.image(product_image, width=500)


# --- FEATURES ---
st.write("")
st.write("---")
st.subheader("Bagaimana Produk Kami Dirawat")
features = {
    "Penanaman.png": [
        "Penanaman dilakukan ditempat yang terjaga dan steril, sehingga memudahkan perawatan tanaman",
        "Tempat untukpenanaman dapat membantu kopi bertumbuh di masa rentan dan mdapat membuat pohon kopi sehat dan menghasilkan buah yg baik.",
    ],
    "kopi mentah.png": [
        "Kopi dipanen saat sudah mencapai tingkat matang maksimal",
        "Cherry kopi yang sudah matang maksimal dapat emnghasilkan rasa yang lebih nikmat.",
    ],
    "Penjemuran.png": [
        "Penjemuran ditempat yang sudah bersih dan steril",
        "Penjemuran di tempat yg baik dan bersih akan menghasilkan kopi kering yang baunya tidak terkontaminasi dengan aroma yg merusak"
    ],
}
for image, description in features.items():
    image = Image.open(ASSETS_DIR / image)
    st.write("")
    left_col, right_col = st.columns(2)
    left_col.image(image, use_container_width=True)
    right_col.write(f"**{description[0]}**")
    right_col.write(description[1])



