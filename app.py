import streamlit as st
import gspread
import pandas as pd
from zoneinfo import ZoneInfo
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from datetime import datetime
import hashlib
import time
import base64
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# =====================================================
# CONFIGURAÇÃO INICIAL DO STREAMLIT
# =====================================================
st.set_page_config(
    page_title="Gestão de Escalas - GCM",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Constante de fuso horário
TZ = ZoneInfo("America/Sao_Paulo")

# LÊ OS DE ID'S DIRETAMENTE DOS SECRETS DO STREAMLIT
ID_PASTA_DRIVE = st.secrets["ID_PASTA_DRIVE"]
ID_PLANILHA_MASTER = st.secrets["ID_PLANILHA_MASTER"]

# =====================================================
# CSS PERSONALIZADO DA INTERFACE
# =====================================================
st.markdown("""
<style>
    .main-title {
        font-size: 2rem;
        font-weight: 800;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        color: #6b7280;
        margin-bottom: 1.2rem;
    }
</style>
""", unsafe_allow_html=True)

st.markdown(
    '<div class="main-title">📅 Sistema de Distribuição Segura de Escalas | GCM</div>',
    unsafe_allow_html=True
)
st.markdown(
    '<div class="sub-title">Visualização e exportação de escalas com marca d\'água digital e rastreamento por auditoria.</div>',
    unsafe_allow_html=True
)

# =====================================================
# FUNÇÕES DE SEGURANÇA E SESSÃO
# =====================================================
def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    return make_hashes(password) == hashed_text

def init_session():
    valores_padrao = {
        "logado": False,
        "usuario_id": None,
        "tipo_usuario": None,
        "primeiro_acesso": False,
        "nome_usuario": "",
        "login_usuario": "",
    }
    for chave, valor in valores_padrao.items():
        if chave not in st.session_state:
            st.session_state[chave] = valor

init_session()

def logout():
    for chave in ["logado", "usuario_id", "tipo_usuario", "primeiro_acesso", "nome_usuario", "login_usuario"]:
        st.session_state[chave] = None
    st.session_state["logado"] = False
    st.rerun()

# =====================================================
# CONEXÕES COM GOOGLE APIS (SHEETS & DRIVE)
# =====================================================
def conectar_planilha():
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(
            st.secrets["google_service_account"],
            scopes=scope
        )
        client = gspread.authorize(creds)
        planilha = client.open_by_key(ID_PLANILHA_MASTER)
        return planilha
    except Exception as e:
        st.error(f"Erro ao conectar com o Google Sheets: {e}")
        st.stop()

try:
    planilha_master = conectar_planilha()
except:
    st.stop()

def conectar_drive():
    try:
        scope = ["https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(
            st.secrets["google_service_account"],
            scopes=scope
        )
        service = build('drive', 'v3', credentials=creds)
        return service
    except Exception as e:
        st.error(f"Erro ao conectar com o Google Drive: {e}")
        return None

# =====================================================
# GERENCIAMENTO AUTOMÁTICO DE ABAS DO SISTEMA
# =====================================================
def conectar_aba_usuarios():
    try:
        aba = planilha_master.worksheet("usuarios")
    except Exception:
        aba = planilha_master.add_worksheet(title="usuarios", rows=2000, cols=10)
        aba.append_row(["id", "tipo_usuario", "login", "nome", "senha", "primeiro_acesso", "status"])

    try:
        registros = aba.get_all_records()
        df = pd.DataFrame(registros)
        if df.empty or "login" not in df.columns:
            senha_hash = make_hashes("admin123")
            aba.append_row([1, "admin", "admin", "ADMINISTRADOR", senha_hash, 1, "ATIVO"])
        else:
            df.columns = df.columns.str.strip().str.lower()
            admin_existe = not df[
                (df["tipo_usuario"].astype(str).str.lower() == "admin") &
                (df["login"].astype(str).str.lower() == "admin") &
                (df["status"].astype(str).str.upper() == "ATIVO")
            ].empty
            if not admin_existe:
                ids = pd.to_numeric(df["id"], errors="coerce").dropna()
                novo_id = int(ids.max()) + 1 if not ids.empty else 1
                senha_hash = make_hashes("admin123")
                aba.append_row([novo_id, "admin", "admin", "ADMINISTRADOR", senha_hash, 1, "ATIVO"])
    except Exception:
        pass
    return aba

def conectar_aba_log():
    try:
        return planilha_master.worksheet("log_auditoria")
    except Exception:
        nova_aba = planilha_master.add_worksheet(title="log_auditoria", rows=5000, cols=5)
        nova_aba.append_row(["data", "hora", "usuario", "acao", "detalhes"])
        return nova_aba

usuarios_sheet = conectar_aba_usuarios()
log_sheet = conectar_aba_log()

# =====================================================
# LEITURA DE DADOS E LOGS (CACHE ATIVO)
# =====================================================
@st.cache_data(ttl=60)
def carregar_usuarios():
    dados = usuarios_sheet.get_all_records()
    df = pd.DataFrame(dados)
    if not df.empty:
        df.columns = df.columns.str.strip().str.lower()
        for col in ["id", "primeiro_acesso"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

@st.cache_data(ttl=60)
def carregar_logs():
    dados = log_sheet.get_all_records()
    df = pd.DataFrame(dados)
    if not df.empty:
        df.columns = df.columns.str.strip().str.lower()
    return df

def registrar_log(usuario, acao, detalhes=""):
    agora = datetime.now(TZ)
    log_sheet.append_row([
        agora.strftime("%d/%m/%Y"),
        agora.strftime("%H:%M:%S"),
        str(usuario).upper(),
        str(acao).upper(),
        str(detalhes).upper()
    ])
    carregar_logs.clear()

# =====================================================
# OPERAÇÕES DE USUÁRIOS
# =====================================================
def localizar_linha_usuario_por_id(id_usuario):
    df = carregar_usuarios()
    if df.empty: return None, None
    df = df.copy()
    df["id"] = pd.to_numeric(df["id"], errors="coerce")
    resultado = df[df["id"] == int(id_usuario)]
    if resultado.empty: return None, None
    return resultado.index[0] + 2, resultado.iloc[0]

def buscar_usuario_login(tipo_usuario, login):
    df = carregar_usuarios()
    if df.empty: return None
    filtros = (
        (df["tipo_usuario"].astype(str).str.strip().str.lower() == str(tipo_usuario).strip().lower()) &
        (df["login"].astype(str).str.strip().str.lower() == str(login).strip().lower()) &
        (df["status"].astype(str).str.strip().str.upper() == "ATIVO")
    )
    resultado = df[filtros]
    if resultado.empty: return None
    return resultado.iloc[0]

def login_usuario_planilha(tipo_usuario, login, senha):
    user = buscar_usuario_login(tipo_usuario, login)
    if user is not None and check_hashes(senha, str(user["senha"])):
        primeiro_acesso_val = user.get("primeiro_acesso", 0)
        try: primeiro_acesso_bool = bool(int(primeiro_acesso_val))
        except Exception: primeiro_acesso_bool = False
        return {
            "sucesso": True, "id": int(user["id"]), "nome": str(user["nome"]),
            "login": str(user["login"]), "primeiro_acesso": primeiro_acesso_bool
        }
    return {"sucesso": False, "id": None, "nome": None, "login": None, "primeiro_acesso": None}

def alterar_senha_usuario_planilha(id_usuario, nova_senha):
    linha, user = localizar_linha_usuario_por_id(id_usuario)
    if linha is None: return False
    nova_senha_hash = make_hashes(nova_senha)
    usuarios_sheet.update(f"E{linha}:F{linha}", [[nova_senha_hash, 0]])
    registrar_log(user.get("nome", "USUARIO"), "ALTERACAO_SENHA", f"ID_USUARIO {id_usuario}")
    carregar_usuarios.clear()
    return True

# =====================================================
# MOTOR DE MARCA D'ÁGUA EM TEMPO REAL
# =====================================================
def criar_pdf_marca_dagua(matricula):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setFillColorRGB(0.78, 0.78, 0.78)  # Marca d'água cinza suave
    c.setFont("Helvetica-Bold", 38)
    
    c.saveState()
    c.translate(300, 450)
    c.rotate(45)
    
    texto = f"COPIA DE SEGURANCA - MATRICULA: {matricula}"
    c.drawCentredString(0, 0, texto)
    c.drawCentredString(0, 160, texto)
    c.drawCentredString(0, -160, texto)
    
    c.restoreState()
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

def aplicar_marca_dagua(pdf_original_bytes, matricula):
    pdf_original = PdfReader(BytesIO(pdf_original_bytes))
    pdf_marca = PdfReader(criar_pdf_marca_dagua(matricula))
    
    escritor_pdf = PdfWriter()
    pagina_marca = pdf_marca.pages[0]
    
    for pagina in pdf_original.pages:
        pagina.merge_page(pagina_marca)
        escritor_pdf.add_page(pagina)
        
    buffer_saida = BytesIO()
    escritor_pdf.write(buffer_saida)
    buffer_saida.seek(0)
    return buffer_saida.getvalue()

# =====================================================
# COMUNICAÇÃO FLUXO GOOGLE DRIVE
# =====================================================
def fazer_upload_escala(arquivo_bytes):
    drive_service = conectar_drive()
    if not drive_service: return False
        
    try:
        # Busca por arquivos antigos suportando drives compartilhados e herança de cota
        query = f"'{ID_PASTA_DRIVE}' in parents and name = 'escala_servico_atual.pdf' and trashed = false"
        resultados = drive_service.files().list(
            q=query, 
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        for f in resultados.get('files', []):
            drive_service.files().delete(fileId=f['id'], supportsAllDrives=True).execute()
    except Exception:
        pass

    metadados = {'name': 'escala_servico_atual.pdf', 'parents': [ID_PASTA_DRIVE]}
    media = MediaIoBaseUpload(BytesIO(arquivo_bytes), mimetype='application/pdf', resumable=True)
    try:
        # Adicionado supportsAllDrives=True para forçar o consumo do armazenamento da pasta mãe (sua conta)
        drive_service.files().create(
            body=metadados, 
            media_body=media, 
            fields='id',
            supportsAllDrives=True
        ).execute()
        return True
    except Exception as e:
        st.error(f"Erro no upload para o Google Drive: {e}")
        return False

def baixar_escala_original():
    drive_service = conectar_drive()
    if not drive_service: return None
        
    try:
        query = f"'{ID_PASTA_DRIVE}' in parents and name = 'escala_servico_atual.pdf' and trashed = false"
        resultados = drive_service.files().list(
            q=query, 
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        arquivos = resultados.get('files', [])
        
        if not arquivos: return None
            
        file_id = arquivos[0]['id']
        requisicao = drive_service.files().get_media(fileId=file_id)
        buffer = BytesIO()
        baixador = MediaIoBaseDownload(buffer, requisicao)
        
        concluido = False
        while not concluido:
            _, concluido = baixador.next_chunk()
            
        buffer.seek(0)
        return buffer.getvalue()
    except Exception as e:
        st.error(f"Erro ao carregar escala do Drive: {e}")
        return None

# =====================================================
# INTERFACES VISUAIS (VIEWS)
# =====================================================
def view_gerenciar_escala_admin():
    st.subheader("⚙️ Publicação e Atualização de Escalas")
    st.info("Carregue o arquivo em PDF. O sistema irá arquivar o antigo e disponibilizar este imediatamente para toda a Guarda.")
    
    arquivo_escala = st.file_uploader("Upload da Escala de Serviço (PDF)", type=["pdf"])
    if st.button("Publicar no Google Drive"):
        if arquivo_escala:
            with st.spinner("Processando e enviando para nuvem..."):
                bytes_pdf = arquivo_escala.read()
                if fazer_upload_escala(bytes_pdf):
                    st.success("Nova escala publicada com sucesso!")
                    registrar_log(st.session_state["nome_usuario"], "UPLOAD_ESCALA", arquivo_escala.name)
        else:
            st.warning("Selecione um documento em formato PDF antes de enviar.")

def view_visualizar_escala_usuario():
    st.subheader("📅 Escala de Serviço Ativa")
    matricula = st.session_state.get("login_usuario", "SEM_MATRICULA").upper()
    
    with st.spinner("Construindo visualização criptografada com sua matrícula..."):
        pdf_original = baixar_escala_original()
        if pdf_original is None:
            st.warning("Nenhuma escala de serviço ativa encontrada no servidor.")
            return
            
        pdf_com_marca = aplicar_marca_dagua(pdf_original, matricula)
        base64_pdf = base64.b64encode(pdf_com_marca).decode('utf-8')
        
        col_info, col_down = st.columns([3, 1])
        with col_info:
            st.caption(f"Documento indexado para: **{st.session_state['nome_usuario']}** (Matrícula: `{matricula}`)")
        with col_down:
            st.download_button(
                label="📥 Exportar Escala com Marca d'Água",
                data=pdf_com_marca,
                file_name=f"Escala_Oficial_{matricula}.pdf",
                mime="application/pdf",
                on_click=lambda: registrar_log(st.session_state["nome_usuario"], "DOWNLOAD_ESCALA", f"Matrícula: {matricula}")
            )
            
        pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="850" type="application/pdf"></iframe>'
        st.markdown(pdf_display, unsafe_allow_html=True)

# =====================================================
# RENDERIZAÇÃO E CONTROLE DE TELAS
# =====================================================
def renderizar_tela_login():
    st.sidebar.title("🔐 Acesso Restrito")
    tipo = st.sidebar.selectbox("Função de Acesso", ["agente", "admin"])
    login = st.sidebar.text_input("Matrícula / Usuário").strip()
    senha = st.sidebar.text_input("Senha Corporativa", type="password")
    
    if st.sidebar.button("Entrar no Sistema"):
        res = login_usuario_planilha(tipo, login, senha)
        if res["sucesso"]:
            st.session_state["logado"] = True
            st.session_state["usuario_id"] = res["id"]
            st.session_state["tipo_usuario"] = tipo
            st.session_state["nome_usuario"] = res["nome"]
            st.session_state["login_usuario"] = res["login"]
            st.session_state["primeiro_acesso"] = res["primeiro_acesso"]
            st.success(f"Autenticado: {res['nome']}")
            time.sleep(1)
            st.rerun()
        else:
            st.sidebar.error("Credenciais inválidas ou conta inativa.")

def view_alterar_senha_obrigatoria():
    st.warning("⚠️ Primeiro acesso detectado. Por motivos de segurança, altere sua senha padrão para prosseguir.")
    nova_senha = st.text_input("Nova Senha", type="password")
    confirmar = st.text_input("Confirme a Senha", type="password")
    
    if st.button("Efetuar Alteração"):
        if len(nova_senha) < 4:
            st.error("A senha deve possuir no mínimo 4 caracteres.")
        elif nova_senha != confirmar:
            st.error("As senhas inseridas diferem.")
        else:
            if alterar_senha_usuario_planilha(st.session_state["usuario_id"], nova_senha):
                st.success("Senha atualizada! Redirecionando...")
                st.session_state["primeiro_acesso"] = False
                time.sleep(1.5)
                st.rerun()

# --- FLUXO PRINCIPAL DE EXECUÇÃO ---
if not st.session_state["logado"]:
    renderizar_tela_login()
    st.info("Acesse a barra lateral esquerda para entrar com suas credenciais.")
elif st.session_state["primeiro_acesso"]:
    view_alterar_senha_obrigatoria()
else:
    # Barra lateral de status fixa
    st.sidebar.write(f"Usuário ativo: **{st.session_state['nome_usuario']}**")
    st.sidebar.write(f"Credencial: `{st.session_state['tipo_usuario'].upper()}`")
    
    if st.session_state["tipo_usuario"] == "admin":
        menu = st.sidebar.radio("Navegação", ["Publicar Escala", "Relatório de Logs"])
        
        if menu == "Publicar Escala":
            view_gerenciar_escala_admin()
        elif menu == "Relatório de Logs":
            st.subheader("📋 Auditoria Geral de Acesso a Escalas")
            st.dataframe(carregar_logs(), use_container_width=True)
    else:
        # Usuário Comum entra direto na tela da escala dele
        view_visualizar_escala_usuario()
        
    if st.sidebar.button("Desconectar / Sair"):
        logout()