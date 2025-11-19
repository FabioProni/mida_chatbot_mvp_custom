import streamlit as st
from openai import OpenAI
import fitz  # PyMuPDF per estrarre testo dal PDF
import pandas as pd  # Per leggere file Excel
import os
import sys

# Valore di default per il tone of voice
DEFAULT_TONE = "Rispondi in modo sintetico, chiaro e professionale."


def get_secret(key, env_var=None, default=None):
    """Recupera un segreto da st.secrets o variabili d'ambiente, con valore di default opzionale."""
    env_var = env_var or key.upper()
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        # In debug st.secrets potrebbe non essere inizializzato
        pass

    env_value = os.getenv(env_var)
    if env_value not in (None, ""):
        return env_value

    if default is not None:
        return default

    raise KeyError(f"Missing secret '{key}' and environment variable '{env_var}'.")


def require_secret(key, env_var=None, friendly_name=None):
    """Richiede un segreto obbligatorio, mostra errore se mancante."""
    try:
        return get_secret(key, env_var)
    except KeyError:
        friendly = friendly_name or key
        env_display = env_var or key.upper()
        st.error(
            f"Configura il segreto '{friendly}' (st.secrets['{key}'] o variabile di ambiente '{env_display}')."
        )
        st.stop()

# Inietta CSS per personalizzare il colore delle icone nella chat
st.markdown(
    """
    <style>
      /* Icona utente (umano) */
      [data-testid="stHorizontalBlock"] [data-testid="stAvatar"][aria-label="user avatar"] svg path {
        fill: #4CAF50 !important;  /* utente */
      }
      /* Icona assistente (robot) */
      [data-testid="stHorizontalBlock"] [data-testid="stAvatar"][aria-label="assistant avatar"] svg path {
        fill: #00a1df !important;  /* robot */
      }

      .st-emotion-cache-16tyu1 h1 {
        font-size: 2.50rem !important;
      }
    </style>
    """,
    unsafe_allow_html=True
)

# Imposta la lingua italiana per tutta l'app
def set_italian_locale():
    import locale
    try:
        locale.setlocale(locale.LC_ALL, 'it_IT.UTF-8')
    except:
        pass
set_italian_locale()

# Carica i segreti necessari, con fallback per il debug locale
auth_password = require_secret("pw", env_var="APP_PASSWORD", friendly_name="password di accesso")
openai_api_key = require_secret(
    "openai_api_key",
    env_var="OPENAI_API_KEY",
    friendly_name="OpenAI API Key",
)

# Configura il client OpenAI con l'API Key
client = OpenAI(api_key=openai_api_key)

# Controllo autenticazione
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 Accesso a demo")
    
    with st.form("login_form"):
        password = st.text_input("Inserisci la password per accedere:", type="password")
        submit_button = st.form_submit_button("Accedi")
    
    if submit_button:
        if password == auth_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Password errata. Riprova.")
    
    st.stop()  # Ferma l'esecuzione del resto del codice finché non autenticati

# Configura lo stato della sessione
if "chats" not in st.session_state:
    st.session_state.chats = []  # Lista per memorizzare le chat
if "selected_chat" not in st.session_state:
    st.session_state.selected_chat = None  # Chat selezionata
if "pdf_text" not in st.session_state:
    st.session_state.pdf_text = ""  # Testo estratto dal PDF
if "tone_of_voice" not in st.session_state:
    # Prova a caricare il tone of voice dai secrets, altrimenti usa il default
    st.session_state.tone_of_voice = get_secret("tone_of_voice", env_var="TONE_OF_VOICE", default=DEFAULT_TONE)
if "show_tone_settings" not in st.session_state:
    st.session_state.show_tone_settings = False  # Controllo per mostrare il box di impostazione del tone of voice
if "messages" not in st.session_state:
    st.session_state.messages = []  # Memorizza la chat corrente
if "documents" not in st.session_state:
    st.session_state.documents = []  # Documenti caricati in sessione
if "skipped_media_files" not in st.session_state:
    st.session_state.skipped_media_files = set()  # PDF pre-caricati rimossi manualmente
