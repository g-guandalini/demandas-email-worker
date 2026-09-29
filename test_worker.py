import unittest
import hashlib
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from worker import (
    build_implementation_prompt,
    build_email,
    create_analysis_worktree,
    demand_branch_name,
    ensure_worktree_commit,
    implementation_completion_email,
    send_result_email,
    project_path,
    response_command,
    run_codex,
    set_status,
    parse_request,
    resolve_base_commit,
    remove_analysis_worktree,
    validate_base_branch,
)
import web_server
from web_server import mailbox_demands


REQUEST_ID = "DEM-20260924-C76EEED1"


class ResponseCommandTests(unittest.TestCase):
    def test_analysis_command_captures_new_notes(self):
        result = response_command(
            f"\nANALISE {REQUEST_ID}\nAdicionar filtro por jogador.\nConsiderar celular."
        )
        self.assertEqual(
            result,
            ("ANALISE", REQUEST_ID, "Adicionar filtro por jogador.\nConsiderar celular."),
        )

    def test_analysis_command_discards_quoted_email(self):
        result = response_command(
            f"ANALISE {REQUEST_ID}\nLevar em conta permissões.\n\nEm sex., alguém escreveu:\n> texto antigo"
        )
        self.assertEqual(result, ("ANALISE", REQUEST_ID, "Levar em conta permissões."))

    def test_analysis_requires_notes(self):
        self.assertIsNone(response_command(f"ANALISE {REQUEST_ID}\n"))

    def test_approval_has_no_details(self):
        self.assertEqual(response_command(f"APROVAR {REQUEST_ID}"), ("APROVAR", REQUEST_ID, ""))

    def test_command_must_be_first_nonempty_line(self):
        self.assertIsNone(response_command(f"Oi\nANALISE {REQUEST_ID}\nNovos pontos"))


class MailboxDashboardTests(unittest.TestCase):
    def setUp(self):
        web_server.invalidate_mailbox_cache()

    def test_dashboard_joins_request_and_spec_by_message_id_hash(self):
        message_id = "<request-123@example.com>"
        body = "Projetos: agente-ai\nTipo: feature\nTítulo: Novo recurso\n\nDetalhes da demanda"
        suffix = hashlib.sha256(message_id.encode()).hexdigest()[:8].upper()
        identifier = f"DEM-20260928-{suffix}"
        demands = mailbox_demands([
            {
                "message_id": message_id,
                "subject": "DEMANDA: Novo recurso",
                "body": body,
                "date": "Mon, 28 Sep 2026 10:00:00 -0300",
                "attachments": [],
            },
            {
                "message_id": "<spec@example.com>",
                "subject": f"DEMANDAS BOT | {identifier}: especificação pronta",
                "body": "A análise terminou.",
                "date": "Mon, 28 Sep 2026 10:05:00 -0300",
                "attachments": [{"filename": "especificacao.md", "text": "# Especificação"}],
            },
        ])
        self.assertEqual(len(demands), 1)
        self.assertEqual(demands[0]["id"], identifier)
        self.assertEqual(demands[0]["title"], "Novo recurso")
        self.assertEqual(demands[0]["status"], "aguardando_aprovacao")
        self.assertEqual(demands[0]["spec"]["text"], "# Especificação")

    def test_dashboard_marks_completed_demand_and_keeps_result_attachment(self):
        identifier = REQUEST_ID
        demands = mailbox_demands([{
            "subject": f"DEMANDAS BOT | {identifier}: implementação concluída",
            "body": "Para prosseguir",
            "date": "Mon, 28 Sep 2026 10:05:00 -0300",
            "attachments": [{"filename": "resultado.md", "text": "# Resultado"}],
        }])
        self.assertEqual(demands[0]["status"], "implementado")
        self.assertEqual(demands[0]["result"]["text"], "# Resultado")

    def test_dashboard_marks_a_demand_failure_from_its_notification(self):
        identifier = REQUEST_ID
        demands = mailbox_demands([{
            "subject": f"DEMANDAS BOT | {identifier}: falha no worker",
            "body": "Falha ao validar",
            "date": "Mon, 28 Sep 2026 10:05:00 -0300",
            "attachments": [],
        }])
        self.assertEqual(demands[0]["id"], identifier)
        self.assertEqual(demands[0]["status"], "erro")

    def test_dashboard_shows_approval_email_waiting_for_local_worker(self):
        identifier = REQUEST_ID
        demands = mailbox_demands([{
            "subject": f"Re: DEMANDAS BOT | {identifier}: especificação pronta",
            "body": f"APROVAR {identifier}",
            "date": "Mon, 28 Sep 2026 10:05:00 -0300",
            "attachments": [],
        }])
        self.assertEqual(demands[0]["status"], "aprovacao_enviada")

    def test_dashboard_reuses_recent_mailbox_read_and_invalidates_after_send(self):
        with patch("web_server.mailbox_messages", return_value=[]) as fetch:
            self.assertEqual(web_server.current_demands(), [])
            self.assertEqual(web_server.current_demands(), [])
            fetch.assert_called_once()
            web_server.invalidate_mailbox_cache()
            web_server.current_demands()
            self.assertEqual(fetch.call_count, 2)

    def test_dashboard_list_keeps_attachment_names_but_not_markdown_bodies(self):
        with patch("web_server.current_demands", return_value=[{
            "id": REQUEST_ID,
            "spec": {"filename": "especificacao.md", "text": "conteúdo longo"},
            "result": None,
        }]):
            demands = web_server.dashboard_demands()
        self.assertEqual(demands[0]["spec"], {"filename": "especificacao.md"})
        self.assertIsNone(demands[0]["result"])

    def test_gmail_search_includes_original_and_worker_notification_subjects(self):
        class FakeMailbox:
            def __init__(self):
                self.subjects = []

            def uid(self, command, *args):
                self.subjects.append(args[-1])
                return "OK", [b"1 2" if args[-1] == "DEMANDA:" else b"2 3"]

        mailbox = FakeMailbox()
        self.assertEqual(web_server.search_demand_uids(mailbox, "test@example.com"), [b"1", b"2", b"3"])
        self.assertEqual(mailbox.subjects, ["DEMANDA:", '"DEMANDAS BOT"'])


