"""Молчание модели: когда повторять попытку, а когда нет.

Модель в отделе одна на всех, и очередь к ней держит сам llama-server. Если
ответа не дождались — значит, она занята: повторять тот же запрос бессмысленно
и вредно, очередь от этого только длиннее. А человек всё это время не видит
ничего: при трёх попытках по пятнадцать минут беда доходила до него через
СОРОК ПЯТЬ минут молчания в браузере.

Связи нет — другое дело: сервер мог только подниматься, и повторить стоит.
"""

import unittest
import urllib.error

import _bootstrap  # noqa: F401

from reportgen.llm import LLMError, OpenAICompatLLM, _timed_out


class KindTests(unittest.TestCase):
    def test_a_timeout_is_recognised(self):
        self.assertTrue(_timed_out(TimeoutError("вышло время")))

    def test_a_timeout_inside_urlerror_is_recognised(self):
        self.assertTrue(_timed_out(urllib.error.URLError(TimeoutError())))

    def test_a_refusal_is_not_a_timeout(self):
        self.assertFalse(_timed_out(ConnectionRefusedError(111, "refused")))
        self.assertFalse(_timed_out(
            urllib.error.URLError(ConnectionRefusedError(111, "refused"))))


class RetryTests(unittest.TestCase):
    """Считаем попытки: сколько раз система пошла к модели."""

    def calls(self, error: BaseException) -> int:
        from reportgen import llm as module

        tries = []

        def broken(request, timeout=None):
            tries.append(timeout)
            raise error

        original = module._http.urlopen
        module._http.urlopen = broken
        try:
            model = OpenAICompatLLM(base_url="http://127.0.0.1:8000/v1",
                                    model="test", retries=3, timeout=1)
            with self.assertRaises(LLMError):
                model.complete("система", "вопрос")
        finally:
            module._http.urlopen = original
        return len(tries)

    def test_a_timeout_is_not_repeated(self):
        """Второй такой же запрос удлиняет очередь отдела, а не помогает."""
        self.assertEqual(1, self.calls(TimeoutError("вышло время")))

    def test_a_timeout_inside_urlerror_is_not_repeated(self):
        self.assertEqual(1, self.calls(urllib.error.URLError(TimeoutError())))

    def test_a_refused_connection_is_retried(self):
        """Служба могла только подниматься — повторить стоит."""
        self.assertEqual(3, self.calls(
            urllib.error.URLError(ConnectionRefusedError(111, "refused"))))


if __name__ == "__main__":
    unittest.main()
