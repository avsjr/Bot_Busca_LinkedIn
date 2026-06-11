import requests
import pandas as pd
import time
import re
import json
import os
from datetime import datetime
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# ==============================================================================
# 1. CONFIGURAÇÕES E CHAVES (VIA GITHUB SECRETS/ENVIRONMENTS)
# ==============================================================================
print("⏳ Iniciando o Motor de Vagas de Dados...")

META_TOTAL_GLOBAL = 400
META_POR_TERMO = 80

TERMOS_BUSCA = [
    "Dados", "Data", "Analytics", "Inteligência de Negócio",
    "Cientista de Dados", "Business Intelligence", "Power BI", "Fabric"
]

try:
    # Lendo chaves de ambiente (Injetadas pelo GitHub Actions)
    RAPIDAPI_KEY = os.environ.get('RAPIDAPI_KEY')
    GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')

    if not all([RAPIDAPI_KEY, GOOGLE_API_KEY]):
        raise ValueError("Chaves secretas das APIs não encontradas no ambiente.")
except Exception as e:
    print(f"❌ ERRO nas Chaves: {e}")
    exit(1)

headers_rapid = {
    "x-rapidapi-host": "linkedin-job-search5.p.rapidapi.com", 
    "x-rapidapi-key": RAPIDAPI_KEY
}

client_genai = genai.Client(api_key=GOOGLE_API_KEY)

# ==============================================================================
# 2. SCHEMA DA IA
# ==============================================================================
class DadosMercadoData(BaseModel):
    localidade: str = Field(description="Cidade ou Estado da vaga.")
    modelo_trabalho: str = Field(description="Classifique como: 'Presencial', 'Híbrido', 'Remoto' ou 'Não Informado'.")
    categoria_hierarquica: str = Field(description="Ex: 'Especialista/Sênior', 'Pleno/Técnico', 'Júnior/Operacional'.")
    ferramentas_exigidas: list[str] = Field(description="Ex: 'Python', 'SQL', 'Power BI', 'Fabric', etc.")
    salario_na_descricao: str | None = Field(description="Valor explícito se houver.")
    salario_estimado_ia: str = Field(description="Estimativa de mercado.")
    hard_skills: list[str] = Field(description="Competências técnicas.")
    soft_skills: list[str] = Field(description="Competências comportamentais.")

# ==============================================================================
# 3. ETAPA A: EXTRAÇÃO (API LINKEDIN NOVA)
# ==============================================================================
vagas_unicas = {}
termos_pesquisados = []
print(f"\n🔎 ETAPA A: Varredura no LinkedIn (Meta: {META_TOTAL_GLOBAL} vagas)...")

for termo in TERMOS_BUSCA:
    if len(vagas_unicas) >= META_TOTAL_GLOBAL: break
    termos_pesquisados.append(termo)
    print(f"   Buscando: '{termo}'... ", end="")

    start_offset = 0 # API nova usa paginação numérica
    count_termo = 0

    while count_termo < META_POR_TERMO:
        try:
            params = {
                "keywords": termo, 
                "location": "Brazil", 
                "datePosted": "past-24h", # ATUALIZADO
                "remote": "remote",       # ATUALIZADO
                "start": start_offset     # ATUALIZADO
            }

            time.sleep(1.5)
            # URL ATUALIZADA
            resp = requests.get("https://linkedin-job-search5.p.rapidapi.com/search", headers=headers_rapid, params=params).json()

            # ATUALIZADO: A API nova devolve as vagas na chave 'jobs' e não em 'data'
            if 'jobs' not in resp or not resp['jobs']: break
            
            for v in resp['jobs']:
                if v['id'] not in vagas_unicas: vagas_unicas[v['id']] = v

            count_termo += len(resp['jobs'])
            if len(vagas_unicas) >= META_TOTAL_GLOBAL: break
            
            start_offset += 10 # Paginação da API nova pula de 10 em 10
        except Exception as e:
            # print(f"Erro na busca: {e}") # Descomente se precisar debugar
            break

    print(f"[{count_termo} encontradas brutas]")

ids_vagas = list(vagas_unicas.values())[:META_TOTAL_GLOBAL]
print(f"✅ IDs encontrados. Baixando detalhes de {len(ids_vagas)} vagas...")