class EmailCreationTests(unittest.TestCase):
    def test_request_email_subject_is_not_marked_as_worker_notification(self):
        with patch("worker.email_settings", return_value=("person@example.com", "unused")):
            message = build_email("DEMANDA: Ajuste", "Projeto: projeto", bot_notification=False)
        self.assertEqual(message["Subject"], "DEMANDA: Ajuste")

    def test_worker_notifications_keep_the_bot_subject_prefix(self):
        with patch("worker.email_settings", return_value=("person@example.com", "unused")):
            message = build_email("Falha no worker", "Erro")
        self.assertEqual(message["Subject"], "DEMANDAS BOT | Falha no worker")


class ProjectPathTests(unittest.TestCase):
    def test_uses_configured_project_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "meu-projeto"
            (project / ".git").mkdir(parents=True)
            settings = {"projects_root": str(root), "projects": {"exemplo": str(project)}}
            with patch("worker.config", return_value=settings):
                self.assertEqual(project_path("exemplo"), project.resolve())

    def test_rejects_repository_outside_configured_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "permitidos"
            elsewhere = Path(temporary) / "fora"
            root.mkdir()
            (elsewhere / ".git").mkdir(parents=True)
            settings = {"projects_root": str(root), "projects": {"exemplo": str(elsewhere)}}
            with patch("worker.config", return_value=settings):
                with self.assertRaises(ValueError):
                    project_path("exemplo")


