import importlib.util
import os
import pathlib
import tempfile
import unittest

SERVICE = pathlib.Path(__file__).resolve().parents[1] / "services" / "n8n-task-runners"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_closure = _load("finance_validate_runner_closure", SERVICE / "validate_closure.py")
relink_closure = _load("finance_relink_runner_closure", SERVICE / "relink_closure.py")


class N8nRunnerClosurePortableTests(unittest.TestCase):
    def test_regular_file_fingerprint_is_stable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "closure"
            root.mkdir()
            (root / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")

            first = validate_closure.validate_and_fingerprint(root)
            second = validate_closure.validate_and_fingerprint(root)

            self.assertEqual(first, second)
            self.assertEqual(first["files"], 1)
            self.assertEqual(first["symlinks"], 0)
            self.assertRegex(first["closure_sha256"], r"^[0-9a-f]{64}$")


@unittest.skipIf(os.name == "nt", "symlink creation is not guaranteed on Windows runners")
class N8nRunnerClosureTests(unittest.TestCase):
    def _workspace_fixture(self, base, duplicate=False):
        workspace = base / "workspace"
        package = workspace / "packages" / "@n8n" / "config"
        package.mkdir(parents=True)
        (package / "package.json").write_text(
            '{"name":"@n8n/config","version":"1.2.3"}\n', encoding="utf-8"
        )
        (package / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")

        closure = workspace / "dist" / "runner"
        store_package = (
            closure
            / "node_modules"
            / ".pnpm"
            / "@n8n+config@file+packages+@n8n+config"
            / "node_modules"
            / "@n8n"
            / "config"
        )
        store_package.mkdir(parents=True)
        (store_package / "package.json").write_text(
            '{"name":"@n8n/config","version":"1.2.3"}\n', encoding="utf-8"
        )
        (store_package / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
        if duplicate:
            duplicate_package = (
                closure
                / "node_modules"
                / ".pnpm"
                / "@n8n+config@file+packages+@n8n+config_peer-duplicate"
                / "node_modules"
                / "@n8n"
                / "config"
            )
            duplicate_package.mkdir(parents=True)
            (duplicate_package / "package.json").write_text(
                '{"name":"@n8n/config","version":"1.2.3"}\n', encoding="utf-8"
            )
        public = closure / "node_modules" / "@n8n" / "config"
        public.parent.mkdir(parents=True, exist_ok=True)
        public.symlink_to(package, target_is_directory=True)
        return workspace, closure, public, store_package

    def test_internal_symlink_is_portable_and_fingerprint_is_stable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "closure"
            package = root / "node_modules" / ".pnpm" / "package" / "node_modules" / "package"
            package.mkdir(parents=True)
            (package / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
            public = root / "node_modules" / "package"
            public.symlink_to(pathlib.Path(".pnpm/package/node_modules/package"), target_is_directory=True)

            first = validate_closure.validate_and_fingerprint(root)
            second = validate_closure.validate_and_fingerprint(root)

            self.assertEqual(first, second)
            self.assertEqual(first["files"], 1)
            self.assertEqual(first["symlinks"], 1)

    def test_external_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            root = base / "closure"
            root.mkdir()
            external = base / "workspace-package"
            external.mkdir()
            (root / "package").symlink_to(external, target_is_directory=True)

            with self.assertRaisesRegex(validate_closure.ClosureError, "external symlink"):
                validate_closure.validate_and_fingerprint(root)

    def test_workspace_link_is_relinked_to_unique_internal_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace, closure, public, store_package = self._workspace_fixture(pathlib.Path(temp))

            changes = relink_closure.relink_external_workspace_links(closure, workspace)

            self.assertEqual(len(changes), 1)
            self.assertEqual(public.resolve(), store_package.resolve())
            self.assertEqual(validate_closure.validate_and_fingerprint(closure)["symlinks"], 1)

    def test_ambiguous_internal_workspace_copies_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace, closure, _, _ = self._workspace_fixture(pathlib.Path(temp), duplicate=True)

            with self.assertRaisesRegex(relink_closure.RelinkError, "expected one in-closure copy"):
                relink_closure.relink_external_workspace_links(closure, workspace)

    def test_non_workspace_external_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            workspace, closure, public, _ = self._workspace_fixture(base)
            outside = base / "outside"
            outside.mkdir()
            public.unlink()
            public.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(relink_closure.RelinkError, "escapes pinned workspace"):
                relink_closure.relink_external_workspace_links(closure, workspace)

    def test_dangling_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "closure"
            root.mkdir()
            (root / "missing").symlink_to("not-present")

            with self.assertRaisesRegex(validate_closure.ClosureError, "dangling or cyclic"):
                validate_closure.validate_and_fingerprint(root)


if __name__ == "__main__":
    unittest.main()
