import streamlit as st
from openai import OpenAI
import fitz  # PyMuPDF per estrarre testo dal PDF
import pandas as pd  # Per leggere file Excel
import os
import sys

DEFAULT_TONE = "Rispondi in modo sintetico, chiaro e professionale."


def get_secret(key, env_var=None):
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

    raise KeyError(f"Missing secret '{key}' and environment variable '{env_var}'.")


def require_secret(key, env_var=None, friendly_name=None):
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
    st.session_state.tone_of_voice = DEFAULT_TONE  # Prompt predefinito
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
if "assistant_id" not in st.session_state:
    # Recupera da secrets se esiste, altrimenti None
    st.session_state.assistant_id = get_secret("assistant_id", env_var="ASSISTANT_ID") if "assistant_id" in st.secrets or os.getenv("ASSISTANT_ID") else None
if "vector_store_id" not in st.session_state:
    st.session_state.vector_store_id = get_secret("vector_store_id", env_var="VECTOR_STORE_ID") if "vector_store_id" in st.secrets or os.getenv("VECTOR_STORE_ID") else None
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None  # ID del Thread per la conversazione corrente

# Sincronizza il tone of voice dall'assistant esistente (se presente)
if st.session_state.assistant_id and "tone_synced" not in st.session_state:
    try:
        assistant = client.beta.assistants.retrieve(st.session_state.assistant_id)
        if assistant.instructions:
            parts = assistant.instructions.split("documenti forniti. ", 1)
            if len(parts) == 2:
                st.session_state.tone_of_voice = parts[1]
        st.session_state.tone_synced = True
    except:
        pass

# Se non esiste alcuna conversazione, ne creo una ed apro la prima
if not st.session_state.chats:
    st.session_state.chats.append({"id": "Conversazione 1", "messages": []})
if not st.session_state.selected_chat:
    st.session_state.selected_chat = st.session_state.chats[0]["id"]

# Mostra il logo dell'app
st.image("media/mida_logo_1000.png", width=350)
# st.image("media/landini_logo_web.png", width=350)

# Funzioni di supporto per la gestione dei documenti
def extract_text_from_pdf(pdf_path=None, file_bytes=None):
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


# Funzioni per gestione Assistants API e Vector Store
def get_or_create_assistant():
    """Crea o recupera l'Assistant OpenAI con file search abilitato."""
    if st.session_state.assistant_id:
        try:
            assistant = client.beta.assistants.retrieve(st.session_state.assistant_id)
            
            # Estrai il tone of voice dalle istruzioni esistenti dell'assistant
            if assistant.instructions:
                # Le istruzioni sono nel formato: "Sei un assistente... {tone_of_voice}"
                # Estrai tutto dopo "documenti forniti. "
                parts = assistant.instructions.split("documenti forniti. ", 1)
                if len(parts) == 2:
                    existing_tone = parts[1]
                    # Aggiorna il tone of voice in sessione con quello dell'assistant
                    st.session_state.tone_of_voice = existing_tone
            
            return assistant
        except:
            st.session_state.assistant_id = None
    
    # Crea nuovo assistant con il tone of voice corrente
    assistant = client.beta.assistants.create(
        name="MIDA Chatbot Assistant",
        instructions=f"Sei un assistente esperto che risponde a domande basandoti sui documenti forniti. {st.session_state.tone_of_voice}",
        model="gpt-4o",  # gpt-4o è il modello più potente attualmente disponibile
        tools=[{"type": "file_search"}],
    )
    st.session_state.assistant_id = assistant.id
    
    # Mostra messaggio per salvare l'ID
    st.sidebar.info(f"Nuovo Assistant creato. Aggiungi ai secrets:\nassistant_id = '{assistant.id}'")
    
    return assistant


