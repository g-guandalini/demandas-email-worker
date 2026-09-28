"""Local-only web interface for composing and sending demand emails."""

from __future__ import annotations

import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import worker


ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "web" / "index.html"
MAX_REQUEST_BYTES = 100_000


class DemandHandler(BaseHTTPRequestHandler):
    server_version = "DemandasLocal/1.0"

    def _send(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: int, value: dict) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler interface
        path = urlparse(self.path).path
        if path == "/healthz":
            self._json(200, {"status": "ok"})
            return
        if path == "/":
            page = PAGE.read_text(encoding="utf-8").replace("__CSRF_TOKEN__", self.server.csrf_token)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/projects":
            try:
                projects = worker.config()["projects"]
                self._json(200, {"projects": sorted(projects.keys())})
            except (OSError, ValueError, KeyError) as exc:
                self._json(500, {"error": f"Não foi possível carregar a lista de projetos: {exc}"})
            return
        self._json(404, {"error": "Rota não encontrada"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler interface
        if urlparse(self.path).path != "/api/send":
            self._json(404, {"error": "Rota não encontrada"})
            return
        if not secrets.compare_digest(self.headers.get("X-Worker-Token", ""), self.server.csrf_token):
            self._json(403, {"error": "Token de sessão inválido. Atualize a página e tente novamente."})
            return
        origin = self.headers.get("Origin", "")
        allowed_origins = {
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }
        configured_origins = os.environ.get("DEMANDAS_WEB_ALLOWED_ORIGINS", "")
        allowed_origins.update(item.strip().rstrip("/") for item in configured_origins.split(",") if item.strip())
        allowed_origins.update(
            str(item).strip().rstrip("/")
            for item in worker.config().get("web_allowed_origins", [])
            if str(item).strip()
        )
        origin = origin.rstrip("/")
        if origin and origin not in allowed_origins:
            self._json(403, {"error": "Origem não autorizada"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "Envie os dados como application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("O formulário está vazio ou excede o tamanho máximo")
            data = json.loads(self.rfile.read(length))
            project_keys = data.get("projects")
            if not isinstance(project_keys, list) or not project_keys:
                raise ValueError("Selecione pelo menos um repositório")
            if len(project_keys) != len(set(project_keys)):
                raise ValueError("O mesmo repositório foi selecionado mais de uma vez")
            request_type = str(data.get("type", "")).strip()
            title = str(data.get("title", "")).strip()
            description = str(data.get("description", "")).strip()
            if len(title) > 180 or not title:
                raise ValueError("Informe um título com até 180 caracteres")
            if not description:
                raise ValueError("Descreva a demanda")
            branches = data.get("branches", {})
            if not isinstance(branches, dict):
                raise ValueError("Branches inválidas")
            settings = worker.config()
            allowed_projects = settings.get("projects", {})
            unknown = set(project_keys) - set(allowed_projects)
            if unknown:
                raise ValueError("Repositório não permitido: " + ", ".join(sorted(unknown)))
            lines = [f"Projetos: {', '.join(project_keys)}", f"Tipo: {request_type}", f"Título: {title}"]
            for project_key in project_keys:
                branch = str(branches.get(project_key, "")).strip()
                if branch:
                    if any(ord(char) < 32 for char in branch) or len(branch) > 250:
                        raise ValueError(f"Nome de branch inválido para {project_key}")
                    if not os.environ.get("DEMANDAS_WEB_MAIL_ONLY"):
                        project = worker.project_path(project_key)
                        worker.validate_base_branch(project, branch)
                    lines.append(f"Branch base [{project_key}]: {branch}")
            body = "\n".join(lines) + "\n\n" + description
            parsed = worker.parse_request(body)
            if not os.environ.get("DEMANDAS_WEB_MAIL_ONLY"):
                worker.project_paths(parsed)
            worker.email_settings()
            worker.send_email(f"DEMANDA: {title}", body, bot_notification=False)
            address, _ = worker.email_settings()
            self._json(200, {"message": f"Demanda enviada para {address}. O worker vai analisá-la no próximo ciclo."})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # SMTP errors must not expose credentials to the browser.
            print(f"Interface web: falha no envio de demanda ({type(exc).__name__})", flush=True)
            self._json(502, {"error": "Não foi possível enviar o e-mail. Confira a configuração SMTP e o log do worker."})

    def log_message(self, format: str, *args) -> None:  # noqa: A003 - stdlib handler interface
        print(f"Web demandas: {self.address_string()} {format % args}", flush=True)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve() -> None:
    settings = worker.config()
    port = int(settings.get("web_port", 8765))
    if not 1 <= port <= 65535:
        raise ValueError("web_port deve estar entre 1 e 65535")
    host = os.environ.get("DEMANDAS_WEB_HOST", "127.0.0.1")
    server = LocalServer((host, port), DemandHandler)
    server.csrf_token = secrets.token_urlsafe(32)
    display_host = "127.0.0.1" if host in {"0.0.0.0", ""} else host
    print(f"Interface de demandas disponível em http://{display_host}:{port}. Ctrl+C para encerrar.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Interface web encerrada.", flush=True)
    finally:
        server.server_close()
