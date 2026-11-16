"""
Gera CSVs 100% fictícios no formato do relatório de origem:
separador ';', cabeçalho na linha 1, intervalo em texto 'dd/mm/yyyy hh:mm',
decimais com vírgula e codificação ANSI (cp1252).

Nenhum valor aqui vem de dados reais.
"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

CABECALHO = [
    "intervalo",
    "chamadas_ofertadas",
    "chamadas_atendidas",
    "chamadas_abandonadas",
    "nivel_servico",
    "tma_segundos",
    "agentes_logados",
    "skill",
    "operacao",
]

SKILLS = ["Atendimento Geral", "Suporte Tecnico", "Retencao"]


def gerar_linhas(uf: str, dias: int = 1, semente: int = 42) -> list[list[str]]:
    """Uma linha por skill a cada 30 minutos, nos últimos `dias` dias (incluindo hoje)."""
    rng = random.Random(f"{semente}-{uf}")
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    linhas: list[list[str]] = []
    for d in range(dias - 1, -1, -1):
        inicio = hoje - timedelta(days=d)
        for passo in range(48):
            momento = inicio + timedelta(minutes=30 * passo)
            for skill in SKILLS:
                ofertadas = rng.randint(0, 120)
                atendidas = int(ofertadas * rng.uniform(0.75, 1.0))
                linhas.append([
                    momento.strftime("%d/%m/%Y %H:%M"),
                    str(ofertadas),
                    str(atendidas),
                    str(ofertadas - atendidas),
                    f"{rng.uniform(70, 100):.2f}".replace(".", ","),
                    str(rng.randint(180, 480)),
                    str(rng.randint(5, 60)),
                    skill,
                    "OPERACAO_A",
                ])
    return linhas


def escrever_csv(uf: str, pasta: Path, dias: int = 1) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"Relatorio_Historico_Skill_{uf}.csv"
    with open(destino, "w", newline="", encoding="cp1252") as f:
        escritor = csv.writer(f, delimiter=";", lineterminator="\n")
        escritor.writerow(CABECALHO)
        escritor.writerows(gerar_linhas(uf, dias))
    return destino


if __name__ == "__main__":
    pasta = Path(__file__).parent / "dados_ficticios"
    for uf in ("SP", "CE", "RJ"):
        caminho = escrever_csv(uf, pasta)
        print("gerado:", caminho.name)
