#!/usr/bin/env python3
"""Two bounded branch checks; no network or generator execution.

The valid-input case deliberately stops at the subprocess boundary, after
the verifier has checked and retained every input. It does not claim that
the mocked generation step passed. Full local regeneration is separate.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


class BundledGeometryBranch(unittest.TestCase):
    candidate_root = None
    inputs_root = None

    @classmethod
    def setUpClass(cls):
        scripts = cls.candidate_root / 'tb371fc/scripts'
        sys.path.insert(0, str(scripts))
        spec = importlib.util.spec_from_file_location(
            'reviewed_llcc_verifier', scripts / 'verify_llcc_generation.py')
        cls.verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.verifier)
        cls.pins = cls.verifier.generator.SOURCE_PINS
        assert len(cls.pins) == 8
        assert cls.pins[-1] == cls.verifier.BUNDLED_GEOMETRY_PIN
        cls.expected_urls = [
            f'https://raw.githubusercontent.com/{repo}/{revision}/{relative}'
            for _, repo, revision, relative, _, _, _ in cls.pins[:7]
        ]
        cls.primary_inputs = {
            url: (cls.inputs_root / (pin[0] + '/' + pin[3])).read_bytes()
            for url, pin in zip(cls.expected_urls, cls.pins[:7])
        }

    def run_case(self, out, bundle_path):
        def primary_only(url, *, max_bytes):
            self.assertEqual(max_bytes, 512 * 1024)
            self.assertIn(url, self.primary_inputs,
                          'Bundled geometry must never use the downloader')
            return self.primary_inputs[url]

        checkpoint = 'unit checkpoint: generator intentionally not executed'
        with mock.patch.object(self.verifier, 'read_source', side_effect=primary_only) as fetch, \
                mock.patch.object(self.verifier, 'BUNDLED_GEOMETRY_PATH', bundle_path), \
                mock.patch.object(self.verifier.subprocess, 'run',
                                  side_effect=RuntimeError(checkpoint)) as generation, \
                contextlib.redirect_stdout(io.StringIO()):
            status = self.verifier.run(SimpleNamespace(out=out, source_root=None))
        self.assertEqual([call.args[0] for call in fetch.call_args_list],
                         self.expected_urls)
        return status, json.loads((out / 'result.json').read_text()), generation, checkpoint

    def test_default_exact_tuple_reads_bundled_complete_input(self):
        with tempfile.TemporaryDirectory(prefix='llcc-bundle-positive-') as temporary:
            out = Path(temporary) / 'out'
            bundle = self.verifier.BUNDLED_GEOMETRY_PATH
            status, report, generation, checkpoint = self.run_case(out, bundle)
            self.assertEqual(status, 1)
            self.assertFalse(report['passed'])
            self.assertIn(checkpoint, report['error'])
            self.assertTrue(report['source_pins_verified'])
            self.assertEqual(len(report['inputs']), 8)
            self.assertEqual(report['checks'], [])
            generation.assert_called_once()
            retained = out / 'inputs/primary-upstream/arch/arm64/boot/dts/qcom/sm8250.dtsi'
            self.assertEqual(retained.read_bytes(), bundle.read_bytes())
            row = report['inputs'][-1]
            self.assertEqual(row['bytes'], 176883)
            self.assertEqual(row['sha256'],
                             '8735b5d8bb61d6a3a579e5b088ff2a719132266cef57c91c319ec19e12770499')
            self.assertEqual(row['git_blob_sha'], '630f4eff20bf810e347685066dce74ab92a23b56')

    def test_default_corrupt_bundled_byte_fails_before_generator(self):
        with tempfile.TemporaryDirectory(prefix='llcc-bundle-negative-') as temporary:
            root = Path(temporary)
            damaged = bytearray(self.verifier.BUNDLED_GEOMETRY_PATH.read_bytes())
            damaged[-1] ^= 1
            bundle = root / 'one-bit-sm8250.dtsi'
            bundle.write_bytes(damaged)
            status, report, generation, _ = self.run_case(root / 'out', bundle)
            self.assertEqual(status, 1)
            self.assertFalse(report['passed'])
            self.assertFalse(report['source_pins_verified'])
            self.assertEqual(len(report['inputs']), 8)
            self.assertEqual(report['checks'], [])
            self.assertIn('Public source identity differs: primary-upstream/', report['error'])
            generation.assert_not_called()
            self.assertNotIn('generator_exit', report)
            self.assertEqual(report['inputs'][-1]['sha256'], hashlib.sha256(damaged).hexdigest())
            self.assertNotEqual(report['inputs'][-1]['sha256'], report['inputs'][-1]['expected_sha256'])
            self.assertFalse((root / 'out/inputs/primary-upstream/arch/arm64/boot/dts/qcom/sm8250.dtsi').exists())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-root', required=True, type=Path)
    parser.add_argument('--inputs-root', required=True, type=Path)
    args = parser.parse_args()
    BundledGeometryBranch.candidate_root = args.candidate_root.resolve()
    BundledGeometryBranch.inputs_root = args.inputs_root.resolve()
    unittest.main(argv=[sys.argv[0]], verbosity=2)