dados_brutos = []
for i, vaga in enumerate(ids_vagas):
    print(f"\r   Baixando [{i+1}/{len(ids_vagas)}]...", end="")
    try:
        time.sleep(1.2)
        # URL ATUALIZADA para pegar detalhes da vaga
        r = requests.get(f"https://linkedin-job-search5.p.rapidapi.com/job/{vaga.get('id')}", headers=headers_rapid)
        if r.status_code == 200:
            detalhe = r.json().get('job', {}) # API nova devolve o detalhe em 'job'
            desc = detalhe.get('description', '')
            
            if desc and len(str(desc)) > 50:
                dados_brutos.append({
                    "id": vaga.get('id'), 
                    "titulo": vaga.get('title'), 
                    "empresa": detalhe.get('company'), # ATUALIZADO
                    "localidade_api": detalhe.get('location', 'Não Informado'), 
                    "data_publicacao": detalhe.get('postedDate') or detalhe.get('postedDateText'), # ATUALIZADO
                    "link": detalhe.get('jobUrl'), # ATUALIZADO
                    "descricao_completa": desc
                })
    except: pass

df_bruto = pd.DataFrame(dados_brutos)

# ==============================================================================
# 4. ETAPA B: TRANSFORMAÇÃO E FILTRO (PYTHON)
# ==============================================================================
df_aprovado = pd.DataFrame()
df_reprovado = pd.DataFrame()

if not df_bruto.empty:
    print(f"\n\n🧹 ETAPA B: Filtrando vagas válidas (Título e Tempo)...")
    
    termos_aceitos = ['data', 'dados', 'analytics', 'bi', 'business intelligence', 'data analyst', 'powerbi', 'power bi', 'data specialist', 'inteligência de negócio', 'engenheiro de dados', 'cientista de dados', 'data engineer', 'data scientist']
    mask_titulo = df_bruto['titulo'].str.contains('|'.join(termos_aceitos), case=False, na=False)

    termos_velhos = ['semana', 'mês', 'meses', 'ano', 'week', 'month', 'year', '2 dias', '3 dias', '4 dias', '5 dias', '6 dias', '2 days', '3 days', '4 days', '5 days', '6 days']
    mask_tempo = ~df_bruto['data_publicacao'].astype(str).str.contains('|'.join(termos_velhos), case=False, na=False)

    mask_final = mask_titulo & mask_tempo
    df_aprovado = df_bruto[mask_final].copy()
    df_reprovado = df_bruto[~mask_final].copy()

    print(f"✅ Vagas aprovadas (Boas e Recentes): {len(df_aprovado)} | 🗑️ Descartadas: {len(df_reprovado)}")

# ==============================================================================
# 5. ETAPA C: ENRIQUECIMENTO COM IA (GEMINI)
# ==============================================================================
if not df_aprovado.empty:
    print(f"\n🧠 ETAPA C: Processando IA do Google Gemini nas aprovadas...")
    dados_processados = []
    lista_aprovadas = df_aprovado.to_dict('records')

    for i, row in enumerate(lista_aprovadas):
        print(f"\r   Analisando vaga [{i+1}/{len(lista_aprovadas)}] | IA Sucessos: {len(dados_processados)}...", end="")
        try:
            prompt = f"Analise vaga de Dados. Local: '{row.get('localidade_api', '')}'. Extraia as ferramentas (Stack). Identifique se é Presencial, Híbrido ou Remoto. Salário: Estime. Vaga: {row.get('descricao_completa', '')}"
            response = client_genai.models.generate_content(
                model='gemini-2.0-flash', contents=prompt,
                config=types.GenerateContentConfig(response_mime_type='application/json', response_schema=DadosMercadoData)
            )
            insights = response.parsed.model_dump()

            for k in ['ferramentas_exigidas', 'hard_skills', 'soft_skills']:
                if isinstance(insights.get(k), list): insights[k] = ", ".join(insights[k])

            registro = row.copy()
            registro.update(insights)
            if 'descricao_completa' in registro: del registro['descricao_completa']
            dados_processados.append(registro)
            
            time.sleep(4.5)
        except Exception as e:
            time.sleep(10)
            pass

# ==============================================================================
# 6. ETAPA D: COMPILAÇÃO DO RELATÓRIO HTML E ENVIO POR E-MAIL
# ==============================================================================
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

