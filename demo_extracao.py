"""
Demo ponta a ponta, 100% local e com dados fictícios:

  portal fictício  ->  extração com Selenium  ->  CSV  ->  carga idempotente (SQLite)

Mostra também o isolamento de falhas: uma UF inexistente ("XX") dá erro sem
impedir as demais, e carregar duas vezes o mesmo arquivo não duplica linhas.

Uso:  python demo_extracao.py
Requisitos: pip install selenium  (e Chrome instalado)
"""
from __future__ import annotations

import csv
import logging
import os
import tempfile
from pathlib import Path

from selenium.webdriver.support.ui import WebDriverWait

from demo_padrao_idempotente import carregar, criar_banco, total
from extracao_web import criar_navegador, extrair_todas, login
from portal_ficticio.servidor import iniciar_servidor


def ler_csv(caminho: Path) -> list[list[str]]:
    with open(caminho, encoding="cp1252", newline="") as f:
        leitor = csv.reader(f, delimiter=";")
        next(leitor)  # cabeçalho
        return [linha for linha in leitor]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Credenciais só por ambiente; aqui valores de demonstração para o portal fictício.
    os.environ.setdefault("PORTAL_USUARIO", "usuario_demo")
    os.environ.setdefault("PORTAL_SENHA", "demo")

    pasta_download = Path(tempfile.mkdtemp(prefix="downloads_"))
    pasta_destino = Path(tempfile.mkdtemp(prefix="relatorios_"))

    httpd, url = iniciar_servidor()
    navegador = criar_navegador(pasta_download, headless=False)
    try:
        espera = WebDriverWait(navegador, 20)
        # ?sessao_duplicada=1 faz o portal exibir o modal de sessão simultânea
        login(navegador, espera, url + "?sessao_duplicada=1",
              os.environ["PORTAL_USUARIO"], os.environ["PORTAL_SENHA"])
        resultados = extrair_todas(navegador, espera, ["SP", "CE", "XX", "RJ"],
                                   pasta_download, pasta_destino)
    finally:
        navegador.quit()
        httpd.shutdown()

    print("\nExtração:")
    for r in resultados:
        print("  ", r)

    conn = criar_banco()
    print("\nCarga (SQLite, padrão idempotente):")
    for r in resultados:
        if r["status"] != "sucesso":
            continue
        linhas = ler_csv(pasta_destino / r["arquivo"])
        carregar(conn, r["uf"], linhas)
        primeira = total(conn, r["uf"])
        carregar(conn, r["uf"], linhas)  # mesma carga de novo
        assert total(conn, r["uf"]) == primeira, "reexecução duplicou linhas"
        print(f"   {r['uf']}: {primeira} linhas; 2ª carga do mesmo arquivo não duplicou")

    sucessos = [r for r in resultados if r["status"] == "sucesso"]
    erros = [r for r in resultados if r["status"] == "erro"]
    assert len(sucessos) == 3 and len(erros) == 1, "resultado inesperado da extração"
    print(f"\nOK: {len(sucessos)} UFs extraídas e carregadas, {len(erros)} erro isolado.")


if __name__ == "__main__":
    main()
