"""Machinery for testing Jupyter kernels via the messaging protocol.
"""

from unittest import TestCase, SkipTest
try:                  # Python 3
    from queue import Empty
except ImportError:   # Python 2
    from Queue import Empty

from jupyter_client.manager import start_new_kernel
from .messagespec import validate_message, MimeBundle

import pprint

TIMEOUT = 15

__version__ = '0.1'


# patching functions used to implement show_traffic
def patch_get(f, channel):
    """
    Monkey patch KernelClient.(shell|iopub)_channel.get_msg to pprint (a
    subset) of sent shell messages (just the `content` and `msg_type`, and add
    a channel name). These responses are indented 20 chars compared to sent
    messages to distinguish them.
    """
    def get_msg_pprint(*args, **kwargs):
        msg = f(*args, **kwargs)
        disp_msg = {'content': msg.get('content', ''),
                    'msg_type': msg.get('msg_type', ''),
                    'channel': channel}
        text = pprint.pformat(disp_msg, width=60, depth=5)
        print('\n'.join((' '*20) + t for t in text.split('\n')))
        print('-'*80)
        return msg
    return get_msg_pprint

def patch_send(f):
    """
    Monkey patch KernelClient.shell_channel.send_msg to pprint sent messages.
    """
    def send_pprint(msg, *args, **kwargs):
        disp_msg = {'content': msg.get('content', ''),
                    'msg_type': msg.get('msg_type', '')}
        pprint.pprint(disp_msg, width=60, depth=5)
        print('-'*80)
        return f(msg, *args, **kwargs)
    return send_pprint

class KernelTests(TestCase):
    kernel_name = ""
    show_traffic = False

    @classmethod
    def setUpClass(cls):
        cls.km, cls.kc = start_new_kernel(kernel_name=cls.kernel_name)
        if cls.show_traffic:
            # patch the shell and iopub channels to pprint the messages
            cls.kc.shell_channel.get_msg = patch_get(cls.kc.shell_channel.get_msg,
                                                     "shell")
            cls.kc.iopub_channel.get_msg = patch_get(cls.kc.iopub_channel.get_msg,
                                                     "iopub")
            cls.kc.shell_channel.send = patch_send(cls.kc.shell_channel.send)


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
                             self.language_name)

    def execute_helper(self, code, timeout=TIMEOUT):
        msg_id = self.kc.execute(code=code)

        reply = self.kc.get_shell_msg(timeout=timeout)
        validate_message(reply, 'execute_reply', msg_id)

        busy_msg = self.kc.iopub_channel.get_msg(timeout=1)
        validate_message(busy_msg, 'status', msg_id)
        self.assertEqual(busy_msg['content']['execution_state'], 'busy')

        output_msgs = []
        while True:
            msg = self.kc.iopub_channel.get_msg(timeout=0.1)
            validate_message(msg, msg['msg_type'], msg_id)
            if msg['msg_type'] == 'status':
                self.assertEqual(msg['content']['execution_state'], 'idle')
                break
            elif msg['msg_type'] == 'execute_input':
                self.assertEqual(msg['content']['code'], code)
                continue
            output_msgs.append(msg)

        return reply, output_msgs

    code_hello_world = ""

    def test_execute_stdout(self):
        if not self.code_hello_world:
            raise SkipTest

        self.flush_channels()
        reply, output_msgs = self.execute_helper(code=self.code_hello_world)

        self.assertEqual(reply['content']['status'], 'ok')

        self.assertGreaterEqual(len(output_msgs), 1)
        self.assertEqual(output_msgs[0]['msg_type'], 'stream')
        self.assertEqual(output_msgs[0]['content']['name'], 'stdout')
        self.assertIn('hello, world', output_msgs[0]['content']['text'])

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
                                 set(sample['matches']))

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
        self.assertEqual(reply['content']['status'],  'ok')
        payloads = reply['content']['payload']
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]['source'], 'page')
        mimebundle = payloads[0]['data']
        # Validate the mimebundle
        MimeBundle().data = mimebundle
        self.assertIn('text/plain', mimebundle)

    code_generate_error = ""

    def test_error(self):
        if not self.code_generate_error:
            raise SkipTest

        self.flush_channels()

        reply, output_msgs = self.execute_helper(self.code_generate_error)
        self.assertEqual(reply['content']['status'], 'error')

    code_execute_result = []

    def test_execute_result(self):
        if not self.code_execute_result:
            raise SkipTest

        for sample in self.code_execute_result:
            self.flush_channels()

            reply, output_msgs = self.execute_helper(sample['code'])

            self.assertEqual(reply['content']['status'], 'ok')

            self.assertGreaterEqual(len(output_msgs), 1)
            self.assertEqual(output_msgs[0]['msg_type'], 'execute_result')
            self.assertIn('text/plain', output_msgs[0]['content']['data'])
            self.assertEqual(output_msgs[0]['content']['data']['text/plain'],
                             sample['result'])

    code_display_data = []

    def test_display_data(self):
        if not self.code_display_data:
            raise SkipTest

        for sample in self.code_display_data:
            self.flush_channels()
            reply, output_msgs = self.execute_helper(sample['code'])

            self.assertEqual(reply['content']['status'], 'ok')

            self.assertGreaterEqual(len(output_msgs), 1)
            self.assertEqual(output_msgs[0]['msg_type'], 'display_data')
            self.assertIn(sample['mime'], output_msgs[0]['content']['data'])
