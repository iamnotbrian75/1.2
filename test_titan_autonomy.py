import importlib.util
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import titan_autonomy as autonomy
import titan_skills as skills

CORE_SPEC = importlib.util.spec_from_file_location("titan_autonomy_test_core", ROOT / "T.v1.1.py")
core = importlib.util.module_from_spec(CORE_SPEC)
CORE_SPEC.loader.exec_module(core)


def tool_call(command):
    return {"content": "", "tool_calls": [{"function": {"name": "do", "arguments": {"command": command}}}]}


class AutonomyTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        autonomy._reset_for_tests(Path(self.tmp.name) / "auto.json")
        self.executed = []

        def execute(command):
            self.executed.append(command)
            return f"done: {command}"

        autonomy.configure(execute, is_action=lambda t: core.is_titan_action(t))
        skills.pop_alerts()

    def tearDown(self):
        self.tmp.cleanup()

    def scripted(self, replies):
        """Fake model: returns the given messages in order, then a plain reply."""
        replies = list(replies)
        calls = []

        def fake_chat(messages, tools=None, **kwargs):
            calls.append((messages, tools))
            if tools is None:
                return {"content": "Checked reply."}
            return replies.pop(0) if replies else {"content": "All done."}

        return patch.object(autonomy, "_chat", side_effect=fake_chat), calls


class TaskTests(AutonomyTestCase):
    def test_task_chains_actions_then_self_checks(self):
        fake, calls = self.scripted([tool_call("set a timer for 25 minutes"), tool_call("open youtube")])
        with fake:
            reply, steps = autonomy.run_task("get me set up for revision")
        self.assertEqual(self.executed, ["set a timer for 25 minutes", "open youtube"])
        self.assertEqual(len(steps), 2)
        self.assertEqual(reply, "Checked reply.")            # self-check rewrote the draft
        self.assertIsNone(calls[-1][1])                     # last call was the self-check (no tools)

    def test_self_check_off_returns_draft(self):
        autonomy.handle("turn off self checking")
        fake, calls = self.scripted([tool_call("battery")])
        with fake:
            reply, _ = autonomy.run_task("check my battery")
        self.assertEqual(reply, "All done.")
        self.assertTrue(all(tools is not None for _, tools in calls))

    def test_summary_falls_back_to_action_results(self):
        autonomy.handle("turn off self checking")
        fake, _ = self.scripted([tool_call("battery"), {"content": "{}"}])
        with fake:
            reply, _ = autonomy.run_task("battery")
        self.assertEqual(reply, "All done. done: battery")

    def test_supervised_mode_limits_steps(self):
        fake, _ = self.scripted([tool_call(f"set a timer for {n} minutes") for n in range(1, 20)])
        with fake:
            _, steps = autonomy.run_task("lots of timers")
        self.assertEqual(len(steps), autonomy.SUPERVISED_ROUNDS)

    def test_autonomous_mode_allows_more_steps(self):
        autonomy.handle("switch to autonomous mode")
        fake, _ = self.scripted([tool_call(f"set a timer for {n} minutes") for n in range(1, 12)])
        with fake:
            _, steps = autonomy.run_task("lots of timers")
        self.assertEqual(len(steps), 11)

    def test_repeated_command_runs_once_and_ends_the_loop(self):
        fake, calls = self.scripted([tool_call("open youtube")] * 10)
        with fake:
            autonomy.run_task("open youtube a lot")
        self.assertEqual(self.executed, ["open youtube"])
        self.assertLessEqual(len(calls), 3)         # 2 planning rounds + self-check, not 10

    def test_text_tool_calls_are_understood(self):
        fake, _ = self.scripted([{"content": '{"name": "do", "arguments": {"command": "battery"}}'}])
        with fake:
            autonomy.run_task("battery please")
        self.assertEqual(self.executed, ["battery"])

    def test_malformed_tool_calls_from_real_model(self):
        # exact shapes qwen2.5-coder:3b produced in a live run
        content = ('{"name": "do", "arguments": {"command": {"type": "string", "description": "open youtube"}}}\n'
                   '{"name": "tell me the battery", "arguments": {}}')
        fake, _ = self.scripted([{"content": content}])
        with fake:
            reply, _ = autonomy.run_task("revision setup")
        self.assertEqual(self.executed, ["open youtube", "tell me the battery"])
        self.assertFalse(reply.lstrip().startswith("{"))

    def test_json_never_becomes_the_spoken_reply(self):
        autonomy.handle("turn off self checking")
        fake, _ = self.scripted([tool_call("battery"), {"content": '{"name": "do", "arguments": {}}'}])
        with fake:
            reply, _ = autonomy.run_task("battery")
        self.assertFalse(reply.lstrip().startswith("{"))

    def test_and_then_of_known_commands_runs_directly_without_ai(self):
        with patch.object(autonomy, "_chat", side_effect=AssertionError("should not call the model")):
            reply = autonomy.handle("check the battery and then tell me the time")
        self.assertEqual(self.executed, ["check the battery", "tell me the time"])
        self.assertIn("done: tell me the time", reply)

    def test_chained_request_needs_a_real_action(self):
        self.assertTrue(autonomy.is_task_request("set a timer for 10 minutes and then open youtube"))
        self.assertTrue(autonomy.is_task_request("task: tidy my evening"))
        self.assertTrue(autonomy.is_task_request("get me ready for revision"))
        self.assertFalse(autonomy.is_task_request("tell me a story and then explain the moral"))


