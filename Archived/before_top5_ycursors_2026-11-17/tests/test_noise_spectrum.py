import unittest

import numpy as np

import noise

FS = 500_000.0


def _tone_signal(tones, seconds=15.0, noise_sd=1.0, seed=0):
    """tones: list of (freq_hz, rms_mV). Returns white noise + sines."""
    rng = np.random.default_rng(seed)
    n = int(FS * seconds)
    t = np.arange(n) / FS
    x = rng.normal(0, noise_sd, n)
    for f, rms in tones:
        x += np.sqrt(2) * rms * np.sin(2 * np.pi * f * t)
    return x


class SpectrumTests(unittest.TestCase):
    def test_sine_amplitude(self):
        x = _tone_signal([(60.0, 1.0)])
        freqs, psd, df = noise.spectrum(x, FS)
        f, a = noise.top_peaks(freqs, psd, df)[0]
        self.assertAlmostEqual(f, 60.0, delta=1.0)
        self.assertAlmostEqual(a, 1.0, delta=0.01)

    def test_high_frequency_tone(self):
        x = _tone_signal([(100_000.0, 0.5)])
        freqs, psd, df = noise.spectrum(x, FS)
        f, a = noise.top_peaks(freqs, psd, df)[0]
        self.assertAlmostEqual(f, 100_000.0, delta=1.0)
        self.assertAlmostEqual(a, 0.5, delta=0.005)

    def test_between_bins(self):
        x = _tone_signal([(60.4, 1.0)])
        freqs, psd, df = noise.spectrum(x, FS)
        f, a = noise.top_peaks(freqs, psd, df)[0]
        self.assertAlmostEqual(a, 1.0, delta=0.01)

    def test_peak_order_and_merge(self):
        x = _tone_signal([(60.0, 1.0), (120.0, 0.3), (15550.0, 0.6),
                          (63.0, 0.5)])
        freqs, psd, df = noise.spectrum(x, FS)
        peaks = noise.top_peaks(freqs, psd, df)
        self.assertEqual(len(peaks), 3)
        for (f, _), want in zip(peaks, [60.0, 15550.0, 120.0]):
            self.assertAlmostEqual(f, want, delta=1.5)

    def test_f_min(self):
        x = _tone_signal([(1.0, 5.0), (60.0, 1.0)])
        freqs, psd, df = noise.spectrum(x, FS)
        f, _ = noise.top_peaks(freqs, psd, df, f_min=2.0)[0]
        self.assertAlmostEqual(f, 60.0, delta=1.0)
        for pf, _ in noise.top_peaks(freqs, psd, df, f_min=2.0):
            self.assertGreaterEqual(pf, 2.0)

    def test_random_walk_peaks_above_f_min(self):
        rng = np.random.default_rng(3)
        n = int(FS * 15.0)
        x = np.cumsum(rng.normal(0, 1, n)) * 0.01
        x += np.sqrt(2) * 1.0 * np.sin(2 * np.pi * 60.0 * np.arange(n) / FS)
        freqs, psd, df = noise.spectrum(x, FS)
        peaks = noise.top_peaks(freqs, psd, df, f_min=2.0)
        self.assertTrue(peaks)
        for pf, _ in peaks:
            self.assertGreaterEqual(pf, 2.0)

    def test_short_window_uses_one_segment(self):
        n = int(FS * 0.1)
        t = np.arange(n) / FS
        x = np.sqrt(2) * 1.0 * np.sin(2 * np.pi * 1000.0 * t)
        freqs, psd, df = noise.spectrum(x, FS)
        self.assertAlmostEqual(df, FS / n, places=9)
        f, a = noise.top_peaks(freqs, psd, df)[0]
        self.assertAlmostEqual(a, 1.0, delta=0.02)

    def test_component_out_of_range(self):
        x = _tone_signal([(60.0, 1.0)], seconds=2.0)
        freqs, psd, df = noise.spectrum(x, FS)
        self.assertIsNone(noise.component_rms(freqs, psd, df, 300_000.0))
        self.assertIsNone(noise.component_rms(freqs, psd, df, df / 2))

    def test_too_few_samples(self):
        with self.assertRaises(ValueError):
            noise.spectrum(np.zeros(1), FS)

    def test_amplitude_spectrum(self):
        psd = np.array([4.0, 9.0])
        np.testing.assert_allclose(noise.amplitude_spectrum(psd, 0.25),
                                   [1.0, 1.5])


def _reference_psd(values, fs, seg):
    """The original single-threaded Welch loop, kept to pin results bit for bit."""
    n = len(values)
    seg = min(seg, n)
    hop = max(seg // 2, 1)
    nseg = (n - seg) // hop + 1
    w = np.hanning(seg + 1)[:-1]
    acc = np.zeros(seg // 2 + 1)
    for i in range(nseg):
        s = np.asarray(values[i * hop:i * hop + seg], dtype=np.float64)
        s = (s - s.mean()) * w
        x = np.fft.rfft(s)
        acc += x.real ** 2 + x.imag ** 2
    return acc * (2.0 / (fs * float(np.sum(w ** 2)) * nseg))


class SpectrumSpeedupTests(unittest.TestCase):
    """The threaded, buffer-reusing Welch loop gives exactly the original result."""

    def test_matches_reference_bit_for_bit(self):
        x = _tone_signal([(60.0, 1.0), (1234.0, 0.3)], seconds=0.2)
        for seg in (4096, 1000, len(x), 3):
            _, psd, _ = noise.spectrum(x, FS, seg=seg)
            np.testing.assert_array_equal(psd, _reference_psd(x, FS, seg))

    def test_integer_input(self):
        x = np.arange(5000, dtype=np.int32) % 17
        _, psd, _ = noise.spectrum(x, FS, seg=1024)
        np.testing.assert_array_equal(psd, _reference_psd(x, FS, 1024))

    def test_single_worker_same_result(self):
        x = _tone_signal([(500.0, 1.0)], seconds=0.1)
        old = noise.WORKERS
        try:
            noise.WORKERS = 1
            _, serial, _ = noise.spectrum(x, FS, seg=2048)
        finally:
            noise.WORKERS = old
        _, threaded, _ = noise.spectrum(x, FS, seg=2048)
        np.testing.assert_array_equal(serial, threaded)

    def test_trace_spectrum_equals_values_spectrum(self):
        import trace_store
        raw = _tone_signal([(60.0, 1.0)], seconds=0.1)
        t = np.round(np.arange(len(raw)) * 2e-6, 6)
        for x in (np.round(raw, 4), raw):   # int32 codes, then float64 values
            tr = trace_store.TraceData.from_arrays(t, x)
            i0, i1 = 123, len(x) - 77
            a = noise.spectrum(tr.values(i0, i1), FS, seg=4096)
            b = noise.trace_spectrum(tr, i0, i1, seg=4096)
            np.testing.assert_array_equal(a[0], b[0])
            np.testing.assert_array_equal(a[1], b[1])
            self.assertEqual(a[2], b[2])
        self.assertIsNone(tr.scale)


if __name__ == "__main__":
    unittest.main()
