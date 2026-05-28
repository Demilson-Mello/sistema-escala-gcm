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
    page_title="Escala de Serviço- GCMCF",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Constante de fuso horário
TZ = ZoneInfo("America/Sao_Paulo")

# LÊ OS ID'S E CHAVES DIRETAMENTE DOS SECRETS DO STREAMLIT
ID_PLANILHA_MASTER = st.secrets["ID_PLANILHA_MASTER"]

# Listas auxiliares para seleção de data
MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril",
    "Maio", "Junho", "Julho", "Agosto",
    "Setembro", "Outubro", "Novembro", "Dezembro"
]

# Gera uma lista de anos (ano atual, anterior e próximos)
ANO_ATUAL = datetime.now(TZ).year
ANOS = [str(ano) for ano in range(ANO_ATUAL - 1, ANO_ATUAL + 3)]

# Dicionário base mapeando os prefixos das escalas
ESCALAS_DISPONIVEIS = {
    "1º Distrito": "escala_1_distrito",
    "2º Distrito": "escala_2_distrito",
    "Marítima e Ambiental": "escala_maritima_ambiental"
}

# Função auxiliar para gerar o nome do arquivo com o mês por extenso
def gerar_nome_arquivo(prefixo_escala, nome_mes, ano):
    mes_limpo = nome_mes.lower().replace("ç", "c")
    return f"{prefixo_escala}_{mes_limpo}_{ano}.pdf"

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
    .card-download {
        background-color: #f3f4f6;
        padding: 15px;
        border-radius: 8px;
        border-left: 5px solid #1d4ed8;
        margin-bottom: 10px;
    }
