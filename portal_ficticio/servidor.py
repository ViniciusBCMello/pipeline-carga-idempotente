"""
Servidor local do portal fictício: serve a página e gera o CSV sob demanda.

O CSV usa os mesmos dados fictícios de gerar_dados_ficticios.py e demora de
propósito para ser gerado, como acontece em sistemas reais.
"""
from __future__ import annotations

import csv
import io
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gerar_dados_ficticios import CABECALHO, gerar_linhas  # noqa: E402

UFS = {"SP", "CE", "RJ"}
ATRASO_RELATORIO_S = 1.5
PAGINA = Path(__file__).with_name("index.html")


def gerar_csv_bytes(uf: str) -> bytes:
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";", lineterminator="\n")
    escritor.writerow(CABECALHO)
    escritor.writerows(gerar_linhas(uf, dias=1))
    return buffer.getvalue().encode("cp1252")


class Manipulador(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        rota = urlparse(self.path)
        if rota.path == "/":
            corpo = PAGINA.read_bytes()
            self._responder(200, corpo, "text/html; charset=utf-8")
        elif rota.path == "/relatorio.csv":
            uf = parse_qs(rota.query).get("uf", [""])[0]
            if uf not in UFS:
                self._responder(404, b"UF desconhecida", "text/plain")
                return
            time.sleep(ATRASO_RELATORIO_S)  # geração "lenta"
            corpo = gerar_csv_bytes(uf)
            nome = f"exportacao_{int(time.time() * 1000)}.csv"  # nome imprevisível, como no real
            self._responder(
                200, corpo, "text/csv",
                {"Content-Disposition": f'attachment; filename="{nome}"'},
            )
        else:
            self._responder(404, b"nao encontrado", "text/plain")

    def _responder(self, status: int, corpo: bytes, tipo: str, extras: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        for chave, valor in (extras or {}).items():
            self.send_header(chave, valor)
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *args) -> None:  # silencia o log de acessos
        pass


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        # O Chrome abre e fecha conexões especulativas; no Windows isso aparece como
        # ConnectionResetError, inofensivo. Qualquer outro erro continua sendo exibido.
        if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def iniciar_servidor() -> tuple[ThreadingHTTPServer, str]:
    """Sobe o servidor numa porta livre, em segundo plano. Devolve (servidor, url)."""
    httpd = Servidor(("127.0.0.1", 0), Manipulador)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}/"


if __name__ == "__main__":
    httpd, url = iniciar_servidor()
    print("Portal fictício em", url, "(Ctrl+C para sair)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()