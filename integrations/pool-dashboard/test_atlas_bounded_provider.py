import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import test_dashboard
from atlas.provider import BoundedMessages,ProviderError

class BoundedProviderTests(unittest.TestCase):
    def make(self,count):
        p=object.__new__(BoundedMessages)
        p.rates={'claude-opus-5-5':{'input':4,'output':20}}
        p.client=Mock();p.client.messages.count_tokens.return_value=SimpleNamespace(input_tokens=count)
        return p
    def test_insufficient_budget_never_submits(self):
        p=self.make(100000);save=Mock()
        with self.assertRaisesRegex(ProviderError,'NO_INFERENCE'):p.run('ATLAS',{},1,save)
        p.client.messages.stream.assert_not_called();save.assert_not_called()
    def test_hard_output_cap_and_conservative_usage(self):
        p=self.make(1000)
        response=SimpleNamespace(id='msg_fixture',stop_reason='end_turn',content=[SimpleNamespace(type='text',text='{}')],usage=Mock())
        response.usage.model_dump.return_value={'input_tokens':1000,'output_tokens':1000}
        p.client.messages.stream.return_value.__enter__=Mock(return_value=SimpleNamespace(get_final_message=lambda:response))
        p.client.messages.stream.return_value.__exit__=Mock(return_value=False)
        result=p.run('ATLAS',{},20,Mock())
        kwargs=p.client.messages.stream.call_args.kwargs
        self.assertLessEqual(kwargs['max_tokens']*20/10000+(1000*1.1+1024)*4/10000,20)
        self.assertNotIn('tools',kwargs);self.assertNotIn('cache_control',kwargs)
        self.assertEqual(result['usage']['list_cost']['amount'],'3')
