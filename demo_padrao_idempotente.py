"""
Demo didática do padrão de carga idempotente, rodando só com a biblioteca padrão.

A versão de produção (carga_idempotente.py) usa SQL Server, BULK INSERT e tabelas
temporárias. Aqui o MESMO padrão é reproduzido em SQLite para que qualquer pessoa
consiga executar e verificar as garantias:

  1. reexecutar o mesmo arquivo não duplica linhas;
  2. cada UF é uma partição independente;
  3. dados corrigidos substituem apenas os dias presentes no arquivo;
  4. uma falha no meio da carga desfaz tudo (nada fica pela metade).

Uso:  python demo_padrao_idempotente.py
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from gerar_dados_ficticios import gerar_linhas


def data_referencia(intervalo: str) -> str:
    """'dd/mm/yyyy hh:mm' -> 'yyyy-mm-dd'."""
    return datetime.strptime(intervalo, "%d/%m/%Y %H:%M").strftime("%Y-%m-%d")


def criar_banco() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE historico_skill (
               intervalo TEXT, chamadas_ofertadas INT, chamadas_atendidas INT,
               chamadas_abandonadas INT, nivel_servico TEXT, tma_segundos INT,
               agentes_logados INT, skill TEXT, operacao TEXT,
               dt_referencia TEXT, uf TEXT)"""
    )
    return conn


def carregar(conn: sqlite3.Connection, uf: str, linhas: list[list[str]]) -> tuple[int, int]:
    """DELETE da partição (UF + dias do arquivo) e INSERT, numa única transação."""
    dias = sorted({data_referencia(l[0]) for l in linhas})
    marcadores = ",".join("?" * len(dias))
    with conn:  # commit se tudo der certo; rollback se qualquer passo falhar
        removidas = conn.execute(
            f"DELETE FROM historico_skill WHERE uf = ? AND dt_referencia IN ({marcadores})",
            [uf, *dias],
        ).rowcount
        for l in linhas:
            conn.execute(
                "INSERT INTO historico_skill VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [*l, data_referencia(l[0]), uf],
            )
    return removidas, len(linhas)


def total(conn: sqlite3.Connection, uf: str | None = None) -> int:
    if uf:
        return conn.execute("SELECT COUNT(*) FROM historico_skill WHERE uf = ?", [uf]).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM historico_skill").fetchone()[0]


def main() -> None:
    conn = criar_banco()
    sp = gerar_linhas("SP", dias=2)
    ce = gerar_linhas("CE", dias=2)

    # 1) Reexecução não duplica
    carregar(conn, "SP", sp)
    primeiro = total(conn, "SP")
    removidas, inseridas = carregar(conn, "SP", sp)
    assert total(conn, "SP") == primeiro, "reexecução duplicou linhas"
    print(f"1) mesma carga 2x: SP continua com {primeiro} linhas "
          f"(2ª vez removeu {removidas} e inseriu {inseridas})")

    # 2) Partições independentes por UF
    carregar(conn, "CE", ce)
    assert total(conn, "SP") == primeiro
    print(f"2) CE carregado sem tocar SP: SP={total(conn, 'SP')}, CE={total(conn, 'CE')}")

    # 3) Correção substitui só os dias do arquivo
    ultimo_dia = data_referencia(sp[-1][0])
    correcao = [l for l in sp if data_referencia(l[0]) == ultimo_dia]
    for l in correcao:
        l[1] = "999"  # valor corrigido
    carregar(conn, "SP", correcao)
    assert total(conn, "SP") == primeiro, "correção alterou a quantidade de linhas"
    corrigidas = conn.execute(
        "SELECT COUNT(*) FROM historico_skill WHERE uf='SP' AND chamadas_ofertadas=999 AND dt_referencia=?",
        [ultimo_dia],
    ).fetchone()[0]
    assert corrigidas == len(correcao)
    print(f"3) correção de 1 dia: {corrigidas} linhas atualizadas, dia anterior intacto")

    # 4) Falha no meio da carga desfaz tudo
    antes = total(conn, "SP")
    defeituoso = [list(l) for l in sp]
    # linha truncada: passa pelo parse da data, mas quebra no meio dos INSERTs,
    # DEPOIS de o DELETE da partição já ter rodado dentro da transação
    defeituoso[len(defeituoso) // 2] = defeituoso[len(defeituoso) // 2][:3]
    try:
        carregar(conn, "SP", defeituoso)
        raise AssertionError("a carga defeituosa deveria ter falhado")
    except sqlite3.ProgrammingError:
        pass
    assert total(conn, "SP") == antes, "falha deixou a tabela pela metade"
    print(f"4) carga com erro no meio (após o DELETE): rollback, SP segue com {total(conn, 'SP')} linhas")

    print("\nTodas as garantias verificadas.")


if __name__ == "__main__":
    main()
