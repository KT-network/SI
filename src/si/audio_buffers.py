from __future__ import annotations

import threading
from collections import deque

import numpy as np
import soxr


class CaptureBuffer:
    def __init__(self, max_bytes: int = 16000):
        self.max_bytes = max_bytes
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._lock = threading.Lock()
        self.closed = False
        self.overflowed = False

    def push(self, data: bytes) -> bool:
        with self._lock:
            if self.closed:
                return False
            if self._size + len(data) > self.max_bytes:
                self.overflowed = True
                return False
            if data:
                self._chunks.append(data)
                self._size += len(data)
            return True

    def take(self) -> bytes:
        with self._lock:
            if not self._chunks or self.closed:
                return b""
            result = self._chunks.popleft()
            self._size -= len(result)
            return result

    def close(self) -> None:
        with self._lock:
            self.closed = True
            self._chunks.clear()
            self._size = 0


class PlaybackBuffer:
    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._chunks: deque[memoryview] = deque()
        self.size = 0

    def push(self, data: bytes) -> None:
        if self.size + len(data) > self.max_bytes:
            raise BufferError("译音播放积压超过 8 秒，已停止该方向")
        if data:
            self._chunks.append(memoryview(data))
            self.size += len(data)

    def peek(self, size: int) -> bytes:
        parts = []
        for chunk in self._chunks:
            if size <= 0:
                break
            part = chunk[:size]
            parts.append(part)
            size -= len(part)
        return b"".join(parts)

    def consume(self, size: int) -> None:
        size = min(size, self.size)
        self.size -= size
        while size and self._chunks:
            chunk = self._chunks.popleft()
            if len(chunk) > size:
                self._chunks.appendleft(chunk[size:])
                break
            size -= len(chunk)

    def clear(self) -> None:
        self._chunks.clear()
        self.size = 0


SAMPLE_BYTES = {"int16": 2, "int32": 4, "float32": 4, "uint8": 1}


def decode_samples(raw: bytes, sample_format: str, channels: int) -> np.ndarray:
    if channels < 1 or len(raw) % (SAMPLE_BYTES[sample_format] * channels):
        raise ValueError("音频数据没有按完整采样帧对齐")
    dtype = {"int16": "<i2", "int32": "<i4", "float32": "<f4", "uint8": "u1"}[sample_format]
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if sample_format == "int16":
        samples /= 32768.0
    elif sample_format == "int32":
        samples /= 2147483648.0
    elif sample_format == "uint8":
        samples = (samples - 128.0) / 128.0
    samples = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
    return np.clip(samples.reshape(-1, channels).mean(axis=1, dtype=np.float32), -1.0, 1.0)


def encode_samples(samples: np.ndarray, sample_format: str, channels: int = 1) -> bytes:
    samples = np.clip(np.nan_to_num(samples), -1.0, 1.0)
    if channels > 1:
        samples = np.repeat(samples[:, None], channels, axis=1).ravel()
    if sample_format == "int16":
        return np.clip(np.rint(samples * 32768.0), -32768, 32767).astype("<i2").tobytes()
    if sample_format == "int32":
        return (
            np.clip(np.rint(samples.astype(np.float64) * 2147483648.0), -2147483648, 2147483647)
            .astype("<i4")
            .tobytes()
        )
    if sample_format == "uint8":
        return np.clip(np.rint(samples * 128.0 + 128.0), 0, 255).astype("u1").tobytes()
    return samples.astype("<f4").tobytes()


class StreamConverter:
    def __init__(
        self,
        input_rate: int,
        output_rate: int,
        input_format: str,
        output_format: str,
        input_channels: int = 1,
        output_channels: int = 1,
    ):
        self.input_format = input_format
        self.output_format = output_format
        self.input_channels = input_channels
        self.output_channels = output_channels
        self.frame_bytes = SAMPLE_BYTES[input_format] * input_channels
        self.remainder = b""
        self.level = 0.0
        self.resampler = (
            None
            if input_rate == output_rate
            else soxr.ResampleStream(input_rate, output_rate, 1, dtype="float32", quality="LQ")
        )

    def feed(self, raw: bytes, *, last: bool = False) -> bytes:
        raw = self.remainder + raw
        available = len(raw) - len(raw) % self.frame_bytes
        self.remainder = raw[available:]
        if last and self.remainder:
            raise ValueError("音频结束时存在不完整采样帧")
        samples = decode_samples(raw[:available], self.input_format, self.input_channels)
        if samples.size:
            self.level = float(np.sqrt(np.mean(samples * samples)))
        if self.resampler:
            samples = self.resampler.resample_chunk(samples, last=last)
        return encode_samples(samples, self.output_format, self.output_channels)
