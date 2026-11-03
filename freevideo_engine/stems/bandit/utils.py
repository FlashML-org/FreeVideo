# Adapted from Bandit (https://github.com/kwatcharasupat/source-separation), Copyright (c) the Bandit authors,
# Apache-2.0 (freevideo_engine/licenses/Bandit-Apache-2.0.txt). Modified: reduced to the inference subset.
import os
from abc import abstractmethod
from typing import Callable

import numpy as np
import torch


def hz_to_midi(frequency):
    return 12 * (np.log2(np.asanyarray(frequency)) - np.log2(440.0)) + 69


def midi_to_hz(notes):
    return 440.0 * (2.0 ** ((np.asanyarray(notes) - 69.0) / 12.0))


def band_widths_from_specs(band_specs):
    return [e - i for i, e in band_specs]


def check_nonzero_bandwidth(band_specs):
    # pprint(band_specs)
    for fstart, fend in band_specs:
        if fend - fstart <= 0:
            raise ValueError("Bands cannot be zero-width")


def check_no_overlap(band_specs):
    fend_prev = -1
    for fstart_curr, fend_curr in band_specs:
        if fstart_curr <= fend_prev:
            raise ValueError("Bands cannot overlap")


def check_no_gap(band_specs):
    fstart, _ = band_specs[0]
    assert fstart == 0

    fend_prev = -1
    for fstart_curr, fend_curr in band_specs:
        if fstart_curr - fend_prev > 1:
            raise ValueError("Bands cannot leave gap")
        fend_prev = fend_curr


class BandsplitSpecification:
    def __init__(self, nfft: int, fs: int) -> None:
        self.fs = fs
        self.nfft = nfft
        self.nyquist = fs / 2
        self.max_index = nfft // 2 + 1

        self.split500 = self.hertz_to_index(500)
        self.split1k = self.hertz_to_index(1000)
        self.split2k = self.hertz_to_index(2000)
        self.split4k = self.hertz_to_index(4000)
        self.split8k = self.hertz_to_index(8000)
        self.split16k = self.hertz_to_index(16000)
        self.split20k = self.hertz_to_index(20000)

        self.above20k = [(self.split20k, self.max_index)]
        self.above16k = [(self.split16k, self.split20k)] + self.above20k

    def index_to_hertz(self, index: int):
        return index * self.fs / self.nfft

    def hertz_to_index(self, hz: float, round: bool = True):
        index = hz * self.nfft / self.fs

        if round:
            index = int(np.round(index))

        return index

    def get_band_specs_with_bandwidth(
            self,
            start_index,
            end_index,
            bandwidth_hz
            ):
        band_specs = []
        lower = start_index

        while lower < end_index:
            upper = int(np.floor(lower + self.hertz_to_index(bandwidth_hz)))
            upper = min(upper, end_index)

            band_specs.append((lower, upper))
            lower = upper

        return band_specs

    @abstractmethod
    def get_band_specs(self):
        raise NotImplementedError












class PerceptualBandsplitSpecification(BandsplitSpecification):
    def __init__(
            self,
            nfft: int,
            fs: int,
            fbank_fn: Callable[[int, int, float, float, int], torch.Tensor],
            n_bands: int,
            f_min: float = 0.0,
            f_max: float = None
    ) -> None:
        super().__init__(nfft=nfft, fs=fs)
        self.n_bands = n_bands
        if f_max is None:
            f_max = fs / 2

        self.filterbank = fbank_fn(
                n_bands, fs, f_min, f_max, self.max_index
        )

        weight_per_bin = torch.sum(
            self.filterbank,
            dim=0,
            keepdim=True
            )  # (1, n_freqs)
        normalized_mel_fb = self.filterbank / weight_per_bin  # (n_mels, n_freqs)

        freq_weights = []
        band_specs = []
        for i in range(self.n_bands):
            active_bins = torch.nonzero(self.filterbank[i, :]).squeeze().tolist()
            if isinstance(active_bins, int):
                active_bins = (active_bins, active_bins)
            if len(active_bins) == 0:
                continue
            start_index = active_bins[0]
            end_index = active_bins[-1] + 1
            band_specs.append((start_index, end_index))
            freq_weights.append(normalized_mel_fb[i, start_index:end_index])

        self.freq_weights = freq_weights
        self.band_specs = band_specs

    def get_band_specs(self):
        return self.band_specs

    def get_freq_weights(self):
        return self.freq_weights

    def save_to_file(self, dir_path: str) -> None:

        os.makedirs(dir_path, exist_ok=True)

        import pickle

        with open(os.path.join(dir_path, "mel_bandsplit_spec.pkl"), "wb") as f:
            pickle.dump(
                    {
                            "band_specs": self.band_specs,
                            "freq_weights": self.freq_weights,
                            "filterbank": self.filterbank,
                    },
                    f,
            )




def musical_filterbank(n_bands, fs, f_min, f_max, n_freqs,
                       scale="constant"):

    nfft = 2 * (n_freqs - 1)
    df = fs / nfft
    # init freqs
    f_max = f_max or fs / 2
    f_min = f_min or 0
    f_min = fs / nfft

    n_octaves = np.log2(f_max / f_min)
    n_octaves_per_band = n_octaves / n_bands
    bandwidth_mult = np.power(2.0, n_octaves_per_band)

    low_midi = max(0, hz_to_midi(f_min))
    high_midi = hz_to_midi(f_max)
    midi_points = np.linspace(low_midi, high_midi, n_bands)
    hz_pts = midi_to_hz(midi_points)

    low_pts = hz_pts / bandwidth_mult
    high_pts = hz_pts * bandwidth_mult

    low_bins = np.floor(low_pts / df).astype(int)
    high_bins = np.ceil(high_pts / df).astype(int)

    fb = np.zeros((n_bands, n_freqs))

    for i in range(n_bands):
        fb[i, low_bins[i]:high_bins[i]+1] = 1.0

    fb[0, :low_bins[0]] = 1.0
    fb[-1, high_bins[-1]+1:] = 1.0

    return torch.as_tensor(fb)

class MusicalBandsplitSpecification(PerceptualBandsplitSpecification):
    def __init__(
            self,
            nfft: int,
            fs: int,
            n_bands: int,
            f_min: float = 0.0,
            f_max: float = None
    ) -> None:
        super().__init__(fbank_fn=musical_filterbank, nfft=nfft, fs=fs, n_bands=n_bands, f_min=f_min, f_max=f_max)



















if __name__ == "__main__":
    import pandas as pd

    band_defs = []

    for bands in [VocalBandsplitSpecification]:  
        band_name = bands.__name__.replace("BandsplitSpecification", "")

        mbs = bands(nfft=2048, fs=44100).get_band_specs()

        for i, (f_min, f_max) in enumerate(mbs):
            band_defs.append({
                "band": band_name,
                "band_index": i,
                "f_min": f_min,
                "f_max": f_max
            })

    df = pd.DataFrame(band_defs)
    df.to_csv("vox7bands.csv", index=False)