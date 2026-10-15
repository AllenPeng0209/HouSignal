"""Private token loading rejects alternate files even if their bytes are valid."""
import secrets
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

class SecretAuthTests(unittest.TestCase):
    def setUp(self):
        source=Path(__file__).parent/'native_runtime.py'
        spec=importlib.util.spec_from_file_location('native_runtime_auth',source)
        self.m=importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name]=self.m;spec.loader.exec_module(self.m)
        self.assertTrue(callable(getattr(self.m,'read_private_auth',None)),'Owned no-follow token reader is missing')
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name).resolve();self.root.chmod(0o700)
        self.auth=self.root/'native-qwen.auth'
        self.secret=secrets.token_urlsafe(32)
        self.auth.write_text(self.secret+'\n');self.auth.chmod(0o600)
    def read(self,path=None,root=None):return self.m.read_private_auth(path or self.auth,expected_root=root or self.root)
    def test_exact_owned_regular_private_file_is_read_without_printing_token(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):value=self.read()
        self.assertEqual(value,self.secret);self.assertEqual(output.getvalue(),'')
    def test_external_symlink_is_rejected_even_if_target_is_private(self):
        target=self.root/'outside-secret';self.auth.rename(target);self.auth.symlink_to(target)
        with self.assertRaises((ValueError,OSError)):self.read()
    def test_0400_is_rejected_instead_of_treating_it_as_0600(self):
        self.auth.chmod(0o400)
        with self.assertRaises(ValueError):self.read()
    def test_hardlinked_secret_is_rejected(self):
        os.link(self.auth,self.root/'duplicate-secret')
        with self.assertRaises(ValueError):self.read()
    def test_wrong_file_owner_is_rejected(self):
        original=os.fstat
        def wrong_owner(fd):
            value=original(fd)
            if self.m.stat.S_ISREG(value.st_mode):
                fields=list(value);fields[4]=os.geteuid()+1;return os.stat_result(fields)
            return value
        with patch.object(self.m.os,'fstat',wrong_owner),self.assertRaises(ValueError):self.read()
    def test_parent_symlink_and_unowned_or_public_parent_are_rejected(self):
        alias=self.root/'alias';alias.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError):self.read(path=alias/'native-qwen.auth',root=alias)
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):self.read()
    def test_caller_cannot_select_another_filename(self):
        target=self.root/'other.auth';target.write_text(self.secret);target.chmod(0o600)
        with self.assertRaises(ValueError):self.read(target)

if __name__=='__main__':unittest.main()
