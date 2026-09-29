"""Local-only web interface for composing and sending demand emails."""

from __future__ import annotations

import json
import email
import email.policy
import email.utils
import hashlib
import imaplib
import os
import re
import secrets
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import worker


ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "web" / "index.html"
MAX_REQUEST_BYTES = 100_000
MAX_MARKDOWN_BYTES = 500_000
MAX_MAILBOX_MESSAGES = 500
MAILBOX_FETCH_BATCH_SIZE = 25
MAILBOX_CACHE_SECONDS = 30
DEMAND_ID_RE = re.compile(r"DEM-\d{8}-[A-F0-9]{8}", re.I)
_MAILBOX_CACHE_LOCK = threading.Lock()
_MAILBOX_CACHE = {"expires_at": 0.0, "demands": []}


def mailbox_messages() -> list[dict]:
    """Read recent messages without changing their seen/unseen state."""
    address, password = worker.email_settings()
    mailbox = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=15)
    try:
        mailbox.login(address, password)
        status, _ = mailbox.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Não foi possível abrir a caixa de entrada do Gmail")
        uids = search_demand_uids(mailbox, address)[-MAX_MAILBOX_MESSAGES:]
        messages = []
        for offset in range(0, len(uids), MAILBOX_FETCH_BATCH_SIZE):
            batch = uids[offset:offset + MAILBOX_FETCH_BATCH_SIZE]
            uid_set = b",".join(batch)
            status, fetched = mailbox.uid("fetch", uid_set, "(BODY.PEEK[])")
            if status != "OK":
                continue
            for entry in fetched:
                if not isinstance(entry, tuple) or len(entry) < 2 or not isinstance(entry[1], bytes):
                    continue
                raw = entry[1]
                if not raw or len(raw) > 1_000_000:
                    continue
                parsed = email.message_from_bytes(raw, policy=email.policy.default)
                data = worker.message_data(raw)
                if data["from"] != address or address not in data["recipients"]:
                    continue
                attachments = []
                for part in parsed.walk() if parsed.is_multipart() else [parsed]:
                    filename = part.get_filename()
                    if not filename or not filename.lower().endswith(".md"):
                        continue
                    payload = part.get_payload(decode=True) or b""
                    if len(payload) <= MAX_MARKDOWN_BYTES:
                        attachments.append({"filename": Path(filename).name, "text": payload.decode("utf-8", errors="replace")})
                data["attachments"] = attachments
                messages.append(data)
    finally:
        try:
            mailbox.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
    return messages


def search_demand_uids(mailbox, address: str) -> list[bytes]:
    """Search both original request and worker notification subject forms."""
    found: set[bytes] = set()
    for subject in ("DEMANDA:", "DEMANDAS BOT"):
        # imaplib sends string arguments verbatim; quote multiword IMAP search strings.
        subject_query = f'"{subject}"' if " " in subject else subject
        status, response = mailbox.uid(
            "search", None, "FROM", address, "TO", address, "SUBJECT", subject_query
        )
        if status != "OK":
            raise RuntimeError("Não foi possível consultar as mensagens do Gmail")
        if response:
            found.update(response[0].split())
    return sorted(found, key=int)


