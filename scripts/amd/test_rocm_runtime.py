"""CPU regression checks for architecture selection and FFN recovery dispatch."""
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from freevideo_engine.hardware import Hardware
from freevideo_engine.backends.cuda import CUDABackend


class ROCmRuntime(unittest.TestCase):
    def hardware(self, **kw):
        return Hardware('Test GPU', (12, 0), 32 << 30, 30 << 30,
                        64 << 30, 60 << 30, **kw)

    def test_hip_capability_does_not_select_nvidia_architecture(self):
        card = self.hardware(hip_version='7.15', gcn_arch='gfx1201:sramecc-')
        self.assertEqual(card.architecture, 'rdna4')
        self.assertEqual(card.cuda_compatibility()['linear_compute'], 'native-fp8')

    def test_unvalidated_gfx12_does_not_select_rdna4(self):
        card = self.hardware(hip_version='7.15', gcn_arch='gfx1250')
        self.assertEqual(card.architecture, 'amd-gfx1250')
        self.assertEqual(card.cuda_compatibility()['linear_compute'], 'bf16-weight-only')

    def test_cuda_architecture_is_preserved(self):
        self.assertEqual(self.hardware().architecture, 'blackwell-rtx')

    def dispatch(self, *, hip='7.15', arch='gfx1201', chunk=2048,
                 recompute=False, selected='tail-preserving'):
        calls = []
        fake = types.SimpleNamespace(
            version=types.SimpleNamespace(hip=hip),
            cuda=types.SimpleNamespace(get_device_properties=lambda _: types.SimpleNamespace(gcnArchName=arch)))
        standard = types.ModuleType('freevideo_engine.fp8_ops')
        standard.install_chunked_ff = lambda module, size, **kw: calls.append(('standard', size, kw))
        optimized = types.ModuleType('freevideo_engine.rocm_ffn')
        optimized.install_tail_preserving_ff = lambda module: calls.append(('tail-preserving', 8192))
        with patch.dict(os.environ, FREEVIDEO_ROCM_FFN=selected), patch.dict(sys.modules, {
                standard.__name__: standard, optimized.__name__: optimized}):
            CUDABackend(torch_module=fake).install_chunked_ff(object(), chunk, recompute=recompute)
        return calls

    def test_r9700_opt_in_selects_measured_path(self):
        self.assertEqual(self.dispatch(), [('tail-preserving', 8192)])

    def test_oom_recovery_keeps_smaller_chunks_and_recomputation(self):
        for chunk, recompute in ((512, False), (1024, False), (2048, True)):
            with self.subTest(chunk=chunk, recompute=recompute):
                self.assertEqual(self.dispatch(chunk=chunk, recompute=recompute),
                                 [('standard', chunk, {'recompute': recompute})])

    def test_cuda_and_default_rocm_use_original_implementation(self):
        for kwargs in ({'hip': None}, {'selected': 'default'}):
            self.assertEqual(self.dispatch(**kwargs), [('standard', 2048, {'recompute': False})])

    def test_other_architectures_and_invalid_modes_fail_explicitly(self):
        for kwargs in ({'arch': 'gfx1100'}, {'selected': 'invalid'}):
            with self.assertRaises(ValueError):
                self.dispatch(**kwargs)


if __name__ == '__main__':
    unittest.main()