if 'dados_processados' in locals() and dados_processados:
    print("\n\n⚙️ ETAPA D: Ajustes finais e Preparação do E-mail...")
    df_ia = pd.DataFrame(dados_processados)

    # Filtrando apenas as vagas realmente remotas encontradas pela IA
    mask_remoto = df_ia['modelo_trabalho'].str.upper() == 'REMOTO'
    df_final = df_ia[mask_remoto].copy()
    df_falsos_remotos = df_ia[~mask_remoto].copy()

    print(f"🕵️ Auditoria da IA: {len(df_final)} remotas reais | {len(df_falsos_remotos)} falsas remotas barradas.")

    if not df_final.empty:
        # Tratamento de regras de negócio para exibição do e-mail
        df_final['salario_medio'] = df_final['salario_estimado_ia'].apply(lambda x: sum([v * 1000 if v < 100 else v for v in [float(n) for n in re.findall(r'\d+', str(x).replace('.', ''))]]) / len([float(n) for n in re.findall(r'\d+', str(x).replace('.', ''))]) if pd.notna(x) and re.findall(r'\d+', str(x).replace('.', '')) else "Não Estimado")
        df_final['categoria_tratada'] = df_final['categoria_hierarquica'].replace({'Técnico/Pl-Sr': 'Técnico/Especialista', 'Analista': 'Técnico/Especialista', 'Operacional/Jr': 'Júnior/Entrada', 'Assistente': 'Júnior/Entrada', 'Gestão': 'Gestão/Liderança', 'Executivo': 'Diretoria/C-Level'})
        
        hoje = datetime.now().strftime("%d/%m/%Y")
        
        # 1. CONSTRUÇÃO DO CORPO DO E-MAIL EM HTML
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; color: #333; }}
                h2 {{ color: #1f4e79; border-bottom: 2px solid #1f4e79; padding-bottom: 5px; }}
                .vaga-card {{ background-color: #f9f9f9; border-left: 5px solid #2088FF; padding: 15px; margin-bottom: 15px; border-radius: 4px; }}
                .titulo {{ font-size: 18px; font-weight: bold; color: #111; }}
                .empresa {{ font-size: 14px; color: #555; font-style: italic; margin-bottom: 8px; }}
                .tag {{ display: inline-block; background-color: #e2eefd; color: #2088FF; padding: 3px 8px; font-size: 12px; border-radius: 3px; font-weight: bold; margin-right: 5px; }}
                .tag-salario {{ background-color: #e6f4ea; color: #137333; }}
                .skills {{ margin-top: 8px; font-size: 13px; }}
                .btn-aplicar {{ display: inline-block; margin-top: 10px; background-color: #2088FF; color: white; padding: 8px 15px; text-decoration: none; border-radius: 4px; font-weight: bold; font-size: 13px; }}
            </style>
        </head>
        <body>
            <h2>🤖 Radar de Vagas de Dados Remotas - {hoje}</h2>
            <p>Foram encontradas <strong>{len(df_final)} oportunidades reais</strong> nas últimas 24 horas após auditoria por IA.</p>
        """

        # Loop para injetar cada vaga estruturada como um bloco visual no e-mail
        for _, row in df_final.iterrows():
            html_content += f"""
            <div class="vaga-card">
                <div class="titulo">{row.get('titulo')}</div>
                <div class="empresa">{row.get('empresa')} - {row.get('localidade_api')}</div>
                <div>
                    <span class="tag">{row.get('categoria_tratada')}</span>
                    <span class="tag tag-salario">Est. Salário: {row.get('salario_estimado_ia')}</span>
                </div>
                <div class="skills">
                    <strong>Stack/Ferramentas:</strong> {row.get('ferramentas_exigidas')}<br>
                    <strong>Hard Skills:</strong> {row.get('hard_skills')}
                </div>
                <a href="{row.get('link')}" class="btn-aplicar" target="_blank">Visualizar Vaga no LinkedIn ↗</a>
            </div>
            """
        
        html_content += """
        </body>
        </html>
        """

        # 2. CONFIGURAÇÃO E ENVIO VIA SMTP (Requer chaves no ambiente)
        GMAIL_USER = os.environ.get('GMAIL_USER')
        GMAIL_PASSWORD = os.environ.get('GMAIL_PASSWORD')
        SMTP_SERVER = 'smtp.gmail.com'
        SMTP_PORT = 587

        if all([GMAIL_USER, GMAIL_PASSWORD]):
            try:
                msg = MIMEMultipart('alternative')
                msg['Subject'] = f"🤖 [{hoje}] Suas Vagas de Dados Curadas por IA"
                msg['From'] = GMAIL_USER
                msg['To'] = GMAIL_USER # Envia para você mesmo

                msg.attach(MIMEText(html_content, 'html'))

                print("📧 Conectando ao servidor do Gmail...")
                server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
                server.starttls()
                server.login(GMAIL_USER, GMAIL_PASSWORD)
                server.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
                server.quit()
                print(f"✅ SUCESSO! Relatório diário enviado para {GMAIL_USER}")
            except Exception as mail_err:
                print(f"❌ Falha ao disparar o e-mail: {mail_err}")
        else:
            print("⚠️ Chaves do Gmail ausentes no GitHub.")
            print(f"Relatório pronto com {len(df_final)} vagas, mas não enviado.")
