import streamlit as st
import gspread
import pandas as pd
from zoneinfo import ZoneInfo
from google.oauth2.service_account import Credentials
from supabase import create_client
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

# LÊ OS ID'S E CHAVES DIRETAMENTE DOS SECRETS DO STREAMLIT
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
# CONEXÃO COM GOOGLE SHEETS (SISTEMA DE USUÁRIOS)
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

# =====================================================
# CONEXÃO COM SUPABASE STORAGE (ARMAZENAMENTO GRATUITO)
# =====================================================
def conectar_supabase():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Erro nas credenciais do Supabase: {e}")
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
# MOTOR DE MARCA D'ÁGUA EM TODA A EXTENSÃO DO DOCUMENTO (MATRÍCULA EXCLUSIVA)
# =====================================================





def criar_pdf_marca_dagua(matricula):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    
    # 🎨 CONFIGURAÇÃO DA TRANSPARÊNCIA:
    # Definimos uma cor cinza escura pura (0, 0, 0 é preto, mas com o alpha baixo vira cinza)
    c.setFillColorRGB(0, 0, 0) 
    # 0.12 define a opacidade em cerca de 12% (fica bem sutil ao fundo)
    c.setFillAlpha(0.12)
    
    # Mantém o tamanho da fonte em 12
    c.setFont("Helvetica-Bold", 12)
    
    texto_rastreio = f"{matricula}"
    
    # Grade de repetição por toda a folha A4
    for x in range(-50, 650, 100):
        for y in range(-50, 900, 80):
            c.saveState()
            c.translate(x, y)
            c.rotate(35) # Inclinação padrão de segurança
            c.drawCentredString(0, 0, texto_rastreio)
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
# ENGINE DE COMUNICAÇÃO (SUPABASE STORAGE)
# =====================================================
def fazer_upload_escala(arquivo_bytes):
    supabase = conectar_supabase()
    if not supabase: return False
        
    try:
        supabase.storage.from_("escalas").upload(
            path="escala_servico_atual.pdf",
            file=arquivo_bytes,
            file_options={"cache-control": "0", "upsert": "true"}
        )
        return True
    except Exception as e:
        st.error(f"Erro no envio para o servidor Supabase: {e}")
        return False

def baixar_escala_original():
    supabase = conectar_supabase()
    if not supabase: return None
        
    try:
        dados = supabase.storage.from_("escalas").download("escala_servico_atual.pdf")
        return dados
    except Exception:
        return None