if "last_added_document" not in st.session_state:
    st.session_state.last_added_document = None
if "last_removed_document" not in st.session_state:
    st.session_state.last_removed_document = None

# Se non esiste alcuna conversazione, ne creo una ed apro la prima
if not st.session_state.chats:
    st.session_state.chats.append({"id": "Conversazione 1", "messages": []})
if not st.session_state.selected_chat:
    st.session_state.selected_chat = st.session_state.chats[0]["id"]

# Mostra il logo dell'app
st.image("media/mida_logo_1000 AI6.png", width=350)
# st.image("media/landini_logo_web.png", width=350)

# Funzioni di supporto per la gestione dei documenti
def extract_text_from_pdf(pdf_path=None, file_bytes=None):
    """Estrae testo da un PDF dato il percorso o i bytes."""
    if pdf_path:
        doc = fitz.open(pdf_path)
    elif file_bytes:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    else:
        raise ValueError("Provide either pdf_path or file_bytes")

    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def refresh_combined_text():
    """Combina tutti i documenti caricati in un unico testo per il contesto."""
    if not st.session_state.documents:
        st.session_state.pdf_text = ""
        return

    combined = []
    for doc in st.session_state.documents:
        combined.append(f"=== Documento: {doc['name']} ===\n{doc.get('text', '')}")
    st.session_state.pdf_text = "\n\n".join(combined)


def add_document(name, text, source):
    """Aggiunge un documento alla lista dei documenti caricati."""
    # Verifica se il documento esiste già
    for doc in st.session_state.documents:
        if doc["name"] == name and doc["source"] == source:
            return  # Documento già caricato
    
    # Aggiungi alla lista documenti
    st.session_state.documents.append({
        "name": name,
        "text": text,
        "source": source
    })
    
    if source == "media":
        st.session_state.skipped_media_files.discard(name)
    
    refresh_combined_text()


def remove_document(index):
    """Rimuove un documento dalla lista."""
    removed = st.session_state.documents.pop(index)
    
    if removed["source"] == "media":
        st.session_state.skipped_media_files.add(removed["name"])
    
    refresh_combined_text()
    st.session_state.last_removed_document = removed["name"]
    st.rerun()


# Caricamento automatico di tutti i PDF presenti nella cartella media
media_dir = "media"
if os.path.isdir(media_dir):
    available_pdfs = sorted(
        f for f in os.listdir(media_dir) if f.lower().endswith(".pdf")
    )
    skipped = st.session_state.skipped_media_files
    for pdf_name in available_pdfs:
        if pdf_name in skipped:
            continue
        already_loaded = any(
            doc["name"] == pdf_name and doc["source"] == "media"
            for doc in st.session_state.documents
        )
        if already_loaded:
            continue
        pdf_path = os.path.join(media_dir, pdf_name)
        try:
            text = extract_text_from_pdf(pdf_path=pdf_path)
        except Exception as exc:
            st.sidebar.warning(f"Errore caricando '{pdf_name}': {exc}")
            continue
        add_document(pdf_name, text, source="media")

# Contenitore della sidebar per gestione documenti
st.sidebar.title("Gestione documenti")

if st.session_state.last_added_document:
    st.sidebar.success(f"Documento '{st.session_state.last_added_document}' caricato correttamente.")
    st.session_state.last_added_document = None

if st.session_state.last_removed_document:
    st.sidebar.info(f"Documento '{st.session_state.last_removed_document}' rimosso dalla sessione.")
    st.session_state.last_removed_document = None

if st.session_state.documents:
    st.sidebar.write("Documenti attivi:")
    for idx, doc in enumerate(st.session_state.documents):
        doc_cols = st.sidebar.columns((19, 1))
        doc_cols[0].write(f"• {doc['name']}")
        if doc_cols[1].button("✕", key=f"remove_doc_{idx}", help="Rimuovi documento"):
            remove_document(idx)
else:
    st.sidebar.info("Nessun documento disponibile.")

uploaded_file = st.sidebar.file_uploader(
    "📄 Aggiungi documento",
    type=["pdf", "xlsx", "xls"],
    key="document_uploader",
)