class RequestParsingTests(unittest.TestCase):
    def test_parses_optional_base_branch(self):
        request = parse_request(
            "Projeto: agente-ai\nTipo: feature\nTítulo: Ajuste de fluxo\nBranch base: feature/whatsapp"
        )
        self.assertEqual(request["base_branch"], "feature/whatsapp")

    def test_base_branch_is_optional(self):
        request = parse_request("Projeto: agente-ai\nTipo: task\nTítulo: Ajuste simples")
        self.assertIsNone(request["base_branch"])

    def test_parses_multiple_repositories_with_individual_base_branches(self):
        request = parse_request(
            "Projetos: agente-ai, agente-ai-n8n-workflows\n"
            "Tipo: feature\nTítulo: Fluxo completo\n"
            "Branch base [agente-ai]: feature/api\n"
            "Branch base [agente-ai-n8n-workflows]: main"
        )
        self.assertEqual(request["projects"], ["agente-ai", "agente-ai-n8n-workflows"])
        self.assertEqual(
            request["base_branches"],
            {"agente-ai": "feature/api", "agente-ai-n8n-workflows": "main"},
        )

    def test_common_base_branch_applies_to_all_selected_repositories(self):
        request = parse_request(
            "Projetos: api, workflows\nTipo: task\nTítulo: Atualizar integração\nBranch base: feature/shared"
        )
        self.assertEqual(request["base_branches"], {"api": "feature/shared", "workflows": "feature/shared"})

    def test_rejects_base_branch_for_unselected_repository(self):
        with self.assertRaisesRegex(ValueError, "não está na lista"):
            parse_request(
                "Projetos: api, workflows\nTipo: task\nTítulo: Atualizar integração\n"
                "Branch base [outro]: feature/shared"
            )

    def test_branch_name_uses_only_lowercase_request_id(self):
        self.assertEqual(demand_branch_name(REQUEST_ID), "dem-20260924-c76eeed1")

    def test_branch_name_rejects_invalid_request_id(self):
        with self.assertRaises(ValueError):
            demand_branch_name("feature/qualquer-nome")