def get_or_create_vector_store():
    """Crea o recupera il Vector Store per i documenti."""
    if st.session_state.vector_store_id:
        try:
            vector_store = client.beta.vector_stores.retrieve(st.session_state.vector_store_id)
            return vector_store
        except:
            st.session_state.vector_store_id = None
    
    # Crea nuovo vector store
    vector_store = client.beta.vector_stores.create(
        name="MIDA Documents"
    )
    st.session_state.vector_store_id = vector_store.id
    
    # Collega il vector store all'assistant
    assistant = get_or_create_assistant()
    client.beta.assistants.update(
        assistant_id=assistant.id,
        tool_resources={"file_search": {"vector_store_ids": [vector_store.id]}},
    )
    
    # Mostra messaggio per salvare l'ID
    st.sidebar.info(f"Nuovo Vector Store creato. Aggiungi ai secrets:\nvector_store_id = '{vector_store.id}'")
    
    return vector_store


def sync_vector_store_files():
    """Sincronizza i file nel Vector Store con i documenti in sessione."""
    if not st.session_state.vector_store_id:
        return
    
    try:
        # Ottieni la lista dei file nel vector store
        vs_files = client.beta.vector_stores.files.list(
            vector_store_id=st.session_state.vector_store_id
        )
        
        # Crea un set dei file_id presenti nel vector store
        vs_file_ids = {f.id for f in vs_files.data}
        
        # Crea un set dei file_id nei documenti di sessione
        session_file_ids = {doc.get("file_id") for doc in st.session_state.documents if doc.get("file_id")}
        
        # Rimuovi file dal vector store che non sono più in sessione
        for file_id in vs_file_ids - session_file_ids:
            try:
                client.beta.vector_stores.files.delete(
                    vector_store_id=st.session_state.vector_store_id,
                    file_id=file_id
                )
            except:
                pass
                
    except Exception as e:
        pass  # Ignora errori di sincronizzazione


def upload_file_to_vector_store(file_path=None, file_bytes=None, filename=None):
    """Carica un file nel Vector Store OpenAI se non già presente."""
    vector_store = get_or_create_vector_store()
    
    # Verifica se un file con lo stesso nome esiste già nel vector store
    try:
        vs_files = client.beta.vector_stores.files.list(
            vector_store_id=vector_store.id
        )
        
        # Controlla se il filename è già presente
        for vs_file in vs_files.data:
            try:
                file_info = client.files.retrieve(vs_file.id)
                if file_info.filename == filename or (file_path and file_info.filename == os.path.basename(file_path)):
                    # File già presente, ritorna l'ID esistente
                    return vs_file.id
            except:
                continue
    except:
        pass
    
    # Carica nuovo file
    if file_path:
        with open(file_path, "rb") as f:
            file_obj = client.files.create(file=f, purpose="assistants")
    elif file_bytes and filename:
        import io
        file_obj = client.files.create(
            file=(filename, io.BytesIO(file_bytes)),
            purpose="assistants"
        )
    else:
        raise ValueError("Provide either file_path or (file_bytes + filename)")
    
    # Aggiungi il file al vector store
    client.beta.vector_stores.files.create(
        vector_store_id=vector_store.id,
        file_id=file_obj.id
    )
    
    return file_obj.id


def refresh_combined_text():
    """Mantiene pdf_text per compatibilità, ma non più usato con Assistants API."""
    if not st.session_state.documents:
        st.session_state.pdf_text = ""
        return

    combined = []
    for doc in st.session_state.documents:
        combined.append(f"Documento: {doc['name']}\n{doc.get('text', '')}")
    st.session_state.pdf_text = "\n\n".join(combined)


def add_document(name, text, source, file_path=None, file_bytes=None):
    """Aggiunge un documento e lo carica nel Vector Store."""
    # Verifica se il documento esiste già
    for doc in st.session_state.documents:
        if doc["name"] == name and doc["source"] == source:
            return  # Documento già caricato
    
    # Carica il file nel Vector Store (controlla duplicati internamente)
    try:
        if file_path:
            file_id = upload_file_to_vector_store(file_path=file_path, filename=name)
        elif file_bytes:
            file_id = upload_file_to_vector_store(file_bytes=file_bytes, filename=name)
        else:
            file_id = None
    except Exception as e:
        st.sidebar.error(f"Errore caricamento '{name}': {e}")
        return
    
    # Aggiungi alla lista documenti
    st.session_state.documents.append({
        "name": name,
        "text": text,
        "source": source,
        "file_id": file_id
    })
    
    if source == "media":
        st.session_state.skipped_media_files.discard(name)
    
    refresh_combined_text()


