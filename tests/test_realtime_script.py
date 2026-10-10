"""Offline tests using the real SDK with simulated audio and socket I/O."""

import base64
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_1 as script


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.args = script.build_parser().parse_args([])
        self.audio = Mock()
        self.input_stream = Mock()
        self.input_stream.read.side_effect = KeyboardInterrupt
        self.output_stream = Mock()
        self.audio.open.side_effect = [self.output_stream, self.input_stream]
        self.messages = []
        self.conversation = None
        self.callback = None
        self.configure_error = None
        self.connect_error = None
        self.send_error = None
        self.sdk_class = script.OmniRealtimeConversation
        self.factory = Mock(side_effect=self.create_conversation)
        self.start_patch(patch.object(script.pyaudio, "PyAudio", return_value=self.audio))
        self.start_patch(patch.object(script, "OmniRealtimeConversation", self.factory))
        self.start_patch(patch.object(script, "LOGGER"))

    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def create_conversation(self, **kwargs):
        self.callback = kwargs["callback"]
        self.conversation = self.sdk_class(**kwargs)
        self.conversation.ws = SimpleNamespace(
            sock=SimpleNamespace(connected=True),
            send=Mock(side_effect=self.on_send),
            close=Mock(side_effect=self.on_close),
        )
        self.conversation.connect = Mock(side_effect=self.on_connect)
        return self.conversation

    def on_connect(self):
        if self.connect_error:
            raise self.connect_error
        self.callback.on_open()

    def on_close(self):
        self.conversation.ws.sock.connected = False
        self.conversation._on_close(self.conversation.ws, 1000, "test close")

    def on_send(self, data):
        event = json.loads(data)
        self.messages.append(event)
        if event["type"] == "session.update":
            self.input_stream.start_stream.assert_not_called()
            if self.configure_error:
                self.callback.on_event({"type": "error", "error": self.configure_error})
            else:
                self.callback.on_event({"type": "session.updated"})
        elif event["type"] == "input_audio_buffer.append" and self.send_error:
            raise self.send_error
        elif event["type"] == "session.finish":
            self.callback.on_event({
                "type": "response.audio.delta",
                "delta": base64.b64encode(b"\x01\x00" * 10).decode("ascii"),
            })
            self.conversation._on_message(
                self.conversation.ws, json.dumps({"type": "session.finished"}),
            )

    def run_script(self):
        script.run_translation(self.args, "test-only-key")

    def assert_player_stopped(self):
        self.assertFalse(any(
            t.name in {"audio-player", "input-monitor-player"}
            for t in threading.enumerate()
        ))

    def assert_resources_closed(self):
        self.input_stream.close.assert_called_once_with()
        self.output_stream.close.assert_called_once_with()
        self.audio.terminate.assert_called_once_with()
        if self.conversation is not None:
            self.conversation.ws.close.assert_called_once_with()
        self.assert_player_stopped()

    def test_close_callback_matches_sdk_and_late_open_does_not_reset_it(self):
        callback = script.RealtimeCallback()
        conversation = self.sdk_class(model="test", callback=callback, api_key="test")
        conversation._on_close(None, 1008, "configuration rejected")
        callback.on_open()
        self.assertTrue(callback.stopped.is_set())
        with self.assertRaisesRegex(RuntimeError, "1008.*configuration rejected"):
            callback.wait_until_ready()

    def test_server_error_stops_before_recording_or_sending(self):
        self.configure_error = {"code": "invalid_voice", "message": "voice unavailable"}
        with self.assertRaisesRegex(RuntimeError, "invalid_voice"):
            self.run_script()
        self.input_stream.start_stream.assert_not_called()
        self.input_stream.read.assert_not_called()
        self.assertEqual([e["type"] for e in self.messages], ["session.update"])
        self.assert_resources_closed()

    def test_session_configuration_timeout_closes_resources(self):
        with patch.object(self, "on_send"), patch.object(script, "SESSION_TIMEOUT", 0):
            with self.assertRaises(TimeoutError):
                self.run_script()
        self.input_stream.start_stream.assert_not_called()
        self.assert_resources_closed()

    def test_failed_connect_closes_partially_created_socket(self):
        self.connect_error = TimeoutError("connect timeout")
        with self.assertRaisesRegex(TimeoutError, "connect timeout"):
            self.run_script()
        self.assert_resources_closed()

    def test_closed_during_read_does_not_send_another_frame(self):
        def read(*args, **kwargs):
            self.on_close()
            return b"\x00\x00" * script.INPUT_FRAMES

        self.input_stream.read.side_effect = read
        with self.assertRaisesRegex(RuntimeError, "test close"):
            self.run_script()
        self.assertEqual([e["type"] for e in self.messages], ["session.update"])
        self.assert_resources_closed()

    def test_sdk_disconnected_socket_error_returns_failure_without_traceback(self):
        def read(*args, **kwargs):
            self.conversation.ws.sock.connected = False
            return b"\x00\x00" * script.INPUT_FRAMES

        self.input_stream.read.side_effect = read
        with patch.dict(script.os.environ, {"DASHSCOPE_API_KEY": "test-only-key"}):
            with patch("sys.argv", ["test_1.py"]):
                self.assertEqual(script.main(), 1)
        self.assert_resources_closed()

    def test_transport_error_between_socket_check_and_send_is_handled(self):
        self.send_error = script.WebSocketException("socket closed during send")
        self.input_stream.read.side_effect = None
        self.input_stream.read.return_value = b"\x00\x00" * script.INPUT_FRAMES
        with patch.dict(script.os.environ, {"DASHSCOPE_API_KEY": "test-only-key"}):
            with patch("sys.argv", ["test_1.py"]):
                self.assertEqual(script.main(), 1)
        self.assert_resources_closed()

    def test_ctrl_c_finishes_session_and_plays_final_audio_before_closing(self):
        pcm = b"\x02\x00" * script.INPUT_FRAMES
        self.input_stream.read.side_effect = [pcm, KeyboardInterrupt]
        self.output_stream.close.side_effect = self.assert_player_stopped
        self.run_script()
        self.assertEqual(
            [e["type"] for e in self.messages],
            ["session.update", "input_audio_buffer.append", "session.finish"],
        )
        self.assertEqual(base64.b64decode(self.messages[1]["audio"]), pcm)
        self.output_stream.write.assert_called_once_with(b"\x01\x00" * 10)
        self.assert_resources_closed()

    def test_input_monitor_is_disabled_by_default(self):
        self.run_script()
        self.assertFalse(self.args.monitor_input)
        self.assertEqual(self.audio.open.call_count, 2)
        self.assert_resources_closed()

    def test_input_monitor_plays_original_pcm_at_input_rate_and_keeps_translation(self):
        self.args = script.build_parser().parse_args([
            "--monitor-input", "--output-device", "6",
        ])
        monitor_stream = Mock()
        monitor_alive_at_close = []
        monitor_stream.close.side_effect = lambda: monitor_alive_at_close.append(any(
            t.name == "input-monitor-player" for t in threading.enumerate()
        ))
        self.audio.open.side_effect = [self.output_stream, self.input_stream, monitor_stream]
        pcm = b"\x02\x00" * script.INPUT_FRAMES
        self.input_stream.read.side_effect = [pcm, KeyboardInterrupt]
        self.run_script()
        self.assertEqual(self.audio.open.call_args_list[2].kwargs, {
            "format": script.pyaudio.paInt16,
            "channels": 1,
            "rate": script.INPUT_RATE,
            "output": True,
            "output_device_index": 6,
            "frames_per_buffer": script.INPUT_FRAMES,
        })
        monitor_stream.write.assert_called_once_with(pcm)
        self.assertEqual(base64.b64decode(self.messages[1]["audio"]), pcm)
        self.output_stream.write.assert_called_once_with(b"\x01\x00" * 10)
        monitor_stream.close.assert_called_once_with()
        self.assertEqual(monitor_alive_at_close, [False])
        self.assert_resources_closed()

    def test_input_monitor_can_use_a_separate_output_device(self):
        self.args = script.build_parser().parse_args([
            "--monitor-input", "--output-device", "6", "--monitor-output-device", "0",
        ])
        monitor_stream = Mock()
        self.audio.open.side_effect = [self.output_stream, self.input_stream, monitor_stream]
        self.run_script()
        self.assertEqual(self.audio.open.call_args_list[0].kwargs["output_device_index"], 6)
        self.assertEqual(self.audio.open.call_args_list[2].kwargs["output_device_index"], 0)
        monitor_stream.close.assert_called_once_with()
        self.assert_resources_closed()

    def test_input_monitor_device_failure_closes_existing_resources(self):
        self.args.monitor_input = True
        self.audio.open.side_effect = [
            self.output_stream, self.input_stream, OSError("unsupported monitor device"),
        ]
        with self.assertRaisesRegex(OSError, "unsupported monitor device"):
            self.run_script()
        self.factory.assert_not_called()
        self.assert_resources_closed()

    def test_output_device_failure_still_terminates_pyaudio(self):
        self.audio.open.side_effect = OSError("unsupported output device")
        with self.assertRaisesRegex(OSError, "unsupported output device"):
            self.run_script()
        self.audio.terminate.assert_called_once_with()
        self.factory.assert_not_called()

    def test_input_device_failure_stops_player_and_closes_output(self):
        self.audio.open.side_effect = [self.output_stream, OSError("unsupported input")]
        with self.assertRaisesRegex(OSError, "unsupported input"):
            self.run_script()
        self.output_stream.close.assert_called_once_with()
        self.audio.terminate.assert_called_once_with()
        self.factory.assert_not_called()
        self.assert_player_stopped()

    def test_cleanup_failure_does_not_skip_other_resources(self):
        self.input_stream.stop_stream.side_effect = OSError("stop failed")
        self.run_script()
        self.assert_resources_closed()

    def test_playback_failure_notifies_recording_loop(self):
        callback = script.RealtimeCallback()
        callback.audio_queue.put(b"\x00\x00")
        self.output_stream.write.side_effect = OSError("device disconnected")
        script.play_audio(callback, self.output_stream, threading.Event(), threading.Event())
        self.assertTrue(callback.stopped.is_set())
        with self.assertRaisesRegex(RuntimeError, "device disconnected"):
            callback.raise_if_failed()

    def test_missing_key_fails_before_opening_devices(self):
        with patch.dict(script.os.environ, {"DASHSCOPE_API_KEY": ""}):
            with patch("sys.argv", ["test_1.py"]):
                self.assertEqual(script.main(), 2)
        self.audio.open.assert_not_called()
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
