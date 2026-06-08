import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from paicli_py.runtime import (
    DurableTaskManager,
    RuntimeApiServer,
    RuntimeThreadStore,
    RuntimeTurn,
    TaskStatus,
    handle_task_command,
)


class RuntimeTest(unittest.TestCase):
    def test_durable_task_manager_runs_and_persists_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = DurableTaskManager(Path(tmp) / "tasks.db", lambda prompt: f"done:{prompt}", worker_count=1)
            try:
                manager.start()
                task = manager.enqueue("hello")
                completed = self._wait_for_task(manager, task.id)
                self.assertEqual(TaskStatus.COMPLETED, completed.status)
                self.assertEqual("done:hello", completed.result)
                reopened = DurableTaskManager(Path(tmp) / "tasks.db", lambda prompt: prompt, worker_count=1)
                try:
                    self.assertEqual(TaskStatus.COMPLETED, reopened.find(task.id).status)
                finally:
                    reopened.close()
            finally:
                manager.close()

    def test_task_command_formatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = DurableTaskManager(Path(tmp) / "tasks.db", lambda prompt: f"ok {prompt}", worker_count=1)
            try:
                manager.start()
                submitted = handle_task_command(manager, "add sample")
                self.assertIn("background task submitted", submitted)
                task_id = submitted.split()[3]
                completed = self._wait_for_task(manager, task_id)
                self.assertIn(task_id, handle_task_command(manager, "list"))
                self.assertIn(completed.result, handle_task_command(manager, f"log {task_id}"))
            finally:
                manager.close()

    def test_runtime_thread_store_and_sse(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeThreadStore(Path(tmp) / "runtime.db")
            try:
                thread_id = store.create_thread()
                event_id = store.append_event(thread_id, "message.delta", '{"content":"hi"}')
                events = store.events(thread_id, 0)
                self.assertEqual(["thread.created", "message.delta"], [event.type for event in events])
                self.assertEqual([], store.events(thread_id, event_id))
            finally:
                store.close()

    def test_runtime_api_auth_threads_turns_and_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeThreadStore(Path(tmp) / "runtime.db")
            server = RuntimeApiServer(store, lambda value: f"echo:{value}", api_key="secret")
            try:
                server.start()
                base = f"http://127.0.0.1:{server.port}"
                unauthorized = self._request(base + "/v1/threads", method="POST", expect_error=True)
                self.assertEqual(401, unauthorized)
                created = self._json_request(base + "/v1/threads", method="POST", api_key="secret")
                thread_id = created["id"]
                turn = self._json_request(
                    base + f"/v1/threads/{thread_id}/turns",
                    method="POST",
                    api_key="secret",
                    payload={"input": "hello"},
                )
                self.assertEqual("running", turn["status"])
                body = self._wait_for_events(base, thread_id, "secret")
                self.assertIn("event: turn.completed", body)
                self.assertIn("echo:hello", body)
            finally:
                server.close()
                store.close()


    def test_runtime_api_cancel_running_turn(self):
        started = []

        def runner(value, token):
            started.append(value)
            for _ in range(50):
                token.throw_if_canceled()
                time.sleep(0.02)
            return "too late"

        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeThreadStore(Path(tmp) / "runtime.db")
            server = RuntimeApiServer(store, runner, api_key="secret")
            try:
                server.start()
                base = f"http://127.0.0.1:{server.port}"
                thread_id = self._json_request(base + "/v1/threads", method="POST", api_key="secret")["id"]
                turn = self._json_request(
                    base + f"/v1/threads/{thread_id}/turns",
                    method="POST",
                    api_key="secret",
                    payload={"input": "slow"},
                )
                canceled = self._json_request(
                    base + f"/v1/threads/{thread_id}/turns/{turn['id']}/cancel",
                    method="POST",
                    api_key="secret",
                )
                self.assertEqual("canceling", canceled["status"])
                body = self._wait_for_events(base, thread_id, "secret", "turn.canceled")
                self.assertIn("event: turn.canceled", body)
                self.assertEqual("canceled", store.find_turn(turn["id"]).status)
                self.assertEqual(["slow"], started)
            finally:
                server.close()
                store.close()

    def test_runtime_store_records_turn_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeThreadStore(Path(tmp) / "runtime.db")
            try:
                thread_id = store.create_thread()
                turn = store.create_turn(thread_id, "hello")
                self.assertIsInstance(turn, RuntimeTurn)
                self.assertEqual("running", store.find_turn(turn.id).status)
                store.mark_turn_terminal(turn.id, "completed", result="ok")
                self.assertEqual("completed", store.find_turn(turn.id).status)
                self.assertEqual("ok", store.find_turn(turn.id).result)
            finally:
                store.close()


    def test_runtime_api_streams_generator_chunks(self):
        def runner(value, token):
            yield "one:"
            token.throw_if_canceled()
            yield value

        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeThreadStore(Path(tmp) / "runtime.db")
            server = RuntimeApiServer(store, runner, api_key="secret")
            try:
                server.start()
                base = f"http://127.0.0.1:{server.port}"
                thread_id = self._json_request(base + "/v1/threads", method="POST", api_key="secret")["id"]
                turn = self._json_request(
                    base + f"/v1/threads/{thread_id}/turns",
                    method="POST",
                    api_key="secret",
                    payload={"input": "two"},
                )
                body = self._wait_for_events(base, thread_id, "secret", "turn.completed")
                self.assertGreaterEqual(body.count("event: message.delta"), 2)
                self.assertIn("one:", body)
                self.assertIn("two", body)
                self.assertEqual("one:two", store.find_turn(turn["id"]).result)
            finally:
                server.close()
                store.close()

    def test_runtime_api_streams_emit_callback_chunks(self):
        def runner(value, token, emit):
            emit("alpha")
            token.throw_if_canceled()
            emit(value)
            return "alphabeta"

        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeThreadStore(Path(tmp) / "runtime.db")
            server = RuntimeApiServer(store, runner, api_key="secret")
            try:
                server.start()
                base = f"http://127.0.0.1:{server.port}"
                thread_id = self._json_request(base + "/v1/threads", method="POST", api_key="secret")["id"]
                turn = self._json_request(
                    base + f"/v1/threads/{thread_id}/turns",
                    method="POST",
                    api_key="secret",
                    payload={"input": "beta"},
                )
                body = self._wait_for_events(base, thread_id, "secret", "turn.completed")
                self.assertEqual(2, body.count("event: message.delta"))
                self.assertEqual("alphabeta", store.find_turn(turn["id"]).result)
            finally:
                server.close()
                store.close()

    def _wait_for_task(self, manager, task_id):
        for _ in range(50):
            task = manager.find(task_id)
            if task and task.terminal:
                return task
            time.sleep(0.05)
        self.fail("task did not finish")

    def _json_request(self, url, method="GET", api_key="", payload=None):
        raw = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        request = Request(url, data=raw, method=method)
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")
        if raw is not None:
            request.add_header("Content-Type", "application/json")
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _request(self, url, method="GET", expect_error=False):
        request = Request(url, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status
        except Exception as exc:
            if expect_error and hasattr(exc, "code"):
                return exc.code
            raise

    def _wait_for_events(self, base, thread_id, api_key, marker="turn.completed"):
        request = Request(base + f"/v1/threads/{thread_id}/events")
        request.add_header("X-PaiCLI-API-Key", api_key)
        for _ in range(50):
            with urlopen(request, timeout=5) as response:
                body = response.read().decode("utf-8")
            if marker in body:
                return body
            time.sleep(0.05)
        self.fail("events did not arrive")


if __name__ == "__main__":
    unittest.main()
