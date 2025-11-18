import streamlit as st

st.title("Tentang Kami")

from forms.saran import saran_form


@st.dialog("Contact Me")
def show_saran_form():
    saran_form()


col1, col2 = st.columns(2, gap="small", vertical_alignment="center")
with col1:
 st.image("./assets/lobster1.png", width=230)
with col2:
   st.title("Lobster ID", anchor=False)
   st.write(
      "Kami jual lobster"
   )
   if st.button("Berikan saran"):
        show_saran_form()


# Informasi lebih Lanjut
st.write("\n")
st.subheader("Lebih banyak tentang kami", anchor=False)
st.write(
    """
    - Lobster enak
    - Aku cinta lobster
    - Lobster adalah temanku
    - Hidup Lobster
    """
)

