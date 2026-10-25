"""Integration tests: TEST_DATABASE_URL must target a dedicated test database."""
import os
from pathlib import Path
import tempfile
import unittest

import psycopg
from database.store import Database


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'TEST_DATABASE_URL is not set')
class DatabaseTests(unittest.TestCase):
    def test_persistence_constraints_and_cascade(self):
        db = Database(os.environ['TEST_DATABASE_URL'])
        with db.connect() as conn:
            conn.autocommit = True
            conn.execute(Path('database/schema.sql').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'example.pdf'
            path.write_bytes(os.urandom(64))  # Store hashes bytes; GUI parses the actual PDF.
            doc_id = db.register_document(path, 2)
            try:
                renamed = path.rename(Path(folder) / 'renamed.pdf')
                self.assertEqual(db.register_document(renamed, 2), doc_id)
                content = "สรุปภาษาไทย '); DROP TABLE notes; --"
                db.save_note(doc_id, 1, content)
                self.assertEqual(Database(db.dsn).get_note(doc_id, 1), content)
                db.save_note(doc_id, 1, 'แก้ไข')
                self.assertEqual(db.get_note(doc_id, 1), 'แก้ไข')
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    db.save_note(doc_id, 3, 'invalid page')
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    db.add_summary(doc_id, 0, 'test', 'invalid page')
                db.add_summary(doc_id, 1, 'qwen2.5:7b', 'ครั้งแรก')
                db.add_summary(doc_id, 1, 'qwen2.5:7b', 'ครั้งที่สอง')
                self.assertEqual(len(db.list_summaries(doc_id, 1)), 2)
                db.delete_note(doc_id, 1)
                self.assertEqual(db.get_note(doc_id, 1), '')
                db.delete_document(doc_id)
                self.assertEqual(db.list_summaries(doc_id, 1), [])
                self.assertTrue(renamed.exists())
            finally:
                db.delete_document(doc_id)


if __name__ == '__main__':
    unittest.main()
