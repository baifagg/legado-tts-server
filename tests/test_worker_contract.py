# -*- coding: utf-8 -*-
"""Worker 契约测试：用桩模型验证加载参数与变速拆分。

不加载真模型、不占显存，可以在任何机器上跑（有 torch 就行）。
CI 里没装 torch 时会自动跳过。
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

try:
    import torch

    HAS_TORCH = True
except Exception:  # pragma: no cover
    HAS_TORCH = False


class _StubModel:
    last_kwargs: dict = {}
    last_gen: dict = {}

    @classmethod
    def from_pretrained(cls, src, **kwargs):
        cls.last_kwargs = dict(src=src, **kwargs)
        m = cls()
        m.sampling_rate = 24000
        return m

    def generate(self, **kwargs):
        import numpy as np

        type(self).last_gen = dict(kwargs)
        return [np.zeros(24000, dtype="float32")]


@unittest.skipUnless(HAS_TORCH, "需要 torch")
class WorkerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fake = types.ModuleType("omnivoice")
        fake.OmniVoice = _StubModel
        sys.modules.setdefault("omnivoice", fake)
        import worker

        cls.w = worker

    def test_falls_back_to_hf_repo_when_no_local_model(self):
        self.w.DEFAULT_MODEL_DIR = Path("/definitely/not/exists")
        self.w.load_model(None, "cpu", "float32")
        self.assertEqual(_StubModel.last_kwargs["src"], self.w.HF_MODEL_ID)

    def test_explicit_model_path_and_dtype(self):
        self.w.load_model("/tmp/MyModel", "cuda:0", "float16")
        kw = _StubModel.last_kwargs
        self.assertEqual(kw["src"], "/tmp/MyModel")
        self.assertEqual(kw["device_map"], "cuda:0")
        self.assertEqual(kw["dtype"], torch.float16)

    def test_synth_limits_model_side_speed(self):
        self.w._model = _StubModel()
        self.w._ref_cache["__stub__"] = ("/tmp/x.wav", "测试")
        self.w.synth_omni("测试", "__stub__", 2.57)
        gen = _StubModel.last_gen
        self.assertEqual(gen["num_step"], 16)
        self.assertLessEqual(gen["speed"], 1.7, "模型侧 speed 必须被限制在 1.6 附近")

    def test_state_fields_present(self):
        for k in ("loaded", "load_seconds", "device", "dtype", "model", "error"):
            self.assertIn(k, self.w._state)


if __name__ == "__main__":
    unittest.main()