class BaseBranchTests(unittest.TestCase):
    def test_accepts_existing_local_branch(self):
        results = [
            SimpleNamespace(returncode=0, stdout="feature/whatsapp\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        with patch("worker.subprocess.run", side_effect=results):
            self.assertEqual(validate_base_branch(Path("/repo"), "feature/whatsapp"), "feature/whatsapp")

    def test_rejects_invalid_branch_name(self):
        with patch("worker.subprocess.run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="")):
            with self.assertRaisesRegex(ValueError, "inválido"):
                validate_base_branch(Path("/repo"), "../main")

    def test_rejects_missing_local_branch(self):
        results = [
            SimpleNamespace(returncode=0, stdout="feature/missing\n", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr=""),
        ]
        with patch("worker.subprocess.run", side_effect=results):
            with self.assertRaisesRegex(ValueError, "não encontrada"):
                validate_base_branch(Path("/repo"), "feature/missing")

    def test_default_base_uses_updated_main_upstream(self):
        results = [
            SimpleNamespace(returncode=0, stdout="origin/main\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="local-sha\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="origin/main\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="remote-sha\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        with patch("worker.validate_base_branch", return_value=None), patch(
            "worker._git", side_effect=results
        ) as git:
            self.assertEqual(resolve_base_commit(Path("/repo"), None), ("main", "remote-sha"))
        self.assertIn(("fetch", "--quiet", "origin"), [call.args[1:] for call in git.call_args_list])

    def test_explicit_local_branch_without_upstream_stays_on_that_branch(self):
        results = [
            SimpleNamespace(returncode=0, stdout="local-feature-sha\n", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr=""),
        ]
        with patch("worker.validate_base_branch", return_value="feature/custom"), patch(
            "worker._git", side_effect=results
        ) as git:
            self.assertEqual(
                resolve_base_commit(Path("/repo"), "feature/custom"),
                ("feature/custom", "local-feature-sha"),
            )
        self.assertEqual(git.call_count, 2)


class AnalysisPullTests(unittest.TestCase):
    @staticmethod
    def git(path, *args):
        return subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_analysis_worktree_pulls_requested_origin_branch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            seed = root / "seed"
            project = root / "project"
            demand = root / "demand"
            analysis_worktree = demand / "analysis-worktree"
            demand.mkdir()

            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True)
            self.git(seed, "config", "user.email", "worker-test@example.com")
            self.git(seed, "config", "user.name", "Worker Test")
            (seed / "source.txt").write_text("before pull\n", encoding="utf-8")
            self.git(seed, "add", "source.txt")
            self.git(seed, "commit", "-m", "initial")
            self.git(seed, "remote", "add", "origin", str(remote))
            self.git(seed, "push", "-u", "origin", "main")
            subprocess.run(
                ["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "clone", str(remote), str(project)], check=True, capture_output=True)
            base_commit = self.git(project, "rev-parse", "refs/heads/main").stdout.strip()

            (seed / "source.txt").write_text("after pull\n", encoding="utf-8")
            self.git(seed, "commit", "-am", "updated remote")
            self.git(seed, "push", "origin", "main")

            worktree, pulled_commit, pulled_origin = create_analysis_worktree(project, demand, "main", base_commit)
            try:
                self.assertEqual(worktree, analysis_worktree)
                self.assertTrue(pulled_origin)
                self.assertNotEqual(pulled_commit, base_commit)
                self.assertEqual((worktree / "source.txt").read_text(encoding="utf-8"), "after pull\n")
            finally:
                remove_analysis_worktree(project, worktree)

    def test_analysis_uses_local_branch_when_origin_does_not_have_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            project = root / "project"
            demand = root / "demand"
            demand.mkdir()
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(project)], check=True, capture_output=True)
            self.git(project, "config", "user.email", "worker-test@example.com")
            self.git(project, "config", "user.name", "Worker Test")
            (project / "source.txt").write_text("local-only\n", encoding="utf-8")
            self.git(project, "add", "source.txt")
            self.git(project, "commit", "-m", "local base")
            self.git(project, "remote", "add", "origin", str(remote))
            self.git(project, "push", "origin", "main")
            self.git(project, "switch", "-c", "feature/local-only")
            (project / "source.txt").write_text("local branch commit\n", encoding="utf-8")
            self.git(project, "commit", "-am", "local-only work")
            local_commit = self.git(project, "rev-parse", "HEAD").stdout.strip()

            worktree, analyzed_commit, pulled_origin = create_analysis_worktree(
                project, demand, "feature/local-only", local_commit
            )
            try:
                self.assertFalse(pulled_origin)
                self.assertEqual(analyzed_commit, local_commit)
                self.assertEqual((worktree / "source.txt").read_text(encoding="utf-8"), "local branch commit\n")
            finally:
                remove_analysis_worktree(project, worktree)


class CodexCommandTests(unittest.TestCase):
    def invoke_codex(self, auto_approve, sandbox):
        result = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        with patch.dict("os.environ", {"CODEX_BIN": "codex", "CODEX_HOME": ""}), patch("worker.subprocess.run", return_value=result) as run:
            output = run_codex(Path("/tmp/project"), "prompt", sandbox, auto_approve=auto_approve)
        return output, run.call_args.args[0], run.call_args.kwargs["env"]

    def test_read_only_mode_keeps_explicit_sandbox(self):
        output, command, child_env = self.invoke_codex(False, "read-only")
        self.assertEqual(output, "ok")
        self.assertIn(["--sandbox", "read-only"], [command[index:index + 2] for index in range(len(command) - 1)])
        self.assertIn(["-c", "mcp_optional_startup_grace_ms=0"], [command[index:index + 2] for index in range(len(command) - 1)])
        self.assertNotIn("--approve-for-me", command)
        self.assertEqual(child_env["CODEX_HOME"], str(Path.home() / ".codex"))

    def test_extra_repositories_are_added_as_workspace_directories(self):
        result = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        extra = Path("/tmp/other-project")
        with patch.dict("os.environ", {"CODEX_BIN": "codex", "CODEX_HOME": ""}), patch(
            "worker.subprocess.run", return_value=result
        ) as run:
            run_codex(Path("/tmp/project"), "prompt", "read-only", add_dirs=[extra])
        command = run.call_args.args[0]
        self.assertIn(["--add-dir", str(extra)], [command[index:index + 2] for index in range(len(command) - 1)])

    def test_auto_review_uses_its_own_workspace_write_sandbox(self):
        output, command, child_env = self.invoke_codex(True, "workspace-write")
        self.assertEqual(output, "ok")
        self.assertIn("--approve-for-me", command)
        self.assertNotIn("--sandbox", command)
        self.assertIn(["-c", "mcp_optional_startup_grace_ms=0"], [command[index:index + 2] for index in range(len(command) - 1)])
        self.assertEqual(child_env["CODEX_HOME"], str(Path.home() / ".codex"))

    def test_auto_review_rejects_other_sandbox_modes(self):
        with self.assertRaises(ValueError):
            run_codex(Path("/tmp/project"), "prompt", "danger-full-access", auto_approve=True)


class ImplementationPromptTests(unittest.TestCase):
    def test_prompt_embeds_specification_without_external_path_dependency(self):
        request = {"body": "Solicitação original", "base_branch": "feature/whatsapp"}
        with patch("worker.workflow_instructions", return_value="Política global"):
            prompt = build_implementation_prompt(
                "DEM-20260924-C76EEED1",
                request,
                "feature/example",
                "Conteúdo integral da especificação",
                project_root=Path("/repo"),
            )
        self.assertIn("Conteúdo integral da especificação", prompt)
        self.assertIn("Não tente abrir o arquivo original", prompt)
        self.assertNotIn("/home/gustavo/Projetos/DEMANDAS/solicitacoes", prompt)
        self.assertIn("branch `feature/example`", prompt)
        self.assertIn("credenciais existentes no `.env`", prompt)
        self.assertIn("banco é local de desenvolvimento/teste", prompt)
        self.assertIn("/repo", prompt)

    def test_prompt_lists_each_repository_and_its_branch(self):
        repositories = {
            "api": {"path": "/projects/api", "branch": "feature/api", "base_branch": "feature/api", "base_commit": "a" * 40},
            "flows": {"path": "/projects/flows", "branch": "dem-123", "base_branch": "main", "base_commit": "b" * 40},
        }
        with patch("worker.workflow_instructions", return_value="Global rules"):
            prompt = build_implementation_prompt(
                REQUEST_ID, {"body": "Demand", "projects": ["api", "flows"]},
                "feature/api", "Specification", repositories=repositories,
            )
        self.assertIn("- api: checkout `/projects/api`, branch `feature/api`", prompt)
        self.assertIn("- flows: checkout `/projects/flows`, branch `dem-123`", prompt)
        self.assertIn("todos os repositórios selecionados", prompt)

    def test_no_code_changes_can_complete_validation_without_a_commit(self):
        results = [
            SimpleNamespace(stdout="", returncode=0),
            SimpleNamespace(stdout="same-commit", returncode=0),
            SimpleNamespace(stdout="", returncode=0),
            SimpleNamespace(stdout="0", returncode=0),
        ]
        with patch("worker.subprocess.run", side_effect=results):
            self.assertEqual(ensure_worktree_commit(Path("/tmp/worktree"), "same-commit"), "same-commit")

    def test_uncommitted_changes_are_not_reported_as_complete(self):
        result = SimpleNamespace(stdout=" M backend/file.js", returncode=0)
        with patch("worker.subprocess.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "sem commit"):
                ensure_worktree_commit(Path("/tmp/worktree"), "base")

    def test_completion_email_contains_instructions_without_agent_report(self):
        body = implementation_completion_email("feature/dem-example")
        self.assertIn("resultado.md", body)
        self.assertIn("Branch base: feature/dem-example", body)
        self.assertNotIn("Resumo do agente", body)

    def test_result_email_records_successful_delivery(self):
        import json

        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "status.json").write_text('{"status":"implementado","branch":"dem-example"}')
            (folder / "resultado.md").write_text("relatório")
            with patch("worker.send_email") as send_email:
                self.assertTrue(send_result_email(folder, "dem-example"))
            self.assertEqual(json.loads((folder / "status.json").read_text())["completion_email_status"], "enviado")
            send_email.assert_called_once()

    def test_result_email_failure_is_recorded_without_losing_completion(self):
        import json

        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "status.json").write_text('{"status":"implementado","branch":"dem-example"}')
            (folder / "resultado.md").write_text("relatório")
            with patch("worker.send_email", side_effect=RuntimeError("smtp indisponível")):
                self.assertFalse(send_result_email(folder, "dem-example"))
            status = json.loads((folder / "status.json").read_text())
            self.assertEqual(status["status"], "implementado")
            self.assertEqual(status["completion_email_status"], "falhou")
            self.assertEqual(status["completion_email_error"], "smtp indisponível")

    def test_status_transition_clears_stale_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "status.json").write_text('{"status":"erro_implementacao","error":"anterior"}')
            state = set_status(folder, "implementando")
        self.assertNotIn("error", state)


if __name__ == "__main__":
    unittest.main()
