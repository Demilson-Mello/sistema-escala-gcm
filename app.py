import streamlit as st
import pandas as pd
from zoneinfo import ZoneInfo
from supabase import create_client
from datetime import datetime
import hashlib
import time
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# =====================================================
# CONFIGURAÇÃO INICIAL DO STREAMLIT
# =====================================================
st.set_page_config(
    page_title="Escalas - GCMCF",
    layout="wide",
    initial_sidebar_state="expanded"
)

TZ = ZoneInfo("America/Sao_Paulo")

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril",
    "Maio", "Junho", "Julho", "Agosto",
    "Setembro", "Outubro", "Novembro", "Dezembro"
]

ANO_ATUAL = datetime.now(TZ).year
ANOS = [str(ano) for ano in range(ANO_ATUAL - 1, ANO_ATUAL + 3)]

ESCALAS_DISPONIVEIS = {
    "1º Distrito": "escala_1_distrito",
    "2º Distrito": "escala_2_distrito",
    "Marítima e Ambiental": "escala_maritima_ambiental"
}

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
    .escala-container {
        background-color: #ffffff;
        padding: 20px;
        border-radius: 8px;
        border: 1px solid #e5e7eb;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        margin-bottom: 20px;
    }
    .escala-titulo {
        font-size: 1.3rem;
        font-weight: 700;
        color: #1e3a8a;
        margin-bottom: 5px;
    }
    .escala-detalhe {
        color: #4b5563;
        font-size: 0.9rem;
        margin-bottom: 15px;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">📅 Sistema de Escalas | GCMCF</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Download seguro de escalas com marca d\'água digital e banco de dados Supabase.</div>', unsafe_allow_html=True)

# =====================================================
# FUNÇÕES DE SEGURANÇA E SESSÃO
# =====================================================
def make_hashes(password):
    return hashlib.sha256(str.encode(password.strip())).hexdigest()

def check_hashes(password, hashed_text):
    return make_hashes(password) == hashed_text.strip()

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
# CONEXÃO COM SUPABASE
# =====================================================
@st.cache_resource
def conectar_supabase():
    try:
        if "SUPABASE_URL" not in st.secrets or "SUPABASE_KEY" not in st.secrets:
            st.error("⚠️ Erro Crítico: As credenciais do Supabase não foram configuradas nos Secrets do Streamlit.")
            return None
        url = st.secrets["SUPABASE_URL"].strip()
        key = st.secrets["SUPABASE_KEY"].strip()
        return create_client(url, key)
    except Exception as e:
        st.error(f"Erro nas credenciais do Supabase: {e}")
        return None

supabase = conectar_supabase()

# =====================================================
# LEITURA DE DADOS E LOGS VIA SUPABASE
# =====================================================
@st.cache_data(ttl=5)
def carregar_usuarios():
    if not supabase:
        return pd.DataFrame()
    try:
        resposta = supabase.table("usuarios").select("*").order("id").execute()
        return pd.DataFrame(resposta.data)
    except Exception as e:
        st.error(f"Erro ao carregar usuários: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=5)
def carregar_logs():
    if not supabase:
        return pd.DataFrame()
    try:
        resposta = supabase.table("log_auditoria").select("*").order("id", desc=True).execute()
        return pd.DataFrame(resposta.data)
    except Exception as e:
        st.error(f"Erro ao carregar logs: {e}")
        return pd.DataFrame()

def registrar_log(usuario, acao, detalhes=""):
    if not supabase:
        return
    agora = datetime.now(TZ)
    try:
        supabase.table("log_auditoria").insert({
            "data": agora.strftime("%d/%m/%Y"),
            "hora": agora.strftime("%H:%M:%S"),
            "usuario": str(usuario).strip().upper(),
            "acao": str(acao).strip().upper(),
            "detalhes": str(detalhes).strip().upper()
        }).execute()
        carregar_logs.clear()
    except Exception as e:
        st.error(f"Falha ao registrar log no banco de dados: {e}")

# =====================================================
# OPERAÇÕES DE USUÁRIOS (LOGICA DE LOGIN REVISADA)
# =====================================================
def login_usuario_supabase(tipo_usuario, login, senha):
    if not supabase:
        return {"sucesso": False, "erro": "Sem conexão com o banco"}
    
    try:
        login_limpo = str(login).strip()
        tipo_limpo = str(tipo_usuario).strip().lower()
        
        # Busca traz apenas o login para evitar conflitos de múltiplos filtros no Postgres
        resposta = supabase.table("usuarios").select("*").eq("login", login_limpo).execute()
        
        if not resposta.data:
            return {"sucesso": False, "erro": f"Usuário com o login '{login_limpo}' não foi encontrado no banco."}
        
        user = resposta.data[0]
        
        # Validações granulares feitas no lado do Python (mais seguro e rastreável)
        if str(user.get("status")).strip().upper() != "ATIVO":
            return {"sucesso": False, "erro": "Usuário encontrado, mas o status não está como 'ATIVO'."}
            
        if str(user.get("tipo_usuario")).strip().lower() != tipo_limpo:
            return {"sucesso": False, "erro": f"Usuário encontrado, mas o perfil no banco é '{user.get('tipo_usuario')}' e você selecionou '{tipo_limpo}'."}
            
        # Validação de Hash de Senha
        if not check_hashes(senha, str(user["senha"])):
            return {
                "sucesso": False, 
                "erro": f"Senha incorreta. O sistema gerou o hash '{make_hashes(senha)}', mas no banco está salvo '{user['senha']}'."
            }
        
        # Se passou em tudo, define o primeiro acesso
        p_acesso_valor = user.get("primeiro_acesso", 1)
        primeiro_acesso_bool = True if str(p_acesso_valor) == "1" else False

        return {
            "sucesso": True, 
            "id": int(user["id"]), 
            "nome": str(user["nome"]).strip(),
            "login": str(user["login"]).strip(), 
            "primeiro_acesso": primeiro_acesso_bool
        }
        
    except Exception as e:
        return {"sucesso": False, "erro": f"Erro interno na execução da query: {e}"}

def alterar_senha_usuario_supabase(id_usuario, nova_senha):
    if not supabase:
        return False
    try:
        nova_senha_hash = make_hashes(nova_senha)
        resposta = supabase.table("usuarios").update({
            "senha": nova_senha_hash,
            "primeiro_acesso": 0
        }).eq("id", id_usuario).execute()
        
        if resposta.data:
            user = resposta.data[0]
            registrar_log(user.get("nome", "USUARIO"), "ALTERACAO_SENHA", f"ID_USUARIO {id_usuario}")
            carregar_usuarios.clear()
            return True
        return False
    except Exception as e:
        st.error(f"Erro ao alterar senha: {e}")
        return False

# =====================================================
# MOTOR DE MARCA D'ÁGUA ULTRA-DENSO ANTI-IA
# =====================================================
def criar_pdf_marca_dagua(matricula):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    opacidades = [0.15, 0.22, 0.28, 0.18]
    linha_texto = "  ".join([f"{matricula}"] * 50)
    
    for i, y in enumerate(range(-400, 1200, 20)): 
        c.saveState()
        opacidade_atual = opacidades[i % len(opacidades)]
        c.setFillColorRGB(0, 0, 0)
        c.setFillAlpha(opacidade_atual)
        c.setFont("Helvetica-Bold", 10)
        x_dinamico = -200 - (y * 0.4) + (i % 3 * 15)
        c.translate(x_dinamico, y) 
        c.rotate(35)
        c.drawString(0, 0, linha_texto)
        c.restoreState()
        
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
    try:
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
    except Exception as e:
        st.error(f"Erro ao processar marca d'água no PDF: {e}")
        return pdf_original_bytes

# =====================================================
# ENGINE DE COMUNICAÇÃO (SUPABASE STORAGE)
# =====================================================
def fazer_upload_escala(arquivo_bytes, nome_arquivo_supabase):
    if not supabase: 
        return False
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
    if not supabase: 
        return None
    try:
        dados = supabase.storage.from_("escalas").download(nome_arquivo_supabase)
        return dados
    except Exception:
        return None

# =====================================================
# INTERFACES VISUAIS (VIEWS ADMINISTRATIVAS)
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
            
        prefix = ESCALAS_DISPONIVEIS[escala_selecionada_admin]
        nome_arquivo_supabase = gerar_nome_arquivo(prefix, mes_selecionado_admin, ano_selecionado_admin)
        
        arquivo_escala = st.file_uploader(f"Upload do arquivo para: {nome_arquivo_supabase}", type=["pdf"], key="uploader_admin")
        
        if st.button("Publicar Escala Oficial"):
            if not supabase:
                st.error("Banco de dados indisponível.")
            elif arquivo_escala:
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
                    if not supabase:
                        st.error("Banco de dados indisponível.")
                    elif not novo_nome or not nova_matricula:
                        st.error("Campos obrigatórios vazios.")
                    elif str(nova_matricula).strip() in df_users["login"].astype(str).str.strip().values:
                        st.error("⚠️ Matrícula já cadastrada.")
                    else:
                        try:
                            supabase.table("usuarios").insert({
                                "tipo_usuario": tipo_func,
                                "login": nova_matricula.strip(),
                                "nome": novo_nome.strip(),
                                "senha": make_hashes(senha_padrao),
                                "primeiro_acesso": 1,
                                "status": "ATIVO"
                            }).execute()
                            st.success(f"✅ {novo_nome} cadastrado!")
                            carregar_usuarios.clear()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erro ao salvar usuário no banco: {e}")
                        
        with col_lista:
            st.markdown("### 📝 Usuários Cadastrados")
            lista_usuarios = ["-- Selecione um usuário para gerenciar --"]
            for _, r in df_users.iterrows():
                lista_usuarios.append(f"ID {r['id']} | {r['nome']} ({r['login']}) - [{r['status']}]")
            usuario_selecionado = st.selectbox("Buscar/Editar Usuário", lista_usuarios)
            
            if usuario_selecionado != "-- Selecione um usuário para gerenciar --":
                id_selecionado = int(usuario_selecionado.split("ID ")[1].split(" |")[0])
                dados_user = df_users[df_users["id"] == id_selecionado].iloc[0]
                
                with st.form("form_edicao_usuario"):
                    edit_nome = st.text_input("Alterar Nome Funcional", value=str(dados_user['nome'])).strip().upper()
                    edit_login = st.text_input("Alterar Matrícula / Login", value=str(dados_user['login'])).strip()
                    edit_tipo = st.selectbox("Alterar Perfil", ["agente", "admin"], index=0 if dados_user['tipo_usuario'] == "agente" else 1)
                    edit_status = st.selectbox("Status da Conta", ["ATIVO", "INATIVO"], index=0 if str(dados_user['status']).upper() == "ATIVO" else 1)
                    col_btn1, col_btn2 = st.columns(2)
                    with col_btn1: salvar_edicao = st.form_submit_button("💾 Salvar Alterações")
                    with col_btn2: forcar_reset = st.form_submit_button("🔄 Redefinir Senha (1234)")
                
                if salvar_edicao and supabase:
                    try:
                        supabase.table("usuarios").update({
                            "nome": edit_nome.strip(),
                            "login": edit_login.strip(),
                            "tipo_usuario": edit_tipo,
                            "status": edit_status
                        }).eq("id", id_selecionado).execute()
                        st.success("Dados alterados com sucesso!")
                        carregar_usuarios.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao atualizar dados: {e}")
                        
                if forcar_reset and supabase:
                    try:
                        supabase.table("usuarios").update({
                            "senha": make_hashes("1234"),
                            "primeiro_acesso": 1
                        }).eq("id", id_selecionado).execute()
                        st.success("Senha resetada para '1234' com sucesso!")
                        carregar_usuarios.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao resetar senha: {e}")

# =====================================================
# INTERFACE DO AGENTE
# =====================================================
def view_visualizar_escala_usuario():
    st.subheader("📥 Central de Downloads - Escalas de Serviço")
    st.info("Selecione o mês e o ano abaixo para listar as escalas disponíveis para download.")
    
    matricula = st.session_state.get("login_usuario", "SEM_MATRICULA").upper()
    col_mes, col_ano = st.columns(2)
    with col_mes:
        mes_desejado = st.selectbox("Filtrar por Mês:", MESES)
    with col_ano:
        ano_desejado = st.selectbox("Filtrar por Ano:", ANOS, index=1)
        
    st.markdown("---")
    
    for nome_exibicao, prefixo in ESCALAS_DISPONIVEIS.items():
        nome_arquivo_target = gerar_nome_arquivo(prefixo, mes_desejado, ano_desejado)
        
        st.markdown(f"""
        <div class="escala-container">
            <div class="escala-titulo">📋 Escala do {nome_exibicao}</div>
            <div class="escala-detalhe">Período de Referência: {mes_desejado} de {ano_desejado} | Ficheiro: {nome_arquivo_target}</div>
        </div>
        """, unsafe_allow_html=True)
        
        file_key = f"btn_{prefixo}_{mes_desejado}_{ano_desejado}"
        pdf_original = baixar_escala_original(nome_arquivo_target)
        
        if pdf_original:
            pdf_com_marca = aplicar_marca_dagua(pdf_original, matricula)
            st.download_button(
                label=f"📥 Baixar Escala do {nome_exibicao} ({matricula})",
                data=pdf_com_marca,
                file_name=f"{nome_arquivo_target.replace('.pdf', '')}_{matricula}.pdf",
                mime="application/pdf",
                key=file_key,
                on_click=registrar_log,
                args=(st.session_state["nome_usuario"], "DOWNLOAD_ESCALA", f"{nome_arquivo_target} | Matrícula: {matricula}")
            )
        else:
            st.button(f"❌ Escala do {nome_exibicao} Não Publicada", key=file_key, disabled=True)
        st.markdown("<br>", unsafe_allow_html=True)

# =====================================================
# RENDERIZAÇÃO E CONTROLE DE TELAS
# =====================================================
def renderizar_tela_login():
    st.sidebar.title("🔐 Acesso Restrito")
    tipo = st.sidebar.selectbox("Função de Acesso", ["agente", "admin"])
    login = st.sidebar.text_input("Matrícula").strip()
    senha = st.sidebar.text_input("Senha Corporativa", type="password")
    
    if st.sidebar.button("Entrar no Sistema"):
        if not supabase:
            st.sidebar.error("Impossível autenticar. Conexão com banco offline.")
            return
            
        with st.sidebar.spinner("Autenticando..."):
            res = login_usuario_supabase(tipo, login, senha)
            
            if res["sucesso"]:
                st.session_state["logado"] = True
                st.session_state["usuario_id"] = res["id"]
                st.session_state["tipo_usuario"] = tipo
                st.session_state["nome_usuario"] = res["nome"]
                st.session_state["login_usuario"] = res["login"]
                st.session_state["primeiro_acesso"] = res["primeiro_acesso"]
                st.success(f"Autenticado: {res['nome']}")
                time.sleep(0.3)
                st.rerun()
            else:
                # EXIBE O ERRO EXATO DETECTADO NO PYTHON
                st.sidebar.error(f"Senha ou usuário incorreto")

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
            if alterar_senha_usuario_supabase(st.session_state["usuario_id"], nova_senha):
                st.success("Senha alterada com sucesso!")
                st.session_state["primeiro_acesso"] = False
                time.sleep(0.3)
                st.rerun()

# Fluxo de Execução Principal
if not supabase:
    st.error("🛑 Erro de Conexão: Não foi possível estabelecer conexão com o banco de dados Supabase. Verifique suas configurações no painel 'Secrets' do Streamlit Cloud.")
else:
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
                df_logs = carregar_logs()
                if not df_logs.empty:
                    st.dataframe(df_logs[["data", "hora", "usuario", "acao", "detalhes"]], use_container_width=True)
                else:
                    st.info("Nenhum log registrado.")
        else:
            view_visualizar_escala_usuario()
            
        if st.sidebar.button("Desconectar / Sair"):
            logout()