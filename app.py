import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

st.set_page_config(page_title="Relatório de Notas no SIGO", layout="wide")

st.title("📄 Conversor de Documentos SIGO - NF/ESTOQUE")

def parse_valor(v):
    if not v: return 0.0
    try:
        v = str(v).strip().replace('R$', '').replace(' ', '')
        if '.' in v and ',' in v:
            v = v.replace('.', '').replace(',', '.')
        elif ',' in v:
            v = v.replace(',', '.')
        # Remove qualquer caractere que não seja número ou ponto
        res = re.sub(r'[^\d.]', '', v)
        return round(float(res), 2)
    except:
        return 0.0

def processar_pdf(file):
    texto_completo = ""
    
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            raw_text = page.extract_text()
            if raw_text:
                linhas_limpas = []
                for linha in raw_text.split('\n'):
                    # LIMPEZA CRÍTICA: Remove rodapés e headers que sujam os dados
                    if any(x in linha for x in ["Sigo-Sistema", "CONSTRUBASE", "Pag.", "NF/Estoque"]):
                        continue
                    # Remove data/hora de extração (ex: 19/03/2026 13:36:06)
                    linha = re.sub(r'\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}', '', linha)
                    linhas_limpas.append(linha)
                texto_completo += "\n".join(linhas_limpas) + "\n"

    # Regex para identificar o início de cada nota (Data + Tipo + Número)
    padrao_nota = re.compile(r'(\d{2}/\d{2}/\d{4})\s+(NFS|NFE|NFF|NF|Nf|Nf-|OUT)\s*[- ]*(\d+)', re.IGNORECASE)
    matches = list(padrao_nota.finditer(texto_completo))
    
    if not matches:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    dados_finais = []
    
    for i in range(len(matches)):
        start = matches[i].start()
        end = matches[i+1].start() if i+1 < len(matches) else len(texto_completo)
        bloco = texto_completo[start:end]
        
        data_emi = matches[i].group(1)
        tipo_doc = matches[i].group(2)
        num_doc = matches[i].group(3)
        doc_full = f"{tipo_doc}-{num_doc}"
        
        linhas = [l.strip() for l in bloco.split('\n') if l.strip()]
        if not linhas: continue
        
        # --- CAPTURA DO CABEÇALHO (FORNECEDOR E VALOR TOTAL) ---
        primeira_linha = linhas[0]
        # O valor total da nota é SEMPRE o último elemento numérico da primeira linha
        valores_linha = re.findall(r'[\d\.,]+', primeira_linha)
        valor_total_nf = parse_valor(valores_linha[-1]) if valores_linha else 0.0

        # Fornecedor: Tudo que está entre o número da nota e o valor total
        # Removendo também padrões de OC (ex: 35165 -Oc 55415)
        fornecedor_raw = primeira_linha.split(num_doc)[-1].replace(valores_linha[-1], "").strip()
        fornecedor = re.sub(r'\d+\s*-Oc\s*\d*', '', fornecedor_raw).strip()
        fornecedor = re.sub(r'^-', '', fornecedor).strip() # Remove traços residuais

        # --- CAPTURA DE APROPRIAÇÃO E OBSERVAÇÃO ---
        apropriacao = ""
        m_aprop = re.search(r'^(.*?)\s*-\s*Operador', bloco, re.MULTILINE)
        if m_aprop:
            apropriacao = m_aprop.group(1).split('\n')[-1].strip()

        observacao = ""
        # Procuramos o termo 'Observação'. O (.*?) pega o texto, mas o [ ]{2,} para a leitura 
        # se encontrar 2 espaços seguidos (comum antes do valor no PDF)
        m_obs = re.search(r'Observação\s*[:\-\s]*(.*?)(?=\s{2,}|\d{2}/\d{2}/\d{4}|$)', bloco, re.IGNORECASE)
        if m_obs:
            observacao = m_obs.group(1).strip()
            # Limpeza extra: se a observação capturou um valor (ex: 450,00), nós removemos
            observacao = re.sub(r'\d{1,3}(\.\d{3})*,\d{2}$', '', observacao).strip()
        # --- AJUSTE FINO DO FINANCEIRO (PARCELAS) ---
        partes_fin = bloco.split("Dt.Ent")
        if len(partes_fin) > 1:
            # Pegamos tudo após Dt.Ent
            corpo_parcelas = partes_fin[1]
            
            # 1. Removemos a data de entrada (primeira data que aparece após Dt.Ent)
            corpo_parcelas = re.sub(r'^\s*\d{2}/\d{2}/\d{4}', '', corpo_parcelas)
            
            # 2. Buscamos apenas pares de DATA + VALOR que estejam no final das frases
            matches_venc = re.findall(r'(\d{2}/\d{2}/\d{4})\s+([\d\.,]+)', corpo_parcelas)
        else:
            matches_venc = [] 

        # REGRA DE OURO: Se não achou parcela, usa o valor total
        if not matches_venc:
            matches_venc = [(data_emi, str(valor_total_nf))]

        for dt_v, v_p in matches_venc:
            v_p_clean = parse_valor(v_p)
            # Evita que o número da nota seja lido como parcela em caso de erro de layout
            if v_p_clean == parse_valor(num_doc) and v_p_clean < 5000:
                continue
                
            dados_finais.append({
                "Documento": doc_full,
                "Data Emissão": data_emi,
                "Fornecedor": fornecedor,
                "Apropriação": apropriacao,
                "Observação": observacao,
                "Valor Total NF": valor_total_nf,
                "Vencimento": dt_v,
                "Valor Parcela": v_p_clean
            })

    df_bruto = pd.DataFrame(dados_finais)
    
    # --- TRATAMENTO DE DUPLICIDADES SÊNIOR ---
    # Remove duplicados mantendo a linha com observação mais completa
    df_bruto['obs_len'] = df_bruto['Observação'].str.len()
    df_bruto = df_bruto.sort_values(by=['Documento', 'Vencimento', 'obs_len'], ascending=[True, True, False])
    
    colunas_chave = ['Documento', 'Vencimento', 'Valor Parcela']
    df_limpo = df_bruto.drop_duplicates(subset=colunas_chave, keep='first').drop(columns=['obs_len'])
    df_dups = df_bruto[df_bruto.duplicated(subset=colunas_chave, keep='first')].drop(columns=['obs_len'])

    # --- AUDITORIA ---
    audit = df_limpo.groupby(['Documento', 'Valor Total NF']).agg({'Valor Parcela': 'sum'}).reset_index()
    audit['Valor Parcela'] = audit['Valor Parcela'].round(2)
    audit['Diferença'] = (audit['Valor Total NF'] - audit['Valor Parcela']).round(2)
    audit['Status'] = audit['Diferença'].apply(lambda x: '✅ OK' if abs(x) < 0.1 else '❌ ERRO SOMA')

    return df_limpo, audit, df_dups

# --- Interface ---
arquivo = st.file_uploader("Suba o PDF do sistema aqui", type="pdf")

if arquivo:
    df, audit, dups = processar_pdf(arquivo)
    
    if not df.empty:
        st.success(f"Processado! Soma Total das Parcelas: R$ {df['Valor Parcela'].sum():,.2f}")
        
        t1, t2, t3 = st.tabs(["📊 Banco de Dados", "🔍 Auditoria", "⚠️ Duplicados"])
        with t1: st.dataframe(df, use_container_width=True)
        with t2: st.dataframe(audit, use_container_width=True)
        with t3: st.dataframe(dups, use_container_width=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='DB_Fluxo', index=False)
            audit.to_excel(writer, sheet_name='Auditoria', index=False)
            dups.to_excel(writer, sheet_name='Duplicados', index=False)
        st.download_button("📥 Baixar Relatório", buffer.getvalue(), "relatorio_NF_Estoque.xlsx")
