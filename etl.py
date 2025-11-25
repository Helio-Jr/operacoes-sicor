import pandas as pd
import time
import os

# ==========================================================
# Função Auxiliar – Leitura com logging + salvamento
# ==========================================================

def read_table(url, sep, usecols, savepath=None, compression=None, encoding='utf-8'):
    # 1. SE O PARQUET JÁ EXISTE → NÃO LÊ, NÃO BAIXA, NÃO CARREGA
    if savepath and os.path.exists(savepath):
        print(f"\n📁 Arquivo já existe em disco: {savepath}")
        print("↪ Pulando totalmente (não lendo csv, não baixando).")
        return pd.DataFrame(columns=usecols)

    print(f"\n🔽 Iniciando download: {url}")

    try:
        df = pd.read_csv(url, sep=sep, compression=compression, encoding=encoding, dtype=str)

        df.columns = df.columns.str.lstrip("#")

        if url=="https://www.bcb.gov.br/htms/sicor/SituacaoOperacao.csv":
            df['DESCRICAO'] = df["CODIGO"].apply(lambda x: x[3:-1].replace('"', ""))
            df['CODIGO'] = df["CODIGO"].apply(lambda x: x[0:2].replace(",", ""))

        df = df.replace(r"(\d+)\.(\d+)", r"\1,\2", regex=True)

        cols = [c for c in usecols if c in df.columns]
        missing = set(usecols) - set(cols)
        if missing:
            print(f"⚠ Colunas ausentes: {missing}")

        df = df[cols]

        print(f"✅ Tabela carregada: {df.shape[0]} linhas, {df.shape[1]} colunas")

        # Somente salva parquet se configurado
        if savepath:
            os.makedirs(os.path.dirname(savepath), exist_ok=True)
            df.to_parquet(savepath, index=False)
            print(f"💾 Arquivo salvo em: {savepath}")

        return df

    except Exception as e:
        print(f"❌ ERRO ao carregar {url}")
        print("   ->", e)
        return pd.DataFrame(columns=usecols)


def fix_cols(df):
    cols_vl = [c for c in df.columns if c.startswith("VL_")]

    for col in cols_vl:
        df[col] = (
            df[col]
            .astype(str)
            .str.replace(".", "", regex=False)   # remove separador milhar
            .str.replace(",", ".", regex=False)  # troca vírgula por ponto
        )

        # tenta converter para número
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df

# ==========================================================
# Colunas desejadas
# ==========================================================
cols_operacao = [
    "REF_BACEN","NU_ORDEM","CNPJ_IF","DT_EMISSAO","DT_VENCIMENTO",
    "CD_FONTE_RECURSO","CNPJ_AGENTE_INVEST","CD_ESTADO","CD_EMPREENDIMENTO",
    "VL_RECEITA_BRUTA_ESPERADA","VL_PARC_CREDITO","VL_AREA_FINANC",
    "DT_FIM_COLHEITA","DT_FIM_PLANTIO","DT_INIC_COLHEITA","DT_INIC_PLANTIO",
    "VL_AREA_INFORMADA"
]

cols_saldos = [
    "REF_BACEN","NU_ORDEM","ANO_BASE","MES_BASE",
    "VL_MEDIO_DIARIO_VINCENDO","VL_ULTIMO_DIA",
    "VL_MEDIO_DIARIO","CD_SITUACAO_OPERACAO"
]

cols_fonte = ["CODIGO","DESCRICAO","DATA_INICIO","DATA_FIM"]
cols_empre = ["CODIGO","DATA_INICIO","DATA_FIM",
              "FINALIDADE","ATIVIDADE","MODALIDADE","PRODUTO"]
cols_situacao = ["CODIGO","DESCRICAO"]
cols_ifs = ["CNPJ_IF","NOME_IF","SEGMENTO_IF"]

# ==========================================================
# Download anual (2024)
# ==========================================================
inicio_total = time.time()
ano = 2024

print("\n============================")
print("📌 INÍCIO DO PROCESSO ETL")
print("============================")

print(f"\n=== ANO {ano} ===")

url_op = f"https://www.bcb.gov.br/htms/sicor/DadosBrutos/SICOR_OPERACAO_BASICA_ESTADO_{ano}.gz"
url_sd = f"https://www.bcb.gov.br/htms/sicor/DadosBrutos/SICOR_SALDOS_{ano}.gz"