# =====================================================
# INTERFACES VISUAIS (VIEWS ADMINISTRATIVAS - CRUD)
# =====================================================
def view_gerenciar_escala_admin():
    aba_escala, aba_usuarios = st.tabs(["📅 Publicar Escala", "👥 Gerenciar Usuários (CRUD)"])
    
    # --- SUB-ABA 1: PUBLICAÇÃO DE ESCALAS ---
    with aba_escala:
        st.subheader("⚙️ Publicação e Atualização de Escalas")
        st.info("Carregue o arquivo em PDF. O sistema irá atualizar e disponibilizar este imediatamente para toda a Guarda.")
        
        arquivo_escala = st.file_uploader("Upload da Escala de Serviço (PDF)", type=["pdf"])
        if st.button("Publicar Escala Oficial"):
            if arquivo_escala:
                with st.spinner("Gravando arquivo no servidor seguro..."):
                    bytes_pdf = arquivo_escala.read()
                    if fazer_upload_escala(bytes_pdf):
                        st.success("Nova escala publicada com sucesso!")
                        registrar_log(st.session_state["nome_usuario"], "UPLOAD_ESCALA", arquivo_escala.name)
            else:
                st.warning("Selecione um documento em formato PDF antes de enviar.")

    # --- SUB-ABA 2: CRUD DE USUÁRIOS COMPLETO ---
    with aba_usuarios:
        st.subheader("👥 Painel de Controle de Usuários")
        
        df_users = carregar_usuarios()
        if df_users.empty:
            st.warning("Nenhum usuário cadastrado.")
            df_users = pd.DataFrame(columns=["id", "tipo_usuario", "login", "nome", "senha", "primeiro_acesso", "status"])
        
        df_users["id"] = pd.to_numeric(df_users["id"], errors="coerce").fillna(0).astype(int)
        df_users["login"] = df_users["login"].astype(str).str.strip()
        df_users["nome"] = df_users["nome"].astype(str).str.strip()
        df_users["status"] = df_users["status"].astype(str).str.strip().str.upper()
        
        col_cadastro, col_lista = st.columns([1, 2])
        
        # [C]REATE: FORMULÁRIO DE CADASTRO
        with col_cadastro:
            st.markdown("### ➕ Novo Cadastro")
            with st.form("form_cadastro_agente", clear_on_submit=True):
                novo_nome = st.text_input("Nome Funcional").strip().upper()
                nova_matricula = st.text_input("Matrícula / Login").strip()
                tipo_func = st.selectbox("Perfil", ["agente", "admin"])
                senha_padrao = st.text_input("Senha Inicial", type="password", value="1234")
                
                botao_cadastrar = st.form_submit_button("Salvar Usuário")
                
                if botao_cadastrar:
                    if not novo_nome or not nova_matricula:
                        st.error("Nome e Matrícula são obrigatórios.")
                    elif nova_matricula in df_users["login"].values:
                        st.error("⚠️ Esta matrícula já está cadastrada.")
                    else:
                        proximo_id = int(df_users["id"].max()) + 1 if not df_users.empty else 1
                        senha_hash = make_hashes(senha_padrao)
                        
                        usuarios_sheet.append_row([
                            proximo_id, tipo_func, nova_matricula, novo_nome, senha_hash, 1, "ATIVO"
                        ])
                        
                        st.success(f"✅ {novo_nome} cadastrado!")
                        registrar_log(st.session_state["nome_usuario"], "CRUD_CREATE", f"Matrícula: {nova_matricula}")
                        carregar_usuarios.clear()
                        st.rerun()
                        
        # [R]EAD, [U]PDATE, RESET & [D]ELETE
        with col_lista:
            st.markdown("### 📝 Usuários Cadastrados")
            st.caption("Selecione um usuário abaixo para editar seus dados, redefinir a senha ou excluí-lo.")
            
            lista_usuarios = ["-- Selecione um usuário para gerenciar --"]
            for _, r in df_users.iterrows():
                lista_usuarios.append(f"ID {r['id']} | {r['nome']} ({r['login']}) - [{r['status']}]")
                
            usuario_selecionado = st.selectbox("Buscar/Editar Usuário", lista_usuarios)
            
            if usuario_selecionado != "-- Selecione um usuário para gerenciar --":
                id_selecionado = int(usuario_selecionado.split("ID ")[1].split(" |")[0])
                linha_planilha, dados_user = localizar_linha_usuario_por_id(id_selecionado)
                
                if linha_planilha:
                    st.markdown(f"#### Editando: **{dados_user['nome']}**")
                    
                    with st.form("form_edicao_usuario"):
                        edit_nome = st.text_input("Alterar Nome Funcional", value=str(dados_user['nome'])).strip().upper()
                        edit_login = st.text_input("Alterar Matrícula / Login", value=str(dados_user['login'])).strip()
                        edit_tipo = st.selectbox("Alterar Perfil", ["agente", "admin"], index=0 if dados_user['tipo_usuario'] == "agente" else 1)
                        edit_status = st.selectbox("Status da Conta", ["ATIVO", "INATIVO"], index=0 if str(dados_user['status']).upper() == "ATIVO" else 1)
                        
                        col_btn1, col_btn2 = st.columns(2)
                        with col_btn1:
                            salvar_edicao = st.form_submit_button("💾 Salvar Alterações")
                        with col_btn2:
                            forcar_reset = st.form_submit_button("🔄 Redefinir para Senha Padrão (1234)")
                    
                    if salvar_edicao:
                        if not edit_nome or not edit_login:
                            st.error("Campos não podem ficar vazios.")
                        else:
                            usuarios_sheet.update(f"B{linha_planilha}:D{linha_planilha}", [[edit_tipo, edit_login, edit_nome]])
                            usuarios_sheet.update(f"G{linha_planilha}", [[edit_status]])
                            st.success("Dados updated com sucesso!")
                            registrar_log(st.session_state["nome_usuario"], "CRUD_UPDATE", f"ID: {id_selecionado}")
                            carregar_usuarios.clear()
                            time.sleep(1)
                            st.rerun()
                            
                    if forcar_reset:
                        senha_padrao_hash = make_hashes("1234")
                        usuarios_sheet.update(f"E{linha_planilha}:F{linha_planilha}", [[senha_padrao_hash, 1]])
                        st.success("🔄 Senha resetada para '1234'! O usuário deverá trocá-la no próximo login.")
                        registrar_log(st.session_state["nome_usuario"], "CRUD_PASSWORD_RESET", f"ID: {id_selecionado}")
                        carregar_usuarios.clear()
                        time.sleep(2)
                        st.rerun()
                        
                    st.markdown("---")
                    st.markdown("⚠️ **Zona de Perigo**")
                    if st.button("❌ Excluir Usuário do Sistema"):
                        if id_selecionado == 1:
                            st.error("Não é possível deletar o Administrador Master do sistema.")
                        else:
                            with st.spinner("Removendo do banco de dados..."):
                                usuarios_sheet.delete_rows(linha_planilha)
                                st.error(f"Usuário permanentemente excluído.")
                                registrar_log(st.session_state["nome_usuario"], "CRUD_DELETE", f"Nome: {dados_user['nome']}")
                                carregar_usuarios.clear()
                                time.sleep(1.5)
                                st.rerun()

# =====================================================
# INTERFACE DO AGENTE (VISUALIZAÇÃO COM MARCA D'ÁGUA)
# =====================================================
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
# RENDERIZAÇÃO E CONTROLE DE TELAS (LOGIN / SENHA)
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
    st.sidebar.write(f"Usuário ativo: **{st.session_state['nome_usuario']}**")
    st.sidebar.write(f"Credencial: `{st.session_state['tipo_usuario'].upper()}`")
    
    if st.session_state["tipo_usuario"] == "admin":
        menu = st.sidebar.radio("Navegação", ["Painel Admin", "Relatório de Logs"])
        
        if menu == "Painel Admin":
            view_gerenciar_escala_admin()
        elif menu == "Relatório de Logs":
            st.subheader("📋 Auditoria Geral de Acesso a Escalas")
            st.dataframe(carregar_logs(), use_container_width=True)
    else:
        view_visualizar_escala_usuario()
        
    if st.sidebar.button("Desconectar / Sair"):
        logout()