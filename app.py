import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

st.set_page_config(page_title="Relatório de Notas no SIGO", page_icon="📄", layout="wide")

st.title("📄 Conversor de Documentos SIGO - NF/ESTOQUE")

def parse_valor(v):
    if not v: return 0.0
    try:
        v = str(v).strip().replace('R$', '').replace(' ', '')
        if '.' in v and ',' in v:
            v = v.replace('.', '').replace(',', '.')
        elif ',' in v:
            v = v.replace(',', '.')
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
                    if any(x in linha for x in ["Sigo-Sistema", "CONSTRUBASE", "Pag.", "NF/Estoque"]):
                        continue
                    linha = re.sub(r'\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}', '', linha)
                    linhas_limpas.append(linha)
                texto_completo += "\n".join(linhas_limpas) + "\n"

    padrao_nota = re.compile(r'(\d{2}/\d{2}/\d{4})\s+(NFS|NFE|NFF|NF|Nf|Nf-|OUT)\s*[- ]*(\d+)', re.IGNORECASE)
    matches = list(padrao_nota.finditer(texto_completo))
    
    if not matches:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

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
        
        primeira_linha = linhas[0]
        valores_linha = re.findall(r'[\d\.,]+', primeira_linha)
        valor_total_nf = parse_valor(valores_linha[-1]) if valores_linha else 0.0

        match_oc = re.search(r'-Oc\s*(\d+)', primeira_linha, re.IGNORECASE)
        num_oc = match_oc.group(1) if match_oc else "Sem Oc"

        fornecedor_raw = primeira_linha.split(num_doc)[-1].replace(valores_linha[-1], "").strip()
        fornecedor = re.sub(r'\d+\s*-Oc\s*\d*', '', fornecedor_raw).strip()
        fornecedor = re.sub(r'^-', '', fornecedor).strip()

        apropriacao = ""
        m_aprop = re.search(r'^(.*?)\s*-\s*Operador', bloco, re.MULTILINE)
        if m_aprop:
            apropriacao = m_aprop.group(1).split('\n')[-1].strip()

        observacao = ""
        m_obs = re.search(r'Observação\s*[:\-\s]*(.*?)(?=\s{2,}|\d{2}/\d{2}/\d{4}|$)', bloco, re.IGNORECASE)
        if m_obs:
            observacao = m_obs.group(1).strip()
            observacao = re.sub(r'\d{1,3}(\.\d{3})*,\d{2}$', '', observacao).strip()

        partes_fin = bloco.split("Dt.Ent")
        if len(partes_fin) > 1:
            corpo_parcelas = partes_fin[1]
            corpo_parcelas = re.sub(r'^\s*\d{2}/\d{2}/\d{4}', '', corpo_parcelas)
            matches_venc = re.findall(r'(\d{2}/\d{2}/\d{4})\s+([\d\.,]+)', corpo_parcelas)
        else:
            matches_venc = []       

        if not matches_venc:
            matches_venc = [(data_emi, str(valor_total_nf))]

        for dt_v, v_p in matches_venc:
            v_p_clean = parse_valor(v_p)
            if v_p_clean == parse_valor(num_doc) and v_p_clean < 5000:
                continue
                
            dados_finais.append({
                "Documento": doc_full,
                "n° O.c": num_oc,
                "Data Emissão": data_emi,
                "Fornecedor": fornecedor,
                "Apropriação": apropriacao,
                "Observação": observacao,
                "Valor Total NF": valor_total_nf,
                "Vencimento": dt_v,
                "Valor Parcela": v_p_clean
            })

    df_bruto_final = pd.DataFrame(dados_finais)
    df_bruto_final['obs_len'] = df_bruto_final['Observação'].str.len()
    df_bruto_final = df_bruto_final.sort_values(by=['Documento', 'Vencimento', 'obs_len'], ascending=[True, True, False])
    
    colunas_chave = ['Documento', 'Vencimento', 'Valor Parcela']
    df_limpo = df_bruto_final.drop_duplicates(subset=colunas_chave, keep='first').drop(columns=['obs_len'])
    df_dups = df_bruto_final[df_bruto_final.duplicated(subset=colunas_chave, keep='first')].drop(columns=['obs_len'])
    df_geral = df_bruto_final.drop(columns=['obs_len'])

    audit = df_limpo.groupby(['Documento', 'Valor Total NF']).agg({'Valor Parcela': 'sum'}).reset_index()
    audit['Valor Parcela'] = audit['Valor Parcela'].round(2)
    audit['Diferença'] = (audit['Valor Total NF'] - audit['Valor Parcela']).round(2)
    audit['Status'] = audit['Diferença'].apply(lambda x: '✅ OK' if abs(x) < 0.1 else '❌ ERRO SOMA')

    return df_limpo, audit, df_dups, df_geral

# --- Interface ---
arquivo = st.file_uploader("Suba o PDF do sistema aqui", type="pdf")

if arquivo:
    df, audit, dups, geral = processar_pdf(arquivo)
    
    if not df.empty:
        soma_limpo = df['Valor Parcela'].sum()
        soma_dups = dups['Valor Parcela'].sum()
        soma_geral = soma_limpo + soma_dups

        st.success("✅ Processamento concluído!")
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Soma Parcelas (SemDuplica)", f"R$ {soma_limpo:,.2f}")
        col2.metric("Soma Duplicados", f"R$ {soma_dups:,.2f}")
        col3.metric("Soma Total Geral", f"R$ {soma_geral:,.2f}")

        t1, t2, t3, t4 = st.tabs(["📊 Sem Duplicadas", "📚 Geral (Tudo)", "🔍 Auditoria", "⚠️ Duplicados"])
        with t1: st.dataframe(df, use_container_width=True)
        with t2: st.dataframe(geral, use_container_width=True)
        with t3: st.dataframe(audit, use_container_width=True)
        with t4: st.dataframe(dups, use_container_width=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Bd_SemDuplica', index=False)
            geral.to_excel(writer, sheet_name='Bd_NotasGeral', index=False)
            audit.to_excel(writer, sheet_name='Auditoria', index=False)
            dups.to_excel(writer, sheet_name='Duplicados', index=False)
        st.download_button("📥 Baixar Relatório SIGO Completo", buffer.getvalue(), "relatorio_SIGO_final.xlsx")
