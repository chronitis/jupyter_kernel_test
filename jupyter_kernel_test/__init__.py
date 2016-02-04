"""Machinery for testing Jupyter kernels via the messaging protocol.
"""

from unittest import TestCase, SkipTest
try:                  # Python 3
    from queue import Empty
except ImportError:   # Python 2
    from Queue import Empty

from jupyter_client.manager import start_new_kernel
from .messagespec import validate_message, MimeBundle

TIMEOUT = 15

__version__ = '0.1'

class KernelTests(TestCase):
    kernel_name = ""

    @classmethod
    def setUpClass(cls):
        cls.km, cls.kc = start_new_kernel(kernel_name=cls.kernel_name)

    @classmethod
    def tearDownClass(cls):
        cls.kc.stop_channels()
        cls.km.shutdown_kernel()

    def flush_channels(self):
        for channel in (self.kc.shell_channel, self.kc.iopub_channel):
            while True:
                try:
                    msg = channel.get_msg(block=True, timeout=0.1)
                except Empty:
                    break
                else:
                    validate_message(msg)

    language_name = ""

    def test_kernel_info(self):
        self.flush_channels()

        msg_id = self.kc.kernel_info()
        reply = self.kc.get_shell_msg(timeout=TIMEOUT)
        validate_message(reply, 'kernel_info_reply', msg_id)

        if self.language_name:
            self.assertEqual(reply['content']['language_info']['name'],
                             self.language_name,
                             msg="Language name in kernel_info didn't match language_name")

    def execute_helper(self, code, timeout=TIMEOUT):
        msg_id = self.kc.execute(code=code)

        reply = self.kc.get_shell_msg(timeout=timeout)
        validate_message(reply, 'execute_reply', msg_id)

        busy_msg = self.kc.iopub_channel.get_msg(timeout=1)
        validate_message(busy_msg, 'status', msg_id)
        self.assertEqual(busy_msg['content']['execution_state'], 'busy',
                         msg="Expected a status message with execution_state=busy after code execution")

        output_msgs = []
        while True:
            msg = self.kc.iopub_channel.get_msg(timeout=0.1)
            validate_message(msg, msg['msg_type'], msg_id)
            if msg['msg_type'] == 'status':
                self.assertEqual(msg['content']['execution_state'], 'idle',
                                 msg="Expected a status message with execution_state=idle after execute, busy (code ran for too long?)")
                break
            elif msg['msg_type'] == 'execute_input':
                self.assertEqual(msg['content']['code'], code,
                                 msg="Code in execute_input message didn't match the executed code")
                continue
            output_msgs.append(msg)

        return reply, output_msgs

    code_hello_world = ""

    def test_execute_stdout(self):
        if not self.code_hello_world:
            raise SkipTest

        self.flush_channels()
        reply, output_msgs = self.execute_helper(code=self.code_hello_world)

        self.assertEqual(reply['content']['status'], 'ok',
                         msg="execute_reply had status != ok")

        self.assertGreaterEqual(len(output_msgs), 1,
                                msg="Got no messages on iopub socket after code execution")
        self.assertEqual(output_msgs[0]['msg_type'], 'stream',
                         msg="Expected a `stream` message on iopub")
        self.assertEqual(output_msgs[0]['content']['name'], 'stdout',
                         msg="Expected name=stdout in stream message")
        self.assertIn('hello, world', output_msgs[0]['content']['text'],
                      msg="Expected stdout to contain string 'hello, world'")

    completion_samples = []

    def test_completion(self):
        if not self.completion_samples:
            raise SkipTest

        for sample in self.completion_samples:
            msg_id = self.kc.complete(sample['text'])
            reply = self.kc.get_shell_msg()
            validate_message(reply, 'complete_reply', msg_id)
            if 'matches' in sample:
                self.assertEqual(set(reply['content']['matches']),
                                 set(sample['matches']),
                                 msg="Completion request didn't return expected results")

    complete_code_samples = []
    incomplete_code_samples = []
    invalid_code_samples = []

    def check_is_complete(self, sample, status):
        msg_id = self.kc.is_complete(sample)
        reply = self.kc.get_shell_msg()
        validate_message(reply, 'is_complete_reply', msg_id)
        if reply['content']['status'] != status:
            msg = "For code sample\n  {!r}\nExpected {!r}, got {!r}."
            raise AssertionError(msg.format(sample, status,
                                            reply['content']['status']))

    def test_is_complete(self):
        if not (self.complete_code_samples
                or self.incomplete_code_samples
                or self.invalid_code_samples):
            raise SkipTest

        self.flush_channels()

        for sample in self.complete_code_samples:
            self.check_is_complete(sample, 'complete')

        for sample in self.incomplete_code_samples:
            self.check_is_complete(sample, 'incomplete')

        for sample in self.invalid_code_samples:
            self.check_is_complete(sample, 'invalid')

    code_page_something = ""

    def test_pager(self):
        if not self.code_page_something:
            raise SkipTest

        self.flush_channels()

        reply, output_msgs = self.execute_helper(self.code_page_something)
        self.assertEqual(reply['content']['status'], 'ok',
                         msg="Expected status=ok in execute_reply")
        payloads = reply['content']['payload']
        self.assertEqual(len(payloads), 1,
                         msg="Expected a single payload in pager execute_reply")
        self.assertEqual(payloads[0]['source'], 'page',
                         msg="Expected payload[0].source to be 'page'")
        mimebundle = payloads[0]['data']
        # Validate the mimebundle
        MimeBundle().data = mimebundle
        self.assertIn('text/plain', mimebundle,
                      msg="Expected payload[0].data to have 'text/plain' content")

    code_generate_error = ""

    def test_error(self):
        if not self.code_generate_error:
            raise SkipTest

        self.flush_channels()

        reply, output_msgs = self.execute_helper(self.code_generate_error)
        self.assertEqual(reply['content']['status'], 'error',
                         msg="Expected execute_reply to have status=error")

    code_execute_result = []

    def test_execute_result(self):
        if not self.code_execute_result:
            raise SkipTest

        for sample in self.code_execute_result:
            self.flush_channels()

            reply, output_msgs = self.execute_helper(sample['code'])

            self.assertEqual(reply['content']['status'], 'ok',
                             msg="Expected execute_reply to have status=ok")

            self.assertGreaterEqual(len(output_msgs), 1,
                                    msg="Got no messages on iopub socket after code execution")
            self.assertEqual(output_msgs[0]['msg_type'], 'execute_result',
                             msg="Expected an execute_result message")
            self.assertIn('text/plain', output_msgs[0]['content']['data'],
                          msg="Expected data to contain 'text/plain'")
            self.assertEqual(output_msgs[0]['content']['data']['text/plain'],
                             sample['result'],
                             msg="execute_result contents didn't match string supplied")

    code_display_data = []

    def test_display_data(self):
        if not self.code_display_data:
            raise SkipTest

        for sample in self.code_display_data:
            self.flush_channels()
            reply, output_msgs = self.execute_helper(sample['code'])

            self.assertEqual(reply['content']['status'], 'ok',
                             msg="Expected execute_reply to have status=ok")

            self.assertGreaterEqual(len(output_msgs), 1,
                                    msg="Got no messages on iopub socket after code execution")
            self.assertEqual(output_msgs[0]['msg_type'], 'display_data',
                             msg="Expected a display_data message")
            self.assertIn(sample['mime'], output_msgs[0]['content']['data'],
                          msg="display_data didn't contain the expected MIME type")
