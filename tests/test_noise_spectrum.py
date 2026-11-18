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


class SeenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        x = _tone_signal([(60.0, 1.0)])                       # 15 s, noise sd = 1 mV
        t = np.arange(len(x)) / FS
        x += np.where(t < 4.0, np.sqrt(2) * 0.5 * np.sin(2 * np.pi * 15_000 * t), 0.0)
        cls.x = x
        cls.freqs, cls.psd, cls.df, cls.sl = noise.spectrum(x, FS, slices=True)

    def test_psd_unchanged_with_slices(self):
        f2, p2, d2 = noise.spectrum(self.x, FS)
        np.testing.assert_array_equal(p2, self.psd)          # bit-identical
        self.assertEqual(d2, self.df)

    def test_slice_count_and_dtype(self):
        self.assertEqual(self.sl.shape, (14, len(self.psd)))
        self.assertEqual(self.sl.dtype, np.float32)

    def test_constant_tone_seen_every_slice(self):
        self.assertEqual(noise.seen_count(self.sl, self.df, 60.0), 14)

    def test_burst_seen_only_while_on(self):
        self.assertIn(noise.seen_count(self.sl, self.df, 15_000.0), (3, 4))

    def test_noise_only_not_seen(self):
        self.assertLessEqual(noise.seen_count(self.sl, self.df, 33_333.0), 1)

    def test_seen_edges(self):
        for f in (self.df, 2.0, self.freqs[-1], self.freqs[-2]):
            n = noise.seen_count(self.sl, self.df, f)
            self.assertTrue(n is None or (isinstance(n, int) and 0 <= n <= 14), n)
        self.assertIsNone(noise.seen_count(self.sl, self.df, 2.0))   # too close to DC to judge

    def test_white_noise_top_peaks_rarely_seen(self):
        x = np.random.default_rng(7).normal(0, 1.0, int(FS * 15))
        freqs, psd, df, sl = noise.spectrum(x, FS, slices=True)
        for hz, _ in noise.top_peaks(freqs, psd, df, n=noise.N_PEAKS):
            s = noise.seen_count(sl, df, hz)
            self.assertTrue(s is None or s <= 1, (hz, s))

    def test_one_over_f_noise_not_seen(self):
        rng = np.random.default_rng(3)
        n = int(FS * 15)
        spec = np.fft.rfft(rng.normal(0, 1.0, n))
        f = np.fft.rfftfreq(n, 1 / FS)
        spec[1:] /= np.sqrt(f[1:])                       # 1/f power
        spec[0] = 0
        x = np.fft.irfft(spec, n)
        freqs, psd, df, sl = noise.spectrum(x, FS, slices=True)
        for hz, _ in noise.top_peaks(freqs, psd, df, n=noise.N_PEAKS):
            s = noise.seen_count(sl, df, hz)
            self.assertTrue(s is None or s <= 2, (hz, s))

    def test_tone_on_one_over_f_still_seen(self):
        rng = np.random.default_rng(4)
        n = int(FS * 15)
        spec = np.fft.rfft(rng.normal(0, 1.0, n))
        f = np.fft.rfftfreq(n, 1 / FS)
        spec[1:] /= np.sqrt(f[1:])
        spec[0] = 0
        x = np.fft.irfft(spec, n)
        x = x / x.std()
        x += np.sqrt(2) * 0.5 * np.sin(2 * np.pi * 30.0 * np.arange(n) / FS)
        _, _, df, sl = noise.spectrum(x, FS, slices=True)
        self.assertEqual(noise.seen_count(sl, df, 30.0), 14)

    def test_short_window_one_slice(self):
        x = _tone_signal([(60.0, 1.0)], seconds=0.2)
        _, _, df, sl = noise.spectrum(x, FS, slices=True)
        self.assertEqual(sl.shape[0], 1)
        self.assertIsNone(noise.seen_count(sl, df, 60.0))     # one slice: "seen" is not judged


if __name__ == "__main__":
    unittest.main()
