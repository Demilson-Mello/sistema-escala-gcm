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
from reportlab.lib.utils import simpleSplit

# =====================================================
# CONFIGURAÇÃO INICIAL DO STREAMLIT
# =====================================================
st.set_page_config(
    page_title="Controle Operacional e Escalas - GCM",
    layout="wide",
    initial_sidebar_state="expanded"
)

# =====================================================
# CONSTANTES E CONFIGURAÇÕES GERAIS
# =====================================================
TZ = ZoneInfo("America/Sao_Paulo")
STATUS_DEPOSITO = "DEPÓSITO"
STATUS_LIBERADO = "LIBERADO"

# ATENÇÃO: Substitua pelo ID da pasta do seu Google Drive onde as escalas ficarão salvas.
# Compartilhe esta pasta no Drive com o e-mail da sua Service Account como "Editor".
ID_PASTA_DRIVE = "SEU_ID_DE_PASTA_DO_DRIVE_AQUI" 

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
    .metric-card {
        background: linear-gradient(135deg, #0f172a, #1e293b);
        padding: 18px;
        border-radius: 18px;
        color: white;
        box-shadow: 0 4px 18px rgba(0,0,0,0.15);
        border: 1px solid rgba(255,255,255,0.08);
        min-height: 110px;
    }
    .metric-card h4 {
        margin: 0;
        font-size: 0.95rem;
        color: #cbd5e1;
        font-weight: 600;
    }
    .metric-card h2 {
        margin: 8px 0 0 0;
        font-size: 2rem;
        font-weight: 800;
        color: #ffffff;
    }
    div[data-testid="stCaptionContainer"] p {
        font-size: 0.85rem;
        color: #475569;
    }
</style>
""", unsafe_allow_html=True)

st.markdown(
    '<div class="main-title">🚓 Depósito Público & Gestão de Escalas | GCM</div>',
    unsafe_allow_html=True
)
st.markdown(
    '<div class="sub-title">Sistema integrado de controle operacional, inventário, auditoria e distribuição segura de escalas.</div>',
    unsafe_allow_html=True
)

# =====================================================
# FUNÇÕES DE SEGURANÇA / LOGIN
# =====================================================
def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    return make_hashes(password) == hashed_text

# =====================================================
# CONTROLE DE SESSÃO
# =====================================================
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
# CONEXÕES COM APIS DO GOOGLE (SHEETS & DRIVE)
# =====================================================
def conectar_planilha():
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(
            st.secrets["google_service_account"],
            scopes=scope
        )
        client = gspread.authorize(creds)
        planilha = client.open_by_key("1p4eVJjnubslCc5mmxj8aHApC6ZTPraD2mvKkD8gBOEI")
        aba = planilha.worksheet("veiculos")
        return aba
    except Exception as e:
        st.error(f"Erro ao conectar com a planilha: {e}")
        st.stop()

sheet = conectar_planilha()

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
# CONEXÃO / CRIAÇÃO DAS ABAS AUXILIARES
# =====================================================
def conectar_aba_usuarios():
    try:
        aba = sheet.spreadsheet.worksheet("usuarios")
    except Exception:
        aba = sheet.spreadsheet.add_worksheet(title="usuarios", rows=2000, cols=10)
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

def conectar_aba_retiradas():
    try:
        return sheet.spreadsheet.worksheet("retirada_pertences")
    except Exception:
        nova_aba = sheet.spreadsheet.add_worksheet(title="retirada_pertences", rows=1000, cols=10)
        nova_aba.append_row(["id_retirada", "id_veiculo", "placa", "data_retirada", "hora_retirada", "nome_retirante", "documento_retirante", "itens_retirados", "observacao_retirada", "agente_responsavel"])
        return nova_aba

def conectar_aba_log():
    try:
        return sheet.spreadsheet.worksheet("log_auditoria")
    except Exception:
        nova_aba = sheet.spreadsheet.add_worksheet(title="log_auditoria", rows=5000, cols=5)
        nova_aba.append_row(["data", "hora", "usuario", "acao", "detalhes"])
        return nova_aba

def conectar_aba_delegacia():
    try:
        return sheet.spreadsheet.worksheet("veiculos_delegacia")
    except Exception:
        nova_aba = sheet.spreadsheet.add_worksheet(title="veiculos_delegacia", rows=2000, cols=16)
        nova_aba.append_row(["id", "numero_grv", "placa", "marca", "modelo", "cor", "tipo", "procedencia", "data_entrada", "hora_entrada", "agente_entrada", "status", "data_saida", "hora_saida", "agente_saida", "observacoes"])
        return nova_aba

usuarios_sheet = conectar_aba_usuarios()
retirada_sheet = conectar_aba_retiradas()
log_sheet = conectar_aba_log()
delegacia_sheet = conectar_aba_delegacia()

# =====================================================
# FUNÇÕES DE LEITURA DE DADOS COM CACHE
# =====================================================
@st.cache_data(ttl=60)
def carregar_dados():
    dados = sheet.get_all_records()
    df = pd.DataFrame(dados)
    if not df.empty:
        df.columns = df.columns.str.strip().str.lower()
    return df

@st.cache_data(ttl=60)
def carregar_retiradas():
    dados = retirada_sheet.get_all_records()
    df = pd.DataFrame(dados)
    if not df.empty:
        df.columns = df.columns.str.strip().str.lower()
    return df

@st.cache_data(ttl=60)
def carregar_logs():
    dados = log_sheet.get_all_records()
    df = pd.DataFrame(dados)
    if not df.empty:
        df.columns = df.columns.str.strip().str.lower()
    return df

@st.cache_data(ttl=60)
def carregar_dados_delegacia():
    dados = delegacia_sheet.get_all_records()
    df = pd.DataFrame(dados)
    if not df.empty:
        df.columns = df.columns.str.strip().str.lower()
    return df

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

def limpar_cache_modulos(usuarios=False, veiculos=False, retiradas=False, logs=False, delegacia=False):
    if usuarios: carregar_usuarios.clear()
    if veiculos: carregar_dados.clear()
    if retiradas: carregar_retiradas.clear()
    if logs: carregar_logs.clear()
    if delegacia: carregar_dados_delegacia.clear()

# =====================================================
# GERAÇÃO DE IDs AUTOMÁTICOS
# =====================================================
def gerar_id(df):
    if df.empty or "id" not in df.columns: return 1
    df = df.copy()
    df["id"] = pd.to_numeric(df["id"], errors="coerce")
    df_ids_validos = df["id"].dropna()
    if df_ids_validos.empty: return 1
    return int(df_ids_validos.max()) + 1

def gerar_id_retirada(df):
    if df.empty or "id_retirada" not in df.columns: return 1
    df = df.copy()
    df["id_retirada"] = pd.to_numeric(df["id_retirada"], errors="coerce")
    ids_validos = df["id_retirada"].dropna()
    if ids_validos.empty: return 1
    return int(ids_validos.max()) + 1

def gerar_id_usuario(df):
    if df.empty or "id" not in df.columns: return 1
    ids = pd.to_numeric(df["id"], errors="coerce").dropna()
    if ids.empty: return 1
    return int(ids.max()) + 1

# =====================================================
# REGISTRO DE LOGS DO SISTEMA
# =====================================================
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
# FUNÇÕES DE USUÁRIOS VIA PLANILHA
# =====================================================
def localizar_linha_usuario_por_id(id_usuario):
    df = carregar_usuarios()
    if df.empty: return None, None
    df = df.copy()
    df["id"] = pd.to_numeric(df["id"], errors="coerce")
    resultado = df[df["id"] == int(id_usuario)]
    if resultado.empty: return None, None
    idx = resultado.index[0]
    return idx + 2, resultado.iloc[0]

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

def cadastrar_usuario_planilha(tipo_usuario, login, nome, senha_inicial):
    df = carregar_usuarios()
    if not df.empty:
        existe = df[
            (df["tipo_usuario"].astype(str).str.lower() == str(tipo_usuario).strip().lower()) &
            (df["login"].astype(str).str.lower() == str(login).strip().lower()) &
            (df["status"].astype(str).str.upper() == "ATIVO")
        ]
        if not existe.empty: return False

    novo_id = gerar_id_usuario(df)
    senha_hash = make_hashes(senha_inicial)
    usuarios_sheet.append_row([novo_id, str(tipo_usuario).strip().lower(), str(login).strip(), str(nome).strip().upper(), senha_hash, 1, "ATIVO"])
    registrar_log(st.session_state.get("nome_usuario", "SISTEMA"), "CADASTRO_USUARIO", f"TIPO {tipo_usuario} | LOGIN {login}")
    limpar_cache_modulos(usuarios=True, logs=True)
    return True

def alterar_senha_usuario_planilha(id_usuario, nova_senha):
    linha, user = localizar_linha_usuario_por_id(id_usuario)
    if linha is None: return False
    nova_senha_hash = make_hashes(nova_senha)
    usuarios_sheet.update(f"E{linha}:F{linha}", [[nova_senha_hash, 0]])
    registrar_log(user.get("nome", "USUARIO"), "ALTERACAO_SENHA", f"ID_USUARIO {id_usuario}")
    limpar_cache_modulos(usuarios=True, logs=True)
    return True

# =====================================================
# ADICIONADO: MANIPULAÇÃO DE PDFS E MARCA D'ÁGUA DINÂMICA
# =====================================================
def criar_pdf_marca_dagua(matricula):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setFillColorRGB(0.75, 0.75, 0.75)  # Cor cinza bem leve transparente
    c.setFont("Helvetica-Bold", 40)
    
    c.saveState()
    c.translate(300, 450)
    c.rotate(45)
    
    texto = f"COPIA DE SEGURANCA - MATRICULA: {matricula}"
    c.drawCentredString(0, 0, texto)
    c.drawCentredString(0, 150, texto)
    c.drawCentredString(0, -150, texto)
    
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
# ADICIONADO: INTEGRAÇÃO DE UPLOAD/DOWNLOAD COM O DRIVE
# =====================================================
def fazer_upload_escala(arquivo_bytes):
    drive_service = conectar_drive()
    if not drive_service: return False
        
    try:
        # Exclui arquivos antigos com o mesmo nome identificador fixo para não poluir o Drive
        query = f"'{ID_PASTA_DRIVE}' in parents and name = 'escala_servico_atual.pdf' and trashed = false"
        resultados = drive_service.files().list(q=query, fields="files(id)").execute()
        for f in resultados.get('files', []):
            drive_service.files().delete(fileId=f['id']).execute()
    except Exception:
        pass

    metadados = {'name': 'escala_servico_atual.pdf', 'parents': [ID_PASTA_DRIVE]}
    media = MediaIoBaseUpload(BytesIO(arquivo_bytes), mimetype='application/pdf', resumable=True)
    try:
        drive_service.files().create(body=metadados, media_body=media, fields='id').execute()
        return True
    except Exception as e:
        st.error(f"Erro no upload para o Drive: {e}")
        return False

def baixar_escala_original():
    drive_service = conectar_drive()
    if not drive_service: return None
        
    try:
        query = f"'{ID_PASTA_DRIVE}' in parents and name = 'escala_servico_atual.pdf' and trashed = false"
        resultados = drive_service.files().list(q=query, fields="files(id)").execute()
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
        st.error(f"Erro ao baixar do Drive: {e}")
        return None

# =====================================================
# VIEWS ESPECÍFICAS DE ESCALA
# =====================================================
def view_gerenciar_escala_admin():
    st.subheader("⚙️ Publicação de Escala de Serviço (Painel Admin)")
    st.info("Insira um novo arquivo em PDF para atualizar a escala exibida aos usuários.")
    
    arquivo_escala = st.file_uploader("Selecione o arquivo da escala", type=["pdf"])
    if st.button("Publicar e Atualizar no Google Drive"):
        if arquivo_escala:
            with st.spinner("Salvando arquivo de forma segura..."):
                bytes_pdf = arquivo_escala.read()
                if fazer_upload_escala(bytes_pdf):
                    st.success("Escala publicada com sucesso!")
                    registrar_log(st.session_state["nome_usuario"], "UPLOAD_ESCALA", arquivo_escala.name)
        else:
            st.warning("Selecione um arquivo PDF válido primeiro.")

def view_visualizar_escala_usuario():
    st.subheader("📅 Escala de Serviço Vigente")
    matricula = st.session_state.get("login_usuario", "SEM_MATRICULA").upper()
    
    with st.spinner("Injetando credenciais de auditoria em tempo real..."):
        pdf_original = baixar_escala_original()
        if pdf_original is None:
            st.warning("Nenhuma escala publicada pela administração no momento.")
            return
            
        pdf_com_marca = aplicar_marca_dagua(pdf_original, matricula)
        base64_pdf = base64.b64encode(pdf_com_marca).decode('utf-8')
        
        col_info, col_down = st.columns([3, 1])
        with col_info:
            st.caption(f"Visualização protegida para: **{st.session_state['nome_usuario']}** (Matrícula: `{matricula}`)")
        with col_down:
            st.download_button(
                label="📥 Exportar PDF Marcado",
                data=pdf_com_marca,
                file_name=f"Escala_{matricula}.pdf",
                mime="application/pdf",
                on_click=lambda: registrar_log(st.session_state["nome_usuario"], "DOWNLOAD_ESCALA", f"Matrícula: {matricula}")
            )
            
        pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="800" type="application/pdf"></iframe>'
        st.markdown(pdf_display, unsafe_allow_html=True)

# =====================================================
# FUNÇÕES RESTANTES DO SEU SISTEMA DE PÁTIO/DEPOSITO
# =====================================================
def preparar_dataframe(df):
    if df.empty: return df
    df = df.copy()
    df.columns = df.columns.str.strip().str.lower()
    mapa_alias = {}
    if "motivo da apreensão" in df.columns: mapa_alias["motivo da apreensão"] = "motivo_apreensao"
    if "agente entrada" in df.columns: mapa_alias["agente entrada"] = "agente_entrada"
    if "agente saída" in df.columns: mapa_alias["agente saída"] = "agente_saida"
    if "observações" in df.columns: mapa_alias["observações"] = "observacoes"
    if "número grv" in df.columns: mapa_alias["número grv"] = "numero_grv"
    if "número_grv" in df.columns: mapa_alias["número_grv"] = "numero_grv"
    if mapa_alias: df = df.rename(columns=mapa_alias)
    for col in df.columns:
        if col != "id": df[col] = df[col].astype(str)
    if "id" in df.columns: df["id"] = pd.to_numeric(df["id"], errors="coerce")
    return df

def registrar_entrada_patio(numero_grv, placa, marca, modelo, cor, tipo, motivo, data_entrada, hora_entrada, agente):
    df = carregar_dados()
    novo_id = gerar_id(df)
    sheet.append_row([novo_id, str(numero_grv).upper(), str(placa).upper(), str(marca).upper(), str(modelo).upper(), str(cor).upper(), str(tipo).upper(), str(motivo).upper(), data_entrada.strftime("%d/%m/%Y"), str(hora_entrada), str(agente).upper(), STATUS_DEPOSITO, "", "", "", ""])
    registrar_log(agente, "ENTRADA DE VEICULO", f"GRV {numero_grv} | PLACA {placa}")
    limpar_cache_modulos(veiculos=True, logs=True)

def registrar_saida_patio(id_veiculo, data_saida, hora_saida, agente_saida, observacoes=""):
    df = carregar_dados()
    df = preparar_dataframe(df)
    
    # Encontra o índice correspondente
    indices = df.index[df["id"] == id_veiculo]
    if len(indices) == 0:
        st.error("Erro ao localizar o veículo informado.")
        return
    linha = indices[0] + 2
    
    sheet.update(f"L{linha}:P{linha}", [[
        STATUS_LIBERADO,
        data_saida.strftime("%d/%m/%Y"),
        str(hora_saida),
        str(agente_saida).upper(),
        str(observacoes).upper()
    ]])
    
    placa = df.loc[df["id"] == id_veiculo, "placa"].values[0]
    registrar_log(agente_saida, "SAIDA VEICULO PATIO", f"ID {id_veiculo} | PLACA {placa}")
    limpar_cache_modulos(veiculos=True, logs=True)

# =====================================================
# RENDERIZAÇÃO PRINCIPAL DO MENUS E ACESSOS
# =====================================================
def renderizar_tela_login():
    st.sidebar.title("🔐 Autenticação")
    tipo = st.sidebar.selectbox("Tipo de Conta", ["agente", "admin"])
    login = st.sidebar.text_input("Matrícula/Login").strip()
    senha = st.sidebar.text_input("Senha", type="password")
    
    if st.sidebar.button("Entrar"):
        res = login_usuario_planilha(tipo, login, senha)
        if res["sucesso"]:
            st.session_state["logado"] = True
            st.session_state["usuario_id"] = res["id"]
            st.session_state["tipo_usuario"] = tipo
            st.session_state["nome_usuario"] = res["nome"]
            st.session_state["login_usuario"] = res["login"]
            st.session_state["primeiro_acesso"] = res["primeiro_acesso"]
            st.success(f"Bem-vindo {res['nome']}")
            time.sleep(1)
            st.rerun()
        else:
            st.sidebar.error("Dados de acesso inválidos ou usuário inativo.")

def view_alterar_senha_obrigatoria():
    st.warning("⚠️ Este é o seu primeiro acesso. Você deve alterar sua senha padrão para continuar.")
    nova_senha = st.text_input("Nova Senha", type="password")
    confirmar = st.text_input("Confirme a Nova Senha", type="password")
    
    if st.button("Salvar Nova Senha"):
        if len(nova_senha) < 4:
            st.error("A senha precisa ter pelo menos 4 caracteres.")
        elif nova_senha != confirmar:
            st.error("As senhas não coincidem.")
        else:
            if alterar_senha_usuario_planilha(st.session_state["usuario_id"], nova_senha):
                st.success("Senha atualizada com sucesso! Recarregando sistema...")
                st.session_state["primeiro_acesso"] = False
                time.sleep(1.5)
                st.rerun()

# Lógica de Controle de Fluxo Geral das Páginas
if not st.session_state["logado"]:
    renderizar_tela_login()
    st.info("Efetue o login no painel lateral para acessar as funcionalidades.")
elif st.session_state["primeiro_acesso"]:
    view_alterar_senha_obrigatoria()
else:
    # Painel do Usuário Autenticado
    st.sidebar.write(f"Usuário: **{st.session_state['nome_usuario']}**")
    st.sidebar.write(f"Perfil: `{st.session_state['tipo_usuario'].upper()}`")
    
    if st.session_state["tipo_usuario"] == "admin":
        menu = st.sidebar.radio("Selecione o Módulo", [
            "Controle do Pátio", 
            "Gerenciar Escalas", 
            "Auditoria de Logs"
        ])
        
        if menu == "Gerenciar Escalas":
            view_gerenciar_escala_admin()
        elif menu == "Auditoria de Logs":
            st.subheader("📋 Histórico Geral de Operações (Logs)")
            st.dataframe(carregar_logs(), use_container_width=True)
        elif menu == "Controle do Pátio":
            st.subheader("📦 Registro Operacional de Veículos")
            
            # Form de entrada simples ilustrativo para completar o fluxo do seu código original
            with st.expander("➕ Registrar Entrada de Veículo no Pátio"):
                grv = st.text_input("Nº GRV")
                placa = st.text_input("Placa")
                marca = st.text_input("Marca")
                modelo = st.text_input("Modelo")
                cor = st.text_input("Cor")
                tipo_v = st.selectbox("Tipo", ["MOTO", "CARRO", "OUTROS"])
                motivo = st.text_area("Motivo da Apreensão")
                
                if st.button("Salvar Entrada"):
                    registrar_entrada_patio(grv, placa, marca, modelo, cor, tipo_v, motivo, datetime.now(TZ), datetime.now(TZ).strftime("%H:%M"), st.session_state["nome_usuario"])
                    st.success("Entrada salva.")
                    time.sleep(1)
                    st.rerun()
                    
            # Mostra dados cadastrados
            df_patio = carregar_dados()
            if not df_patio.empty:
                st.dataframe(preparar_dataframe(df_patio), use_container_width=True)

    else: # Fluxo para Usuários Comuns (Agente)
        menu = st.sidebar.radio("Selecione o Módulo", ["Visualizar Escala", "Consulta de Frota"])
        
        if menu == "Visualizar Escala":
            view_visualizar_escala_usuario()
        elif menu == "Consulta de Frota":
            st.subheader("🔍 Painel de Consulta de Veículos em Custódia")
            df_patio = carregar_dados()
            if not df_patio.empty:
                st.dataframe(preparar_dataframe(df_patio), use_container_width=True)
            else:
                st.info("Nenhum veículo em custódia no momento.")
                
    if st.sidebar.button("Sair / Logout"):
        logout()