def mailbox_demands(messages: list[dict]) -> list[dict]:
    """Build a demand view from request and result emails; Gmail remains the source of truth."""
    by_suffix: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    for message in messages:
        subject = message.get("subject", "")
        body = message.get("body", "")
        bot_subject = subject.casefold().startswith("demandas bot |")
        response = worker.response_command(body)
        if not bot_subject and (subject.casefold().startswith("demanda:") or worker.has_request_fields(body)):
            seed = message.get("message_id") or body
            suffix = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8].upper()
            try:
                request = worker.parse_request(body)
            except ValueError:
                continue
            item = {
                "id": None,
                "title": request["title"],
                "type": request["type"],
                "projects": request["projects"],
                "received_at": message.get("date", ""),
                "status": "aguardando_worker",
                "spec": None,
                "result": None,
                "updated_at": message.get("date", ""),
            }
            by_suffix[suffix] = item
            continue
        if response and "demandas bot |" in subject.casefold():
            command, identifier, _ = response
            suffix = identifier.rsplit("-", 1)[1]
            item = by_id.get(identifier) or by_suffix.get(suffix) or {
                "id": identifier, "title": identifier, "type": "", "projects": [],
                "received_at": message.get("date", ""), "status": "aguardando_worker",
                "spec": None, "result": None, "updated_at": message.get("date", ""),
            }
            item["id"] = identifier
            item["updated_at"] = message.get("date", "")
            item["status"] = "aprovacao_enviada" if command == "APROVAR" else "reanalisando"
            by_id[identifier] = item
            continue
        match = DEMAND_ID_RE.search(subject)
        if not match:
            match = DEMAND_ID_RE.search(body)
        if not bot_subject or not match:
            continue
        identifier = match.group(0).upper()
        item = by_id.get(identifier)
        if item is None:
            suffix = identifier.rsplit("-", 1)[1]
            item = by_suffix.get(suffix, {
                "id": identifier, "title": identifier, "type": "", "projects": [],
                "received_at": message.get("date", ""), "status": "aguardando_worker",
                "spec": None, "result": None, "updated_at": message.get("date", ""),
            })
            by_id[identifier] = item
        item["id"] = identifier
        item["updated_at"] = message.get("date", "")
        lower_subject = subject.casefold()
        markdown = next((part for part in message.get("attachments", []) if part["filename"].lower().endswith(".md")), None)
        if "especificação pronta" in lower_subject and markdown:
            item["spec"] = markdown
            item["status"] = "aguardando_aprovacao"
        elif "implementação concluída" in lower_subject and markdown:
            item["result"] = markdown
            item["status"] = "implementado"
        elif "falha no worker" in lower_subject:
            item["status"] = "erro"
    demands = list({id(item): item for item in [*by_suffix.values(), *by_id.values()]}.values())
    def date_key(item: dict) -> float:
        try:
            return email.utils.parsedate_to_datetime(item.get("updated_at", "")).timestamp()
        except (TypeError, ValueError, OverflowError):
            return 0

    demands.sort(key=date_key, reverse=True)
    return demands


def current_demands() -> list[dict]:
    """Serialize Gmail reads and reuse their result across dashboard requests."""
    with _MAILBOX_CACHE_LOCK:
        if _MAILBOX_CACHE["expires_at"] > time.monotonic():
            return deepcopy(_MAILBOX_CACHE["demands"])
        demands = mailbox_demands(mailbox_messages())
        _MAILBOX_CACHE.update({
            "expires_at": time.monotonic() + MAILBOX_CACHE_SECONDS,
            "demands": demands,
        })
        return deepcopy(demands)


def dashboard_demands() -> list[dict]:
    """Return a compact list; fetch Markdown only when the user opens/downloads it."""
    result = current_demands()
    for demand in result:
        for key in ("spec", "result"):
            attachment = demand.get(key)
            if attachment:
                demand[key] = {"filename": attachment["filename"]}
    return result


def invalidate_mailbox_cache() -> None:
    with _MAILBOX_CACHE_LOCK:
        _MAILBOX_CACHE["expires_at"] = 0.0


def safe_imap_error(exc: Exception) -> str:
    detail = " ".join(str(exc).split())
    try:
        address, password = worker.email_settings()
        detail = detail.replace(password, "[segredo]").replace(address, "[email]")
    except Exception:
        pass
    return detail[:240] or type(exc).__name__


