"""进程信息在本机上真实可读：local-sessions 列会话、认发送方都靠它（macOS 没有 /proc）。"""
import os
import unittest

from src import procinfo


class ProcinfoTests(unittest.TestCase):
    def test_own_process_identity(self):
        pid = os.getpid()
        self.assertIn('python', procinfo.name(pid))
        self.assertEqual(procinfo.parent(pid), os.getppid())
        self.assertEqual(procinfo.cwd(pid), os.path.realpath(os.getcwd()))
        start = procinfo.start_token(pid)
        self.assertTrue(start and start.isdigit(), start)
        self.assertEqual(start, procinfo.start_token(pid))  # 同一进程两次取值一致

    def test_missing_process(self):
        self.assertIsNone(procinfo.start_token(999999))
        self.assertIsNone(procinfo.cwd(999999))
        self.assertEqual(procinfo.args(999999), [])


if __name__ == '__main__':
    unittest.main()
