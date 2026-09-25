import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from worker import project_path, response_command, run_codex


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


class CodexCommandTests(unittest.TestCase):
    def invoke_codex(self, auto_approve, sandbox):
        result = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        with patch.dict("os.environ", {"CODEX_BIN": "codex"}), patch("worker.subprocess.run", return_value=result) as run:
            output = run_codex(Path("/tmp/project"), "prompt", sandbox, auto_approve=auto_approve)
        return output, run.call_args.args[0]

    def test_read_only_mode_keeps_explicit_sandbox(self):
        output, command = self.invoke_codex(False, "read-only")
        self.assertEqual(output, "ok")
        self.assertIn(["--sandbox", "read-only"], [command[index:index + 2] for index in range(len(command) - 1)])
        self.assertNotIn("--approve-for-me", command)

    def test_auto_review_uses_its_own_workspace_write_sandbox(self):
        output, command = self.invoke_codex(True, "workspace-write")
        self.assertEqual(output, "ok")
        self.assertIn("--approve-for-me", command)
        self.assertNotIn("--sandbox", command)

    def test_auto_review_rejects_other_sandbox_modes(self):
        with self.assertRaises(ValueError):
            run_codex(Path("/tmp/project"), "prompt", "danger-full-access", auto_approve=True)


if __name__ == "__main__":
    unittest.main()