# Agora só existe parquet da tabela final.
operacao_final = read_table(
    url_op, sep=";", usecols=cols_operacao,
    savepath=None,  # Não salva ainda (só após join)
    compression="gzip"
)

# Tabela de saldos é carregada mas NÃO É SALVA
saldos_final = read_table(
    url_sd, sep=";", usecols=cols_saldos,
    savepath=None,
    compression="gzip"
)

operacao_final = fix_cols(operacao_final)
saldos_final   = fix_cols(saldos_final)

# ==========================================================
# ENRIQUECIMENTO: Juntar Operações + Agregações de Saldos
# ==========================================================

print("\n============================")
print("🔗 CRIANDO CHAVE E AGREGANDO SALDOS")
print("============================")

operacao_final["CODIGO_UNICO"] = operacao_final["REF_BACEN"].astype(str) + operacao_final["NU_ORDEM"].astype(str)
saldos_final["CODIGO_UNICO"]   = saldos_final["REF_BACEN"].astype(str) + saldos_final["NU_ORDEM"].astype(str)

saldos_final["CD_SITUACAO_OPERACAO"] = pd.to_numeric(saldos_final["CD_SITUACAO_OPERACAO"], errors="coerce")
saldos_final["VL_MEDIO_DIARIO"] = pd.to_numeric(saldos_final["VL_MEDIO_DIARIO"], errors="coerce")

agg_saldos = (
    saldos_final
    .assign(
        FLAG_INAD = lambda df: (df["CD_SITUACAO_OPERACAO"] == 12).astype(int),
        VALOR_INAD = lambda df: df["VL_MEDIO_DIARIO"].where(df["CD_SITUACAO_OPERACAO"] == 12, 0)
    )
    .groupby("CODIGO_UNICO")
    .agg(
        MESES_INADIMPLENTES = ("FLAG_INAD", "sum"),
        INADIMPLENCIA       = ("FLAG_INAD", "max"),
        VALOR_INADIMPLENTE  = ("VALOR_INAD", "sum")
    )
    .reset_index()
)

print(f"🔎 Agregação criada com {agg_saldos.shape[0]} operações.")

operacao_final = operacao_final.merge(agg_saldos, on="CODIGO_UNICO", how="left")

operacao_final["MESES_INADIMPLENTES"] = operacao_final["MESES_INADIMPLENTES"].fillna(0).astype(int)
operacao_final["INADIMPLENCIA"] = operacao_final["INADIMPLENCIA"].fillna(0).astype(int)
operacao_final["VALOR_INADIMPLENTE"] = operacao_final["VALOR_INADIMPLENTE"].fillna(0)

# ==========================================================
# SALVAR APENAS A TABELA FINAL PRONTA PARA POWER BI
# ==========================================================
output_path = f"dados/F_operacao_saldos_{ano}.parquet"
os.makedirs("dados", exist_ok=True)
operacao_final.to_parquet(output_path, index=False)

print(f"\n💾 Arquivo final salvo em: {output_path}")

# ==========================================================
# Tabelas de domínio
# ==========================================================
print("\n============================")
print("📌 CARREGANDO TABELAS DE DOMÍNIO")
print("============================")

dom_fonte = read_table(
    "https://www.bcb.gov.br/htms/sicor/FonteRecursos.csv",
    ",", cols_fonte,
    savepath="dados/dim_fontes_recursos.parquet", encoding="latin1"
)

dom_empre = read_table(
    "https://www.bcb.gov.br/htms/sicor/Empreendimento.csv",
    ",", cols_empre,
    savepath="dados/dim_empreendimento.parquet", encoding="latin1"
)

dom_ifs = read_table(
    "https://www.bcb.gov.br/htms/sicor/DadosBrutos/SICOR_LISTA_IFS.csv",
    ";", cols_ifs,
    savepath="dados/dim_ifs_sicor.parquet", encoding="latin1"
)

# ==========================================================
# LOG FINAL
# ==========================================================
print("\n============================")
print("📊 RESUMO FINAL DO ETL")
print("============================\n")

print(f"Operações Enriquecidas → {operacao_final.shape}")
print(f"Fonte Recursos         → {dom_fonte.shape}")
print(f"Empreendimento         → {dom_empre.shape}")
print(f"Instituições           → {dom_ifs.shape}")

fim_total = time.time()
print(f"\n⏱ Tempo total do processo: {round(fim_total - inicio_total, 2)} segundos")
print("🚀 ETL concluído com sucesso!")
