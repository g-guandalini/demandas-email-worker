import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from worker import project_path, response_command


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


if __name__ == "__main__":
    unittest.main()