def remove_document(index):
    """Rimuove un documento dalla lista e dal Vector Store."""
    removed = st.session_state.documents.pop(index)
    
    # Rimuovi il file dal vector store
    if removed.get("file_id") and st.session_state.vector_store_id:
        try:
            client.beta.vector_stores.files.delete(
                vector_store_id=st.session_state.vector_store_id,
                file_id=removed["file_id"]
            )
        except:
            pass
    
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
        add_document(pdf_name, text, source="media", file_path=pdf_path)

# Sincronizza il vector store per rimuovere file obsoleti
sync_vector_store_files()

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
        add_document(uploaded_file.name, text, source="upload", file_bytes=pdf_bytes)
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

#st.warning(sys.getsizeof(st.session_state.pdf_text), icon="⚠️")

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
        # Aggiorna le istruzioni dell'assistant se esiste
        if st.session_state.assistant_id:
            try:
                client.beta.assistants.update(
                    assistant_id=st.session_state.assistant_id,
                    instructions=f"Sei un assistente esperto che risponde a domande basandoti sui documenti forniti. {new_tone}"
                )
            except:
                pass
    if col2.button("↩️ Ripristina default"):
        st.session_state.tone_of_voice = DEFAULT_TONE
        # Aggiorna le istruzioni dell'assistant se esiste
        if st.session_state.assistant_id:
            try:
                client.beta.assistants.update(
                    assistant_id=st.session_state.assistant_id,
                    instructions=f"Sei un assistente esperto che risponde a domande basandoti sui documenti forniti. {DEFAULT_TONE}"
                )
            except:
                pass

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
    if user_input := st.chat_input("Fai una domanda sul tuo PDF"):
        # Aggiungi e visualizza il messaggio dell'utente
        chat_data["messages"].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Usa Assistants API con file search
        try:
            # Assicurati che l'assistant sia creato
            assistant = get_or_create_assistant()
            
            # Crea o recupera il thread per questa conversazione
            if not st.session_state.thread_id:
                thread = client.beta.threads.create()
                st.session_state.thread_id = thread.id
            
            # Aggiungi il messaggio dell'utente al thread
            client.beta.threads.messages.create(
                thread_id=st.session_state.thread_id,
                role="user",
                content=user_input
            )
            
            # Esegui l'assistant
            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                full_response = ""
                
                import time
                import re
                
                # Mostra spinner animato mentre attende il primo token
                spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
                spinner_idx = 0
                received_first_token = False
                
                # Usa streaming API per feedback immediato
                with client.beta.threads.runs.stream(
                    thread_id=st.session_state.thread_id,
                    assistant_id=assistant.id,
                ) as stream:
                    for event in stream:
                        # Gestisci eventi di testo in streaming
                        if event.event == "thread.message.delta":
                            if hasattr(event.data, 'delta') and hasattr(event.data.delta, 'content'):
                                for content in event.data.delta.content:
                                    if hasattr(content, 'text') and hasattr(content.text, 'value'):
                                        received_first_token = True
                                        full_response += content.text.value
                                        # Rimuovi citazioni 【...】 che non sono cliccabili
                                        display_text = re.sub(r'【[^】]*】', '', full_response)
                                        message_placeholder.markdown(display_text)
                        
                        # Mostra spinner se non ha ancora ricevuto testo
                        if not received_first_token:
                            message_placeholder.markdown(f"{spinner_frames[spinner_idx % len(spinner_frames)]} _Elaborazione in corso..._")
                            spinner_idx += 1
                
                # Pulisci la risposta finale dalle citazioni
                full_response = re.sub(r'【[^】]*】', '', full_response)
                message_placeholder.markdown(full_response)
            
            # Aggiungi la risposta alla conversazione
            chat_data["messages"].append({"role": "assistant", "content": full_response})
            
        except Exception as e:
            st.error(f"Errore nella chiamata all'API: {str(e)}")
            chat_data["messages"].append({"role": "assistant", "content": f"Errore: {str(e)}"})