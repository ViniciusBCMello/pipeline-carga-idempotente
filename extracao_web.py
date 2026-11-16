"""
Extração web genérica com Selenium.

Reúne os padrões de robustez usados na automação real, sem nenhum seletor, URL
ou credencial de sistema de verdade. Os seletores abaixo apontam para o portal
fictício de `portal_ficticio/`; para outro sistema, só `SELETORES` muda.

Padrões demonstrados:
  - esperas explícitas por elemento (nada de `sleep` fixo para sincronizar a página);
  - retentativa de clique/digitação com re-localização do elemento (evita
    StaleElementReference e, quando o navegador acusa, cliques bloqueados por overlay);
  - tratamento de modal opcional ("sessão ativa em outro local");
  - espera do download com limite de tempo e checagem de arquivo estável;
  - credenciais só por variável de ambiente.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

log = logging.getLogger("extracao")

Localizador = tuple[str, str]

SELETORES: dict[str, Localizador] = {
    "usuario": (By.ID, "usuario"),
    "senha": (By.ID, "senha"),
    "entrar": (By.ID, "entrar"),
    "modal_sessao": (By.ID, "modal-sessao"),
    "confirmar_sessao": (By.ID, "confirmar-sessao"),
    "painel": (By.ID, "painel"),
    "uf": (By.ID, "uf"),
    "data": (By.ID, "data"),
    "gerar": (By.ID, "gerar"),
    "carregando": (By.ID, "carregando"),
    "baixar": (By.ID, "baixar"),
}


# --------------------------------------------------------------------------
# Navegador
# --------------------------------------------------------------------------
def criar_navegador(
    pasta_download: Path, headless: bool = True, perfil: Path | None = None
) -> WebDriver:
    """
    Chrome configurado para baixar arquivos sem janelas de confirmação.

    perfil: pasta de perfil persistente (mantém cookies e sessão entre execuções,
            útil quando o login passa por SSO).
    Variáveis opcionais: CHROME_BINARY, CHROMEDRIVER_PATH, CHROME_NO_SANDBOX=1.
    """
    pasta_download = Path(pasta_download)
    pasta_download.mkdir(parents=True, exist_ok=True)

    opcoes = Options()
    if headless:
        opcoes.add_argument("--headless=new")
    if perfil:
        opcoes.add_argument(f"--user-data-dir={perfil}")
    if os.getenv("CHROME_NO_SANDBOX") == "1":  # contêineres/CI
        for arg in ("--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"):
            opcoes.add_argument(arg)
    opcoes.add_argument("--window-size=1366,900")
    # Evita o balão/aviso de download do Chrome, que bloquearia a automação
    opcoes.add_argument("--disable-features=DownloadBubble,DownloadBubbleV2")
    opcoes.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(pasta_download.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
        },
    )
    opcoes.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])

    binario = os.getenv("CHROME_BINARY")
    if binario:
        opcoes.binary_location = binario
    caminho_driver = os.getenv("CHROMEDRIVER_PATH")
    servico = Service(caminho_driver) if caminho_driver else Service()  # sem caminho: Selenium Manager

    navegador = webdriver.Chrome(service=servico, options=opcoes)
    try:  # garante a pasta de download também em modos headless
        navegador.execute_cdp_cmd(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(pasta_download.resolve())},
        )
    except Exception as erro:  # noqa: BLE001
        log.debug("setDownloadBehavior indisponível: %s", erro)
    return navegador


# --------------------------------------------------------------------------
# Interação com a página
# --------------------------------------------------------------------------
def clicar_e_digitar(
    espera: WebDriverWait,
    localizador: Localizador,
    texto: str | None = None,
    limpar: bool = False,
    tentativas: int = 3,
) -> None:
    """
    Clica (e opcionalmente digita) re-localizando o elemento a cada tentativa.

    Páginas dinâmicas trocam elementos do DOM sem aviso (StaleElementReference)
    ou cobrem a tela com um overlay (ElementClickIntercepted). Em vez de guardar
    a referência, procuramos o elemento de novo e tentamos mais algumas vezes.
    """
    ultimo_erro: Exception | None = None
    for numero in range(1, tentativas + 1):
        try:
            elemento = espera.until(EC.element_to_be_clickable(localizador))
            elemento.click()
            if limpar:
                elemento.clear()
            if texto is not None:
                elemento.send_keys(texto)
            return
        except (StaleElementReferenceException, ElementClickInterceptedException) as erro:
            ultimo_erro = erro
            log.warning("%s, tentativa %s/%s: %s", type(erro).__name__, numero, tentativas, localizador)
            time.sleep(0.5 * numero)  # recuo curto e crescente entre tentativas
    raise RuntimeError(
        f"Não foi possível interagir com {localizador} após {tentativas} tentativas"
    ) from ultimo_erro


def esperar_overlay_sumir(espera: WebDriverWait, localizador: Localizador | None = None) -> None:
    espera.until(EC.invisibility_of_element_located(localizador or SELETORES["carregando"]))


def _visivel(navegador: WebDriver, localizador: Localizador) -> bool:
    return any(e.is_displayed() for e in navegador.find_elements(*localizador))


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------
def credenciais_do_ambiente() -> tuple[str, str]:
    """Usuário e senha vêm de variáveis de ambiente, nunca do código."""
    try:
        return os.environ["PORTAL_USUARIO"], os.environ["PORTAL_SENHA"]
    except KeyError as erro:
        raise RuntimeError(f"Defina a variável de ambiente {erro.args[0]}") from None


def login(navegador: WebDriver, espera: WebDriverWait, url: str, usuario: str, senha: str) -> None:
    """
    Faz login e trata o modal opcional de sessão simultânea.

    Em vez de um `sleep` fixo para "dar tempo ao modal aparecer", esperamos pelo
    primeiro de dois desfechos possíveis: o painel (login normal) ou o modal.
    """
    navegador.get(url)
    espera.until(EC.presence_of_element_located(SELETORES["usuario"]))  # formulário pronto
    clicar_e_digitar(espera, SELETORES["usuario"], usuario, limpar=True)
    clicar_e_digitar(espera, SELETORES["senha"], senha, limpar=True)
    clicar_e_digitar(espera, SELETORES["entrar"])

    espera.until(
        lambda d: _visivel(d, SELETORES["painel"]) or _visivel(d, SELETORES["modal_sessao"])
    )
    if _visivel(navegador, SELETORES["modal_sessao"]):
        log.info("Sessão simultânea detectada; encerrando a outra sessão")
        clicar_e_digitar(espera, SELETORES["confirmar_sessao"])
        espera.until(EC.visibility_of_element_located(SELETORES["painel"]))
    log.info("Login concluído")


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------
def aguardar_download(
    pasta: Path,
    antes: set[Path],
    extensao: str = ".csv",
    timeout_s: float = 60,
    intervalo_s: float = 0.5,
) -> Path:
    """
    Espera surgir um arquivo novo na pasta e devolve quando ele para de crescer.

    Compara a pasta com o instantâneo `antes`. O Chrome grava `nome.csv.crdownload`
    durante o download, o que não casa com `*.csv`: o arquivo só aparece quando
    termina. Mesmo assim exigimos tamanho > 0 e igual em duas leituras seguidas.
    """
    pasta = Path(pasta)
    limite = time.monotonic() + timeout_s
    anterior: tuple[Path, int] | None = None
    while time.monotonic() < limite:
        novos = set(pasta.glob(f"*{extensao}")) - antes
        if novos:
            arquivo = max(novos, key=lambda p: p.stat().st_mtime)
            tamanho = arquivo.stat().st_size
            if tamanho > 0 and anterior == (arquivo, tamanho):
                return arquivo
            anterior = (arquivo, tamanho)
        time.sleep(intervalo_s)
    raise TimeoutError(f"Download não concluído em {timeout_s:.0f}s (pasta: {pasta})")


def mover_para_destino(arquivo: Path, pasta_destino: Path, nome: str) -> Path:
    pasta_destino = Path(pasta_destino)
    pasta_destino.mkdir(parents=True, exist_ok=True)
    destino = pasta_destino / nome
    shutil.move(str(arquivo), str(destino))  # funciona também entre volumes diferentes
    return destino


# --------------------------------------------------------------------------
# Fluxo de extração
# --------------------------------------------------------------------------
def extrair_relatorio(
    navegador: WebDriver,
    espera: WebDriverWait,
    uf: str,
    pasta_download: Path,
    pasta_destino: Path,
    data: str | None = None,
) -> Path:
    """Gera o relatório da UF no portal, baixa o CSV e o salva com o nome esperado pela carga."""
    data = data or datetime.now().strftime("%d/%m/%Y")

    Select(espera.until(EC.element_to_be_clickable(SELETORES["uf"]))).select_by_value(uf)
    clicar_e_digitar(espera, SELETORES["data"], data, limpar=True)
    clicar_e_digitar(espera, SELETORES["gerar"])

    esperar_overlay_sumir(espera)                                   # relatório carregando
    espera.until(EC.element_to_be_clickable(SELETORES["baixar"]))   # botão habilitado

    antes = set(Path(pasta_download).glob("*.csv"))  # instantâneo ANTES de clicar
    clicar_e_digitar(espera, SELETORES["baixar"])
    baixado = aguardar_download(pasta_download, antes)

    nome = f"Relatorio_Historico_Skill_{uf}.csv"  # o mesmo nome que carga_idempotente.py lê
    destino = mover_para_destino(baixado, pasta_destino, nome)
    log.info("[%s] relatório salvo em %s", uf, destino.name)
    return destino


def extrair_todas(
    navegador: WebDriver,
    espera: WebDriverWait,
    ufs: list[str],
    pasta_download: Path,
    pasta_destino: Path,
) -> list[dict]:
    """Uma UF por vez; o erro de uma é registrado e não impede as demais."""
    resultados: list[dict] = []
    for uf in ufs:
        try:
            arquivo = extrair_relatorio(navegador, espera, uf, pasta_download, pasta_destino)
            resultados.append({"uf": uf, "status": "sucesso", "arquivo": arquivo.name})
        except Exception as erro:  # noqa: BLE001 - registramos e seguimos
            log.error("[%s] erro na extração: %s: %s", uf, type(erro).__name__, erro)
            resultados.append({"uf": uf, "status": "erro", "erro": f"{type(erro).__name__}"})
    return resultados
