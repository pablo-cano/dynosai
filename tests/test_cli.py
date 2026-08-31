from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from dynosai_flow.cli import main, parser


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="dynosai-cli-"))
        self.addCleanup(lambda: shutil.rmtree(self.temp, ignore_errors=True))

    def test_cli_init_start_board(self):
        self.assertEqual(main(["--json", "init", str(self.temp), "--name", "CLI Test"]), 0)
        self.assertEqual(main(["--project", str(self.temp), "--json", "start", "Añadir ayuda CLI"]), 0)
        self.assertEqual(main(["--project", str(self.temp), "--json", "board"]), 0)


    def test_cli_version_flag(self):
        out=StringIO()
        with self.assertRaises(SystemExit) as ctx, redirect_stdout(out):
            parser().parse_args(["--version"])
        self.assertEqual(ctx.exception.code,0)
        self.assertIn("DynosAI 0.15.0",out.getvalue())

    def test_managed_agent_cannot_use_mutating_cli_as_privileged_controller(self):
        self.assertEqual(main(["--json", "init", str(self.temp), "--name", "CLI Guard Test"]), 0)
        self.assertEqual(main(["--project", str(self.temp), "--json", "start", "Cambio"]), 0)
        old=os.environ.get("DYNOSAI_MANAGED_AGENT"); os.environ["DYNOSAI_MANAGED_AGENT"]="1"
        try:
            self.assertEqual(main(["--project", str(self.temp), "continue", "DYN-0001"]), 1)
        finally:
            if old is None: os.environ.pop("DYNOSAI_MANAGED_AGENT",None)
            else: os.environ["DYNOSAI_MANAGED_AGENT"]=old


if __name__ == "__main__":
    unittest.main()