</style>
""", unsafe_allow_html=True)

st.markdown(
    '<div class="main-title">📅 Sistema de Escala de Serviço | GCMCF</div>',
    unsafe_allow_html=True
)
st.markdown(
    '<div class="sub-title">Download seguro de escalas com marca d\'água digital.</div>',
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
# CONEXÃO COM SUPABASE STORAGE
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
# MOTOR DE MARCA D'ÁGUA (MATRÍCULA EXCLUSIVA)
# =====================================================
def criar_pdf_marca_dagua(matricula):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    
    # Lista de opacidades variadas para quebrar o padrão geométrico que a IA procura
    opacidades = [0.15, 0.22, 0.28, 0.18]
    
    # Aumentamos o número de repetições do texto na mesma linha (de 35 para 50)
    linha_texto = "  ".join([f"{matricula}"] * 50)
    
    # CAMADA 1: Linhas inclinadas para a direita (35 graus)
    # Diminuímos o passo do 'range' de 35 para 20 (isso coloca o dobro de linhas na tela)
    for i, y in enumerate(range(-400, 1200, 20)): 
        c.saveState()
        opacidade_atual = opacidades[i % len(opacidades)]
        
        c.setFillColorRGB(0, 0, 0) # Vermelho de segurança
        c.setFillAlpha(opacidade_atual)
        c.setFont("Helvetica-Bold", 10) # Tamanho levemente menor para caber mais texto sem borrar
        
        # Deslocamento horizontal dinâmico
        x_dinamico = -200 - (y * 0.4) + (i % 3 * 15)
        
        c.translate(x_dinamico, y) 
        c.rotate(35)
        c.drawString(0, 0, linha_texto)
        c.restoreState()
        
    # CAMADA 2: Cruzamento Inverso (Linhas a -35 graus)
    # Diminuímos o passo de 70 para 40 para adensar também o cruzamento contra-IA
    for i, y in enumerate(range(-400, 1200, 40)): 
        c.saveState()
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.setFillAlpha(0.12)
        c.setFont("Helvetica-Bold", 9)
        
        x_dinamico = -100 + (y * 0.3)
        c.translate(x_dinamico, y)
        c.rotate(-35)
        c.drawString(0, 0, linha_texto)
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
def fazer_upload_escala(arquivo_bytes, nome_arquivo_supabase):
    supabase = conectar_supabase()
    if not supabase: return False
    try:
        supabase.storage.from_("escalas").upload(
            path=nome_arquivo_supabase,
            file=arquivo_bytes,
            file_options={"cache-control": "0", "upsert": "true"}
        )
        return True
    except Exception as e:
        st.error(f"Erro no envio para o servidor Supabase: {e}")
        return False

def baixar_escala_original(nome_arquivo_supabase):
    supabase = conectar_supabase()
    if not supabase: return None
    try:
        dados = supabase.storage.from_("escalas").download(nome_arquivo_supabase)
        return dados
    except Exception:
        return None

# =====================================================
# INTERFACES VISUAIS (VIEWS ADMINISTRATIVAS - CRUD)
# =====================================================
def view_gerenciar_escala_admin():
    aba_escala, aba_usuarios = st.tabs(["📅 Publicar Escalas", "👥 Gerenciar Usuários"])
    
    with aba_escala:
        st.subheader("⚙️ Publicação de Escalas por Período")
        col_escala, col_mes, col_ano = st.columns(3)
        with col_escala:
            escala_selecionada_admin = st.selectbox("Selecione a Escala:", list(ESCALAS_DISPONIVEIS.keys()))
        with col_mes:
            mes_selecionado_admin = st.selectbox("Mês de Referência:", MESES)
        with col_ano:
            ano_selecionado_admin = st.selectbox("Ano de Referência:", ANOS, index=1)
            
        prefixo = ESCALAS_DISPONIVEIS[escala_selecionada_admin]
        nome_arquivo_supabase = gerar_nome_arquivo(prefixo, mes_selecionado_admin, ano_selecionado_admin)
        
        arquivo_escala = st.file_uploader(f"Upload do arquivo para: {nome_arquivo_supabase}", type=["pdf"], key="uploader_admin")
        
        if st.button("Publicar Escala Oficial"):
            if arquivo_escala:
                with st.spinner(f"Gravando '{nome_arquivo_supabase}'..."):
                    bytes_pdf = arquivo_escala.read()
                    if fazer_upload_escala(bytes_pdf, nome_arquivo_supabase):
                        st.success(f"Escala **{escala_selecionada_admin}** de **{mes_selecionado_admin}/{ano_selecionado_admin}** publicada!")
                        registrar_log(st.session_state["nome_usuario"], "UPLOAD_ESCALA", f"{nome_arquivo_supabase}")
            else:
                st.warning("Selecione um documento antes de enviar.")

    with aba_usuarios:
        st.subheader("👥 Painel de Controle de Usuários")
        df_users = carregar_usuarios()
        if df_users.empty:
            df_users = pd.DataFrame(columns=["id", "tipo_usuario", "login", "nome", "senha", "primeiro_acesso", "status"])
        
        df_users["id"] = pd.to_numeric(df_users["id"], errors="coerce").fillna(0).astype(int)
        col_cadastro, col_lista = st.columns([1, 2])
        
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
                        st.error("Campos obrigatórios vazios.")
                    elif nova_matricula in df_users["login"].astype(str).values:
                        st.error("⚠️ Matrícula já cadastrada.")
                    else:
                        proximo_id = int(df_users["id"].max()) + 1 if not df_users.empty else 1
                        usuarios_sheet.append_row([proximo_id, tipo_func, nova_matricula, novo_nome, make_hashes(senha_padrao), 1, "ATIVO"])
                        st.success(f"✅ {novo_nome} cadastrado!")
                        carregar_usuarios.clear()
                        st.rerun()
                        
        with col_lista:
            st.markdown("### 📝 Usuários Cadastrados")
            lista_usuarios = ["-- Selecione um usuário para gerenciar --"]
            for _, r in df_users.iterrows():
                lista_usuarios.append(f"ID {r['id']} | {r['nome']} ({r['login']}) - [{r['status']}]")
            usuario_selecionado = st.selectbox("Buscar/Editar Usuário", lista_usuarios)
            
            if usuario_selecionado != "-- Selecione um usuário para gerenciar --":
                id_selecionado = int(usuario_selecionado.split("ID ")[1].split(" |")[0])
                linha_planilha, dados_user = localizar_linha_usuario_por_id(id_selecionado)
                
                if linha_planilha:
                    with st.form("form_edicao_usuario"):
                        edit_nome = st.text_input("Alterar Nome Funcional", value=str(dados_user['nome'])).strip().upper()
                        edit_login = st.text_input("Alterar Matrícula / Login", value=str(dados_user['login'])).strip()
                        edit_tipo = st.selectbox("Alterar Perfil", ["agente", "admin"], index=0 if dados_user['tipo_usuario'] == "agente" else 1)
                        edit_status = st.selectbox("Status da Conta", ["ATIVO", "INATIVO"], index=0 if str(dados_user['status']).upper() == "ATIVO" else 1)
                        col_btn1, col_btn2 = st.columns(2)
                        with col_btn1: salvar_edicao = st.form_submit_button("💾 Salvar Alterações")
                        with col_btn2: forcar_reset = st.form_submit_button("🔄 Redefinir Senha (1234)")
                    
                    if salvar_edicao:
                        usuarios_sheet.update(f"B{linha_planilha}:D{linha_planilha}", [[edit_tipo, edit_login, edit_nome]])
                        usuarios_sheet.update(f"G{linha_planilha}", [[edit_status]])
                        st.success("Dados alterados!")
                        carregar_usuarios.clear()
                        st.rerun()
                            
                    if forcar_reset:
                        usuarios_sheet.update(f"E{linha_planilha}:F{linha_planilha}", [[make_hashes("1234"), 1]])
                        st.success("Senha resetada para '1234'!")
                        carregar_usuarios.clear()
                        st.rerun()

# =====================================================
# NOVA INTERFACE DO AGENTE (APENAS LISTA DE DOWNLOADS)
# =====================================================
def view_visualizar_escala_usuario():
    st.subheader("📥 Central de Downloads - Escalas de Serviço")
    st.info("Selecione o mês e o ano abaixo para listar os documentos oficiais disponíveis para baixar.")
    
    matricula = st.session_state.get("login_usuario", "SEM_MATRICULA").upper()
    
    # Filtro de Período para não poluir a tela
    col_mes, col_ano = st.columns(2)
    with col_mes:
        mes_desejado = st.selectbox("Filtrar por Mês:", MESES)
    with col_ano:
        ano_desejado = st.selectbox("Filtrar por Ano:", ANOS, index=1)
        
    st.markdown("---")
    st.markdown("### 📋 Documentos do Período:")
    
    # Renderiza a lista de escalas disponíveis para o período filtrado
    for nome_exibicao, prefixo in ESCALAS_DISPONIVEIS.items():
        nome_arquivo_target = gerar_nome_arquivo(prefixo, mes_desejado, ano_desejado)
        
        # Estrutura visual em formato de lista/cards de download
        col_escala_info, col_botao_acao = st.columns([3, 1])
        
        with col_escala_info:
            st.markdown(f"""
            <div class="card-download">
                <strong>📌 {nome_exibicao}</strong><br>
                <small style="color: #4b5563;">Referência: {mes_desejado}/{ano_desejado} | Arquivo Base: {nome_arquivo_target}</small>
            </div>
            """, unsafe_allow_html=True)
            
        with col_botao_acao:
            # Espaçamento para alinhar o botão ao card
            st.write("")
            
            # O download e o processamento ocorrem apenas ao clicar no botão correspondente
            file_key = f"btn_{prefixo}_{mes_desejado}_{ano_desejado}"
            
            # Como precisamos baixar o arquivo para verificar se existe, fazemos isso sob demanda de clique
            pdf_original = baixar_escala_original(nome_arquivo_target)
            
            if pdf_original:
                # Se o arquivo existe no Supabase, gera o arquivo com a marca d'água em background
                pdf_com_marca = aplicar_marca_dagua(pdf_original, matricula)
                
                st.download_button(
                    label="📥 Baixar PDF",
                    data=pdf_com_marca,
                    file_name=f"{nome_arquivo_target.replace('.pdf', '')}_{matricula}.pdf",
                    mime="application/pdf",
                    key=file_key,
                    on_click=lambda f=nome_arquivo_target: registrar_log(
                        st.session_state["nome_usuario"], 
                        "DOWNLOAD_ESCALA", 
                        f"{f} | Matrícula: {matricula}"
                    )
                )
            else:
                st.button("❌ Não Publicada", key=file_key, disabled=True)

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
            st.sidebar.error("Credenciais inválidas.")

def view_alterar_senha_obrigatoria():
    st.warning("⚠️ Altere sua senha padrão para prosseguir.")
    nova_senha = st.text_input("Nova Senha", type="password")
    confirmar = st.text_input("Confirme a Senha", type="password")
    
    if st.button("Efetuar Alteração"):
        if len(nova_senha) < 4:
            st.error("Mínimo de 4 caracteres.")
        elif nova_senha != confirmar:
            st.error("As senhas diferem.")
        else:
            if alterar_senha_usuario_planilha(st.session_state["usuario_id"], nova_senha):
                st.success("Senha alterada!")
                st.session_state["primeiro_acesso"] = False
                time.sleep(1)
                st.rerun()

# Fluxo de Execução
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