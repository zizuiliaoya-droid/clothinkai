"""New phase2 dependency delta; does not reopen or inherit old phase1 review."""
import unittest

import release_builder as builder
from test_release_tools import BuilderTests

PATHS = tuple('backend/app/modules/credential/' + name + '.py' for name in (
    '__init__','api','config','deps','enums','exceptions','models','permissions',
    'repository','schemas','service'))


class CredentialPathTests(unittest.TestCase):
    def test_exact_eleven_public_code_paths_are_accepted(self):
        for name in PATHS:
            self.assertEqual(builder.safe_name(name),name)

    def test_sensitive_siblings_variants_and_noncanonical_paths_remain_rejected(self):
        bad = ['backend/app/modules/credential/new.py',
               'backend/app/modules/credential/secrets.json',
               'backend/app/modules/credential/config.local.py',
               'backend/app/modules/credential/credentials.py',
               'backend/app/modules/credential/config.py.bak',
               'backend/app/modules/Credential/config.py',
               'backend/app/modules/credential/Config.py',
               'backend/app/modules/credential/../credential/config.py',
               'backend/app/modules/credential//config.py',
               'backend/app/modules/credentials/config.py',
               'backend/app/modules/credential/.env']
        for name in bad:
            with self.subTest(path=name), self.assertRaises(ValueError):
                builder.safe_name(name)

    def fixture(self):
        fixture=BuilderTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_git_source_contains_all_eleven_exact_committed_public_modules(self):
        fixture=self.fixture()
        for name in PATHS:
            fixture.put(name,b'PUBLIC_TEST_CODE = True\n')
        fixture.refresh_source_pins()
        source=builder.source_files(fixture.repo,fixture.head,fixture.tree)
        for name in PATHS:
            self.assertEqual(source[name],b'PUBLIC_TEST_CODE = True\n')

    def test_allowlisted_file_with_private_material_is_still_rejected(self):
        fixture=self.fixture()
        fixture.put(PATHS[2],b'PRIVATE = "-----BEGIN PRIVATE KEY-----"\n')
        fixture.refresh_source_pins()
        with self.assertRaises(ValueError):
            builder.source_files(fixture.repo,fixture.head,fixture.tree)

    def test_allowlisted_symlink_git_mode_is_still_rejected(self):
        fixture=self.fixture()
        oid=fixture.git('hash-object','-w','--stdin',stdin=b'fixture-target')
        fixture.git('update-index','--add','--cacheinfo','120000,'+oid+','+PATHS[2])
        fixture.git('commit','--quiet','-m','synthetic-link')
        head=fixture.git('rev-parse','HEAD')
        tree=fixture.git('rev-parse','HEAD^{tree}')
        with self.assertRaises(ValueError):
            builder.source_files(fixture.repo,head,tree)
