import importlib.util
from pathlib import Path
import unittest
import socket

class PreflightTests(unittest.TestCase):
    def setUp(self):
        source=Path(__file__).parent / 'preflight.py'
        self.assertTrue(source.exists(), 'Idle-device launch guard is missing')
        spec=importlib.util.spec_from_file_location('preflight',source)
        self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m)
    def test_allocated_memory_or_compute_pid_or_utilization_blocks_launch(self):
        for memory,util,processes in [(129,0,''),(1,3,''),(1,0,'GPU-0, 9, 32')]:
            with self.subTest(memory=memory,util=util,processes=processes),self.assertRaises(RuntimeError):
                self.m.assert_idle(f'0, GPU-0, {memory}, 98304, {util}',processes)
    def test_large_idle_card_is_selected_without_interfering_other_cards(self):
        result=self.m.assert_idle('0, GPU-0, 1, 98304, 0\n8, GPU-8, 61456, 98304, 0','GPU-8, 42, 61200')
        self.assertEqual(result['physical_ppu'],0)
        self.assertEqual(result['free_memory_mib'],98303)
    def test_missing_or_malformed_or_too_small_device_fails_closed(self):
        for snapshot in ['1, GPU-1, 1, 98304, 0','0, GPU-0, unknown, 98304, 0','0, GPU-0, 1, 65536, 0']:
            with self.subTest(snapshot=snapshot),self.assertRaises(RuntimeError):self.m.assert_idle(snapshot,'')
    def test_loopback_admission_rejects_listener_and_allows_owned_time_wait(self):
        self.assertTrue(callable(getattr(self.m,"assert_loopback_port_available",None)),"Loopback bind guard is missing")
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind(("127.0.0.1",0));port=listener.getsockname()[1];listener.listen()
            with self.assertRaises(OSError):self.m.assert_loopback_port_available(port)
            with socket.create_connection(("127.0.0.1",port)) as client:
                connection,_=listener.accept();connection.close();client.recv(1)
        self.m.assert_loopback_port_available(port)

if __name__=='__main__':unittest.main()
