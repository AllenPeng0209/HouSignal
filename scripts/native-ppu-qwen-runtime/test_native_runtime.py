"""Observable contract tests. Only the costly model computation is replaced."""
import importlib.util
import contextlib
from types import SimpleNamespace
from unittest.mock import patch
import json
from pathlib import Path
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).parent

class ContractTests(unittest.TestCase):
    def setUp(self):
        target = ROOT / "native_runtime.py"
        self.assertTrue(target.exists(), "Standalone runtime implementation is missing")
        spec = importlib.util.spec_from_file_location("native_runtime", target)
        self.module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)

    def test_string_chat_is_accepted_but_model_switch_and_tools_are_rejected(self):
        valid = {"model":"fixed-model", "messages":[{"role":"user","content":"写一句话"}], "max_tokens":32}
        request = self.module.validate_request(valid, "fixed-model")
        self.assertEqual(request.messages[0]["content"], "写一句话")
        try: long_request = self.module.validate_request({**valid, "max_tokens":6000}, "fixed-model")
        except self.module.RequestError: self.fail("Required 6000-token generation budget is rejected")
        self.assertEqual(long_request.max_tokens, 6000)
        for change in [{"model":"another-model"}, {"tools":[]}, {"messages":[{"role":"user","content":[{"type":"image_url","image_url":{"url":"file:///tmp/x"}}]}]}, {"messages":[{"role":"tool","content":"x"}]}, {"max_tokens":0}, {"max_tokens":True}, {"max_tokens":6001}, {"messages":[]}, {"temperature":float("nan")}, {"stream":True}, {"n":2}]:
            with self.subTest(change=change), self.assertRaises(self.module.RequestError):
                self.module.validate_request({**valid, **change}, "fixed-model")

    def test_auth_and_model_identity_and_usage_are_consumer_visible(self):
        m = self.module
        class Backend:
            parameter_count = 33357390064
            decoder_layers = 64
            identity_scope = "local metadata; not upstream commit"
            def generate(self, request, control):
                return m.Generation("实际结果", 17, 3, "stop")
        server = m.create_server(Backend(), "private-test-token", "fixed-model", "manifest-abc", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(url + "/v1/models")
            self.assertEqual(ctx.exception.code, 401)
            ctx.exception.close()
            models = self.call(url, "/v1/models")
            self.assertEqual(models["data"][0]["id"], "fixed-model")
            health = self.call(url, "/health")
            self.assertEqual(health["manifest_id"], "manifest-abc")
            self.assertEqual(health.get("decoder_layers"),64)
            self.assertEqual(health.get("parameter_count"),33357390064)
            self.assertEqual(health.get("max_output_tokens"),6000)
            result = self.call(url, "/v1/chat/completions", {"model":"fixed-model","messages":[{"role":"user","content":"x"}]})
            self.assertEqual(result["model"], "fixed-model")
            self.assertEqual(result["choices"][0]["message"]["content"], "实际结果")
            self.assertEqual(result["usage"], {"prompt_tokens":17,"completion_tokens":3,"total_tokens":20})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.call(url, "/v1/chat/completions", {"model":"other","messages":[{"role":"user","content":"x"}]})
            self.assertEqual(ctx.exception.code, 400)
            ctx.exception.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_busy_request_is_rejected_and_client_can_cancel_active_computation(self):
        m = self.module
        entered = threading.Event()
        class Backend:
            def generate(self, request, control):
                entered.set()
                while not control.should_stop():
                    time.sleep(0.005)
                raise m.RequestError("generation_cancelled", "cancelled", 499)
        server = m.create_server(Backend(), "private-test-token", "fixed-model", "manifest-abc", port=0, timeout_seconds=1)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        statuses = []
        def first():
            try: self.call(url, "/v1/chat/completions", {"model":"fixed-model","request_id":"test-cancel","messages":[{"role":"user","content":"x"}]})
            except urllib.error.HTTPError as exc: statuses.append(exc.code); exc.close()
        request_thread = threading.Thread(target=first); request_thread.start()
        try:
            self.assertTrue(entered.wait(0.5))
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.call(url, "/v1/chat/completions", {"model":"fixed-model","messages":[{"role":"user","content":"x"}]})
            self.assertEqual(ctx.exception.code, 429)
            self.assertNotIn("usage",json.load(ctx.exception))
            ctx.exception.close()
            cancelled = self.call(url, "/v1/cancel", {"request_id":"test-cancel"})
            self.assertTrue(cancelled["cancelled"])
            request_thread.join(1)
            self.assertEqual(statuses, [499])
        finally:
            server.shutdown(); server.server_close(); thread.join(); request_thread.join()

    def test_deadline_returns_timeout_and_releases_single_concurrency(self):
        m = self.module
        class Backend:
            def generate(self, request, control):
                while not control.should_stop(): time.sleep(0.005)
                raise m.RequestError("generation_timeout", "timeout", 504)
        server = m.create_server(Backend(), "private-test-token", "fixed-model", "manifest-abc", port=0, timeout_seconds=0.1)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            started = time.monotonic()
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.call(url, "/v1/chat/completions", {"model":"fixed-model","messages":[{"role":"user","content":"x"}]})
            self.assertEqual(ctx.exception.code,504)
            self.assertEqual(json.load(ctx.exception)["error"]["code"],"generation_timeout")
            ctx.exception.close()
            self.assertLess(time.monotonic()-started,0.5)
            deadline = time.monotonic()+0.5
            while self.call(url,"/health")["busy"] and time.monotonic()<deadline: time.sleep(0.005)
            self.assertFalse(self.call(url,"/health")["busy"])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_failure_after_generation_keeps_actual_usage_and_length_keeps_tokens(self):
        m = self.module
        self.assertTrue(callable(getattr(m,"completion_result",None)),"Generated-token failure receipt helper is missing")
        for reason,content,status in [("timeout","partial",504),("cancelled","partial",499),(None,"",503)]:
            control=m.GenerationControl("receipt",time.monotonic()+10)
            control.reason=reason
            with self.subTest(reason=reason),self.assertRaises(m.RequestError) as ctx:
                m.completion_result(content,17,5,control,5)
            self.assertEqual(ctx.exception.status,status)
            self.assertEqual(ctx.exception.usage,{"prompt_tokens":17,"completion_tokens":5,"total_tokens":22})
            self.assertEqual(ctx.exception.usage_status,"known")
        complete=m.completion_result("complete",17,5,m.GenerationControl("length",time.monotonic()+10),5)
        self.assertEqual(complete.finish_reason,"length")
        self.assertEqual(complete.completion_tokens,5)

    def test_timeout_waits_for_actual_output_and_transports_receipt(self):
        m=self.module
        class Backend:
            def generate(self,request,control):
                while not control.should_stop():time.sleep(.002)
                time.sleep(.01)
                raise m.RequestError("generation_timeout","timeout",504,usage={"prompt_tokens":17,"completion_tokens":5,"total_tokens":22})
        server=m.create_server(Backend(),"private-test-token","fixed-model","manifest-abc",port=0,timeout_seconds=.3)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f"http://127.0.0.1:{server.server_port}"
        try:
            started=time.monotonic()
            with self.assertRaises(urllib.error.HTTPError) as ctx:self.call(url,"/v1/chat/completions",{"model":"fixed-model","messages":[{"role":"user","content":"x"}]})
            error=json.load(ctx.exception);ctx.exception.close()
            self.assertEqual(ctx.exception.code,504)
            self.assertEqual(error.get("usage"),{"prompt_tokens":17,"completion_tokens":5,"total_tokens":22})
            self.assertEqual(error.get("usage_status"),"known")
            self.assertEqual(error.get("model"),"fixed-model")
            self.assertEqual(error.get("system_fingerprint"),"manifest-abc")
            self.assertLess(time.monotonic()-started,.5)
        finally:server.shutdown();server.server_close();thread.join()

    def test_validation_has_no_fake_usage_and_inference_failure_is_unknown_and_redacted(self):
        m=self.module
        class Backend:
            def generate(self,request,control):raise RuntimeError("SECRET private-test-token must never be emitted")
        server=m.create_server(Backend(),"private-test-token","fixed-model","manifest-abc",port=0,timeout_seconds=.3)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f"http://127.0.0.1:{server.server_port}"
        try:
            for body,status in [({"model":"other","messages":[{"role":"user","content":"x"}]},400),({"model":"fixed-model","messages":[{"role":"user","content":"x"*131072}]},413),({"model":"fixed-model","messages":[{"role":"user","content":"x"}]},503)]:
                with self.subTest(status=status),self.assertRaises(urllib.error.HTTPError) as ctx:self.call(url,"/v1/chat/completions",body)
                error=json.load(ctx.exception);ctx.exception.close()
                self.assertEqual(ctx.exception.code,status)
                self.assertNotIn("private-test-token",json.dumps(error))
                if status!=503:self.assertNotIn("usage",error)
                else:
                    self.assertEqual(error.get("usage_status"),"unknown")
                    self.assertIn("usage",error)
                    self.assertIsNone(error["usage"])
        finally:server.shutdown();server.server_close();thread.join()

    def test_hard_deadline_keeps_usage_unknown_and_watchdog_targets_owned_runtime(self):
        m=self.module
        watchdog_fired=threading.Event();codes=[]
        class Backend:
            def generate(self,request,control):
                time.sleep(.25)
                return m.Generation("late",17,5,"stop")
        def owned_exit(code):codes.append(code);watchdog_fired.set()
        server=m.create_server(Backend(),"private-test-token","fixed-model","manifest-abc",port=0,timeout_seconds=.1,hard_exit=owned_exit)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f"http://127.0.0.1:{server.server_port}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:self.call(url,"/v1/chat/completions",{"model":"fixed-model","messages":[{"role":"user","content":"x"}]})
            error=json.load(ctx.exception);ctx.exception.close()
            self.assertEqual(ctx.exception.code,504)
            self.assertEqual(error.get("usage_status"),"unknown")
            self.assertIn("usage",error);self.assertIsNone(error["usage"])
            self.assertTrue(watchdog_fired.wait(.2));self.assertEqual(codes,[75])
        finally:server.shutdown();server.server_close();thread.join()

    def test_malformed_role_is_a_safe_request_error_instead_of_a_type_error(self):
        body={"model":"fixed-model","messages":[{"role":{},"content":"x"}]}
        try:self.module.validate_request(body,"fixed-model")
        except self.module.RequestError as error:self.assertEqual(error.status,400)
        except Exception as error:self.fail("Malformed role escaped request validation: "+type(error).__name__)
        else:self.fail("Malformed role was accepted")

    def test_generated_eos_at_limit_reports_stop_with_unchanged_real_usage(self):
        m=self.module
        class InputIds:
            shape=(1,7)
        class Inputs(dict):
            def __init__(self):super().__init__(input_ids=InputIds())
            def to(self,device):return self
        class Generated:
            def __init__(self,values):self.values=values;self.shape=(len(values),)
            def __getitem__(self,index):return self.values[index]
            def tolist(self):return self.values
        class Output:
            def __init__(self,values):self.values=values
            def __getitem__(self,index):
                assert index[0]==0 and index[1].start==7
                return Generated(self.values)
        class Tokenizer:
            def __init__(self,eos):self.eos_token_id=eos
            def apply_chat_template(self,*args,**kwargs):return Inputs()
            def decode(self,generated,**kwargs):return "正文1正文2"
        class Model:
            def __init__(self,values):self.values=values
            def generate(self,**kwargs):return Output(self.values)
        transformer_double=SimpleNamespace(StoppingCriteria=object,StoppingCriteriaList=list)
        request=m.ChatRequest([{"role":"user","content":"x"}],3,0,"eos-boundary")
        for values,eos,expected in [([10,11,99],99,"stop"),([10,11,99],[98,99],"stop"),([10,11,12],99,"length")]:
            with self.subTest(values=values,eos=eos),patch.dict("sys.modules",{"transformers":transformer_double}):
                backend=object.__new__(m.TransformersBackend)
                backend.torch=SimpleNamespace(inference_mode=contextlib.nullcontext)
                backend.tokenizer=Tokenizer(eos);backend.model=Model(values)
                result=backend.generate(request,m.GenerationControl("eos",time.monotonic()+10))
                self.assertEqual(result.finish_reason,expected)
                self.assertEqual(result.prompt_tokens,7);self.assertEqual(result.completion_tokens,3)
                control=m.GenerationControl("cancelled-eos",time.monotonic()+10);control.cancelled.set()
                with self.assertRaises(m.RequestError) as ctx:backend.generate(request,control)
                self.assertEqual(ctx.exception.status,499)
                self.assertEqual(ctx.exception.usage,{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10})

    def test_generation_control_enforces_deadline(self):
        control = self.module.GenerationControl("deadline", time.monotonic() - 1)
        self.assertTrue(control.should_stop())
        self.assertEqual(control.reason, "timeout")

    def call(self, url, route, body=None):
        request = urllib.request.Request(url + route, data=None if body is None else json.dumps(body).encode(), headers={"Authorization":"Bearer private-test-token", "Content-Type":"application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

if __name__ == "__main__": unittest.main()