class SafetyTests(unittest.TestCase):
    def test_blocked_actions_are_refused_in_tasks(self):
        with patch.object(core.skills, "run_skill") as run_skill:
            self.assertIn("Blocked", core.agent_execute("lock my computer"))
            self.assertIn("Blocked", core.agent_execute(r"find large files in C:\Users\User\Downloads"))
            run_skill.assert_not_called()

    def test_conversation_is_not_an_action(self):
        self.assertIn("isn't one of my actions", core.agent_execute("why is the sky blue"))

    def test_allowed_action_runs(self):
        self.assertIn(":", core.agent_execute("what time is it"))


class RoutineTests(AutonomyTestCase):
    def test_repeated_sequence_is_suggested_then_adopted_and_run(self):
        start = time.time() - 10 * 86400
        with patch.object(autonomy.time, "time") as clock:
            for day in range(3):
                for offset, (cmd, text) in enumerate([("weather", "what's the weather"), ("news", "news headlines")]):
                    clock.return_value = start + day * 86400 + offset * 30
                    autonomy.record_command(cmd, text)
        suggestions = autonomy.settings()["suggestions"]
        self.assertEqual(suggestions[0]["commands"], ["weather", "news"])
        self.assertIn("adopt routine 1", autonomy.heartbeat_item())
        reply = autonomy.handle("adopt routine 1 as morning")
        self.assertIn("morning", reply)
        self.assertEqual(autonomy.settings()["suggestions"], [])
        autonomy.handle("/morning")
        self.assertEqual(self.executed, ["what's the weather", "news headlines"])

    def test_two_repeats_is_not_enough(self):
        start = time.time() - 10 * 86400
        with patch.object(autonomy.time, "time") as clock:
            for day in range(2):
                for offset, cmd in enumerate(["weather", "news"]):
                    clock.return_value = start + day * 86400 + offset * 30
                    autonomy.record_command(cmd, cmd)
        self.assertEqual(autonomy.settings()["suggestions"], [])

    def test_forget_and_stop_learning(self):
        autonomy.record_command("weather", "weather")
        autonomy.handle("forget my routines")
        self.assertEqual(autonomy.settings()["usage"], [])
        autonomy.handle("stop learning routines")
        autonomy.record_command("weather", "weather")
        self.assertEqual(autonomy.settings()["usage"], [])


class ScheduleTests(AutonomyTestCase):
    def test_standing_goal_saved_listed_and_cancelled(self):
        reply = autonomy.handle("every morning at 7:30am check the weather and the news")
        self.assertIn("07:30", reply)
        goal = autonomy.settings()["goals"][0]
        self.assertEqual((goal["time"], goal["task"]), ("07:30", "check the weather and the news"))
        self.assertIn("07:30", autonomy.handle("list my goals"))
        autonomy.handle("cancel goal 1")
        self.assertEqual(autonomy.settings()["goals"], [])

    def test_briefing_delivered_once_per_day_after_its_time(self):
        autonomy.handle("set my briefing for 23:59")
        with patch.object(autonomy, "briefing", return_value="Your briefing."):
            autonomy.scheduled_check(datetime.now().replace(hour=8, minute=0))
            self.assertEqual(skills.pop_alerts(), [])
            autonomy.scheduled_check(datetime.now().replace(hour=23, minute=59))
            autonomy.scheduled_check(datetime.now().replace(hour=23, minute=59))
        alerts = skills.pop_alerts()
        self.assertEqual([a["text"] for a in alerts], ["Your briefing."])


class WatchdogTests(AutonomyTestCase):
    def fake_psutil(self, battery=50, plugged=False, mem=40):
        return SimpleNamespace(
            sensors_battery=lambda: SimpleNamespace(percent=battery, power_plugged=plugged),
            virtual_memory=lambda: SimpleNamespace(percent=mem),
            cpu_percent=lambda interval=None: 10,
            disk_usage=lambda path: SimpleNamespace(free=100 * 1024 ** 3, total=200 * 1024 ** 3),
            process_iter=lambda attrs: [],
            boot_time=lambda: time.time(),
        )

    def test_low_battery_warns_once(self):
        autonomy._watch["warned"].clear()
        with patch.object(autonomy, "psutil", self.fake_psutil(battery=15)), \
                patch.object(autonomy, "_ollama_up", return_value=True):
            autonomy.watchdog_check()
            autonomy.watchdog_check()
        alerts = skills.pop_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("15 percent", alerts[0]["text"])

    def test_watchdog_off_is_silent(self):
        autonomy._watch["warned"].clear()
        autonomy.handle("turn off the watchdog")
        with patch.object(autonomy, "psutil", self.fake_psutil(battery=5)):
            autonomy.watchdog_check()
        self.assertEqual(skills.pop_alerts(), [])


if __name__ == "__main__":
    unittest.main()
