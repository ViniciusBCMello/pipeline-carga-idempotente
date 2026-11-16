"""
Carga idempotente de um CSV particionado por UF/dia em SQL Server.

Padrão: CSV -> tabela temporária -> DELETE da partição -> INSERT, tudo em UMA
transação. Reexecutar o mesmo arquivo não duplica dados, e uma falha no meio
não deixa a tabela de destino pela metade.

Versão pública e higienizada: servidor, banco, tabelas, pastas e colunas são
genéricos e vêm de variáveis de ambiente (veja .env.example).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path, PureWindowsPath

import pyodbc

log = logging.getLogger("carga")

UFS_VALIDAS = {"SP", "CE", "RJ"}

# Colunas do CSV de origem, na ordem do arquivo (amostra ilustrativa).
COLUNAS = [
    "intervalo",  # texto 'dd/mm/yyyy hh:mm'
    "chamadas_ofertadas",
    "chamadas_atendidas",
    "chamadas_abandonadas",
    "nivel_servico",
    "tma_segundos",
    "agentes_logados",
    "skill",
    "operacao",
]

TABELA_DESTINO = os.getenv("TABELA_DESTINO", "dw.dbo.historico_skill")
TEMP_COLUNAS = "#stg_historico"
TEMP_LINHAS = "#stg_linhas"

# 'dd/mm/yyyy hh:mm' -> 'yyyymmdd'
DATA_SQL = (
    "SUBSTRING(intervalo,7,4)+SUBSTRING(intervalo,4,2)+SUBSTRING(intervalo,1,2)"
)

# Nomes de tabela entram no SQL por interpolação: só aceitamos identificadores.
_IDENTIFICADOR = re.compile(r"^[A-Za-z0-9_.\[\]#]+$")


class ArquivoDesatualizado(Exception):
    """O CSV existe, mas não é de hoje (a extração do dia provavelmente falhou)."""


def conectar() -> pyodbc.Connection:
    """Conexão com autocommit desligado: a transação é controlada por nós."""
    conn_str = (
        f"DRIVER={{{os.getenv('SQL_DRIVER', 'ODBC Driver 17 for SQL Server')}}};"
        f"SERVER={os.environ['SQL_SERVER']};"
        f"DATABASE={os.environ['SQL_DATABASE']};"
        "Trusted_Connection=yes;"
    )
    return pyodbc.connect(conn_str)


def _caminhos(uf: str) -> tuple[Path, str]:
    """
    Devolve (caminho visto pelo cliente, caminho visto pelo servidor SQL).

    O BULK INSERT é executado PELO servidor, então o caminho precisa existir do
    ponto de vista dele, que pode ser outro nome de compartilhamento.
    """
    nome = f"Relatorio_Historico_Skill_{uf}.csv"
    cliente = Path(os.environ["CSV_DIR_CLIENTE"]) / nome
    servidor = str(PureWindowsPath(os.environ["CSV_DIR_SERVIDOR"]) / nome)
    if "'" in servidor:
        raise ValueError("Caminho inválido para BULK INSERT.")
    return cliente, servidor


def _validar_frescor(arquivo: Path) -> None:
    if not arquivo.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {arquivo.name}")
    modificado_em = datetime.fromtimestamp(arquivo.stat().st_mtime).date()
    if modificado_em != datetime.now().date():
        raise ArquivoDesatualizado(f"Arquivo não é de hoje: {arquivo.name}")


def carregar_particao(conn: pyodbc.Connection, uf: str) -> dict:
    """Carrega o CSV da UF substituindo apenas as partições (UF + dia) do arquivo."""
    if uf not in UFS_VALIDAS:
        raise ValueError(f"UF inválida: {uf}")
    if not _IDENTIFICADOR.match(TABELA_DESTINO):
        raise ValueError("Nome de tabela de destino inválido.")

    cliente, servidor = _caminhos(uf)
    _validar_frescor(cliente)

    cur = conn.cursor()
    try:
        # --- tabelas temporárias (vivem só nesta sessão/transação) ---
        cur.execute(f"IF OBJECT_ID('tempdb..{TEMP_LINHAS}') IS NOT NULL DROP TABLE {TEMP_LINHAS}")
        cur.execute(f"CREATE TABLE {TEMP_LINHAS} (linha VARCHAR(MAX) NULL)")
        cur.execute(f"IF OBJECT_ID('tempdb..{TEMP_COLUNAS}') IS NOT NULL DROP TABLE {TEMP_COLUNAS}")
        colunas_ddl = ", ".join(f"[{c}] VARCHAR(50) NULL" for c in COLUNAS)
        cur.execute(f"CREATE TABLE {TEMP_COLUNAS} ({colunas_ddl})")

        # --- 1ª passada: linhas cruas, só para contar ---
        cur.execute(
            f"""BULK INSERT {TEMP_LINHAS} FROM '{servidor}'
                WITH (FIRSTROW = 2, ROWTERMINATOR = '0x0A', CODEPAGE = 'ACP')"""
        )
        cur.execute(f"SELECT COUNT(*) FROM {TEMP_LINHAS}")
        total = cur.fetchone()[0]
        log.info("[%s] %s linhas lidas", uf, total)
        if total == 0:
            conn.rollback()
            return {"uf": uf, "status": "ignorado", "linhas_removidas": 0, "linhas_inseridas": 0}

        # --- 2ª passada: separado por colunas, lendo exatamente as linhas contadas ---
        cur.execute(
            f"""BULK INSERT {TEMP_COLUNAS} FROM '{servidor}'
                WITH (FIRSTROW = 2, ROWTERMINATOR = '0x0A', CODEPAGE = 'ACP',
                      FIELDTERMINATOR = ';', LASTROW = {int(total)})"""
        )

        # --- diagnóstico: datas fora do formato esperado (só registra em log) ---
        cur.execute(
            f"""SELECT TOP 10 intervalo FROM {TEMP_COLUNAS}
                WHERE intervalo IS NULL
                   OR intervalo NOT LIKE '[0-3][0-9]/[0-1][0-9]/[0-9][0-9][0-9][0-9] [0-2][0-9]:[0-5][0-9]'"""
        )
        ruins = [r[0] for r in cur.fetchall()]
        if ruins:
            log.warning("[%s] intervalos fora do formato (amostra): %s", uf, ruins)

        # --- a partição é a unidade de idempotência: UF + dias presentes no arquivo ---
        cur.execute(
            f"""DELETE FROM {TABELA_DESTINO}
                WHERE uf = ?
                  AND dt_referencia IN (SELECT DISTINCT CAST({DATA_SQL} AS date)
                                        FROM {TEMP_COLUNAS})""",
            uf,
        )
        removidas = cur.rowcount

        demais = ", ".join(f"[{c}]" for c in COLUNAS[1:])
        cur.execute(
            f"""INSERT INTO {TABELA_DESTINO}
                SELECT CAST({DATA_SQL} + RIGHT(intervalo, 6) AS datetime) AS intervalo,
                       CAST(RIGHT(intervalo, 5) AS time(0))               AS hora,
                       {demais},
                       CAST({DATA_SQL} AS date)                            AS dt_referencia,
                       ?                                                   AS uf
                FROM {TEMP_COLUNAS}""",
            uf,
        )
        inseridas = cur.rowcount

        conn.commit()  # DELETE + INSERT viram efetivos juntos, ou nenhum dos dois
        log.info("[%s] concluída: %s removidas, %s inseridas", uf, removidas, inseridas)
        return {"uf": uf, "status": "sucesso", "linhas_removidas": removidas, "linhas_inseridas": inseridas}

    except Exception:
        conn.rollback()
        raise


def processar(ufs: list[str]) -> list[dict]:
    """Uma transação por UF: a falha de uma não impede nem desfaz as demais."""
    resultados = []
    conn = conectar()
    try:
        for uf in ufs:
            try:
                resultados.append(carregar_particao(conn, uf))
            except Exception as erro:  # noqa: BLE001 - registramos e seguimos
                log.error("[%s] erro na carga (rollback feito): %s", uf, erro)
                resultados.append({"uf": uf, "status": "erro", "erro": str(erro)})
    finally:
        conn.close()
    return resultados


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    for item in processar(sorted(UFS_VALIDAS)):
        print(item)