if uploaded_file:
    file_ext = uploaded_file.name.split('.')[-1].lower()
    if file_ext == "pdf":
        pdf_bytes = uploaded_file.read()
        text = extract_text_from_pdf(file_bytes=pdf_bytes)
        add_document(uploaded_file.name, text, source="upload")
        st.session_state.last_added_document = uploaded_file.name
        st.session_state.document_uploader = None
        st.rerun()
    elif file_ext in ["xlsx", "xls"]:
        df = pd.read_excel(uploaded_file)
        cleaned_data = (
            df.fillna('')
            .applymap(lambda x: str(x).strip() if pd.notnull(x) else '')
            .replace(r'^\s*$', '', regex=True)
        )
        excel_text = "\n".join(
            "|".join(row)
            for row in cleaned_data.astype(str).values
            if any(field.strip() for field in row)
        )
        add_document(uploaded_file.name, excel_text, source="upload")
        st.session_state.last_added_document = uploaded_file.name
        st.session_state.document_uploader = None
        st.rerun()

# Visualizza le chat esistenti nella sidebar
st.sidebar.divider()
st.sidebar.title("Gestione conversazioni")
if st.sidebar.button("➕ Nuova Conversazione"):
    chat_id = f"Conversazione {len(st.session_state.chats) + 1}"
    st.session_state.chats.append({"id": chat_id, "messages": []})
    st.session_state.selected_chat = chat_id

for chat in st.session_state.chats:
    if st.sidebar.button(chat["id"]):
        st.session_state.selected_chat = chat["id"]

# Pulsante per mostrare/nascondere le impostazioni del tone of voice
if st.sidebar.button("⚙️ Imposta Tone of Voice"):
    st.session_state.show_tone_settings = not st.session_state.show_tone_settings

# Modifica nella sezione delle impostazioni del tone of voice
if st.session_state.show_tone_settings:
    # Usa value per mantenere esplicitamente lo stato corrente
    new_tone = st.sidebar.text_area(
        "Modifica il tone of voice:",
        value=st.session_state.tone_of_voice,
        key="tone_input",
        help="Il tone of voice verrà applicato a tutte le nuove risposte"
    )
    # Pulsanti per gestire il reset
    col1, col2 = st.sidebar.columns(2)
    if col1.button("💾 Salva modifiche"):
        st.session_state.tone_of_voice = new_tone
    if col2.button("↩️ Ripristina default"):
        st.session_state.tone_of_voice = DEFAULT_TONE

# Visualizza la chat
st.title("🤖 Chiedi a MIDA")
if not st.session_state.selected_chat:
    st.write("Seleziona una conversazione o creane una nuova dalla barra laterale.")
else:
    chat_data = next(c for c in st.session_state.chats if c["id"] == st.session_state.selected_chat)
    
    # Visualizza i messaggi esistenti
    for message in chat_data["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Input per l'utente
    if user_input := st.chat_input("Fai una domanda sui tuoi documenti"):
        # Aggiungi e visualizza il messaggio dell'utente
        chat_data["messages"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Prepara i messaggi per la chiamata all'API
        messages_for_api = []
        
        # Se ci sono documenti caricati, includi il loro contenuto come contesto
        if st.session_state.pdf_text:
            messages_for_api.append({
                "role": "system",
                "content": f"Utilizza i seguenti documenti come contesto per rispondere alle domande:\n\n{st.session_state.pdf_text}\n\n"
            })
        
        # Aggiungi il tone of voice come istruzione prioritaria
        if st.session_state.tone_of_voice:
            messages_for_api.append({
                "role": "system",
                "content": f"ISTRUZIONE PRIORITARIA: {st.session_state.tone_of_voice}"
            })
        
        # Aggiungi i messaggi della conversazione
        messages_for_api.extend([{"role": m["role"], "content": m["content"]} for m in chat_data["messages"]])
        
        # Genera la risposta in streaming
        with st.chat_message("assistant"):
            response = st.write_stream(
                client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages_for_api,
                    stream=True,
                )
            )
        
        # Aggiungi la risposta generata alla conversazione
        chat_data["messages"].append({"role": "assistant", "content": response})