class DemandHandler(BaseHTTPRequestHandler):
    server_version = "DemandasLocal/1.0"

    def _send(self, status: int, content: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            # A browser can cancel a slow refresh; that is not an IMAP failure.
            pass

    def _json(self, status: int, value: dict) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _authorize_post(self) -> bool:
        if not secrets.compare_digest(self.headers.get("X-Worker-Token", ""), self.server.csrf_token):
            self._json(403, {"error": "Token de sessão inválido. Atualize a página e tente novamente."})
            return False
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
        if origin and origin.rstrip("/") not in allowed_origins:
            self._json(403, {"error": "Origem não autorizada"})
            return False
        return True

    def _read_json(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Envie os dados como application/json")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("O formulário está vazio ou excede o tamanho máximo")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("Formato de dados inválido")
        return data

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
        if path == "/api/demands":
            try:
                demands = dashboard_demands()
            except imaplib.IMAP4.error as exc:
                if "too many simultaneous connections" in str(exc).casefold():
                    self._json(503, {"error": "O Gmail limitou as conexões simultâneas. Aguarde alguns segundos e atualize o painel."})
                else:
                    print(f"Interface web: falha IMAP ({safe_imap_error(exc)})", flush=True)
                    self._json(502, {"error": "Não foi possível consultar as demandas no Gmail. Confira a conexão IMAP."})
                return
            except Exception as exc:
                print(f"Interface web: falha na leitura do Gmail ({type(exc).__name__})", flush=True)
                self._json(502, {"error": "Não foi possível consultar as demandas no Gmail. Confira a conexão IMAP."})
                return
            self._json(200, {"demands": demands})
            return
        file_match = re.fullmatch(r"/api/demands/(DEM-\d{8}-[A-F0-9]{8})/markdown/(spec|result)", path, re.I)
        if file_match:
            identifier, kind = file_match.group(1).upper(), file_match.group(2)
            try:
                demand = next((item for item in current_demands() if item.get("id") == identifier), None)
                markdown = demand.get(kind) if demand else None
                if not markdown:
                    self._json(404, {"error": "Arquivo Markdown não encontrado no Gmail"})
                    return
                if parse_qs(urlparse(self.path).query).get("preview") == ["1"]:
                    self._json(200, {"filename": markdown["filename"], "text": markdown["text"]})
                    return
                content = markdown["text"].encode("utf-8")
                safe_name = f"{identifier.lower()}-{kind}.md"
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)
            except Exception as exc:
                print(f"Interface web: falha ao baixar arquivo do Gmail ({type(exc).__name__})", flush=True)
                self._json(502, {"error": "Não foi possível baixar o arquivo do Gmail."})
            return
        self._json(404, {"error": "Rota não encontrada"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler interface
        path = urlparse(self.path).path
        if path not in {"/api/send"} and not re.fullmatch(r"/api/demands/DEM-\d{8}-[A-F0-9]{8}/command", path, re.I):
            self._json(404, {"error": "Rota não encontrada"})
            return
        if not self._authorize_post():
            return
        try:
            data = self._read_json()
            if path.startswith("/api/demands/"):
                return self._demand_command(path, data)
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
            invalidate_mailbox_cache()
            address, _ = worker.email_settings()
            self._json(200, {"message": f"Demanda enviada para {address}. O worker vai analisá-la no próximo ciclo."})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # SMTP errors must not expose credentials to the browser.
            print(f"Interface web: falha no envio de demanda ({type(exc).__name__})", flush=True)
            self._json(502, {"error": "Não foi possível enviar o e-mail. Confira a configuração SMTP e o log do worker."})

    def _demand_command(self, path: str, data: dict) -> None:
        match = re.fullmatch(r"/api/demands/(DEM-\d{8}-[A-F0-9]{8})/command", path, re.I)
        identifier = match.group(1).upper()
        action = str(data.get("action", "")).strip().lower()
        if action not in {"approve", "reanalyze"}:
            raise ValueError("Ação inválida")
        demand = next((item for item in current_demands() if item.get("id") == identifier), None)
        if not demand or demand.get("status") != "aguardando_aprovacao":
            raise ValueError("A demanda não está aguardando aprovação segundo os e-mails do Gmail")
        if action == "approve":
            body = f"APROVAR {identifier}"
            confirmation = f"Aprovação enviada. O worker local pegará a mensagem no próximo ciclo para iniciar o desenvolvimento de {identifier}."
        else:
            notes = str(data.get("notes", "")).strip()
            if not notes:
                raise ValueError("Descreva os novos pontos para a análise")
            if len(notes) > 20_000:
                raise ValueError("As observações excedem o limite de 20.000 caracteres")
            body = f"ANALISE {identifier}\n{notes}"
            confirmation = f"Pedido de nova análise enviado para {identifier}. O worker local o processará no próximo ciclo."
        worker.send_email(
            f"Re: DEMANDAS BOT | {identifier}: especificação pronta",
            body,
            bot_notification=False,
        )
        invalidate_mailbox_cache()
        self._json(200, {"message": confirmation})

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
