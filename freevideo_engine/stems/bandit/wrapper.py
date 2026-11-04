# Adapted from Bandit (https://github.com/kwatcharasupat/source-separation), Copyright (c) the Bandit authors,
# Apache-2.0 (freevideo_engine/licenses/Bandit-Apache-2.0.txt). Modified: reduced to the inference subset.
from typing import Dict, List, Optional, Tuple, Union

import torch
from torch import nn

from .core import MultiSourceMultiMaskBandSplitCoreRNN
from .spectral import _SpectralComponent
from .utils import MusicalBandsplitSpecification


def get_band_specs(band_specs, n_fft, fs, n_bands=None):
    if "musical" not in band_specs or n_bands is None:
        raise NameError(band_specs)
    specs = MusicalBandsplitSpecification(nfft=n_fft, fs=fs, n_bands=n_bands)
    return specs.get_band_specs(), specs.get_freq_weights(), True


class BandSplitWrapperBase(nn.Module):
    bsrnn: nn.Module
    
    def __init__(self, **kwargs):
        super().__init__()




class MultiMaskMultiSourceBandSplitBase(
        BandSplitWrapperBase,
        _SpectralComponent
):
    def __init__(
            self,
            stems: List[str],
            band_specs: Union[str, List[Tuple[float, float]]],
            fs: int = 44100,
            n_fft: int = 2048,
            win_length: Optional[int] = 2048,
            hop_length: int = 512,
            window_fn: str = "hann_window",
            wkwargs: Optional[Dict] = None,
            power: Optional[int] = None,
            center: bool = True,
            normalized: bool = True,
            pad_mode: str = "constant",
            onesided: bool = True,
            n_bands: int = None,
    ) -> None:
        super().__init__(
                n_fft=n_fft,
                win_length=win_length,
                hop_length=hop_length,
                window_fn=window_fn,
                wkwargs=wkwargs,
                power=power,
                center=center,
                normalized=normalized,
                pad_mode=pad_mode,
                onesided=onesided,
        )

        if isinstance(band_specs, str):
            self.band_specs, self.freq_weights, self.overlapping_band = get_band_specs(
                band_specs,
                n_fft,
                fs,
                n_bands
                )

        self.stems = stems

    def forward(self, batch):
        # with torch.no_grad():
        audio = batch["audio"]
        cond = batch.get("condition", None)
        with torch.no_grad():
            batch["spectrogram"] = {stem: self.stft(audio[stem]) for stem in
                                    audio}

        X = batch["spectrogram"]["mixture"]
        length = batch["audio"]["mixture"].shape[-1]

        output = self.bsrnn(X, cond=cond)
        output["audio"] = {}

        for stem, S in output["spectrogram"].items():
            s = self.istft(S, length)
            output["audio"][stem] = s

        return batch, output






class MultiMaskMultiSourceBandSplitRNN(MultiMaskMultiSourceBandSplitBase):
    def __init__(
            self,
            in_channel: int,
            stems: List[str],
            band_specs: Union[str, List[Tuple[float, float]]],
            fs: int = 44100,
            require_no_overlap: bool = False,
            require_no_gap: bool = True,
            normalize_channel_independently: bool = False,
            treat_channel_as_feature: bool = True,
            n_sqm_modules: int = 12,
            emb_dim: int = 128,
            rnn_dim: int = 256,
            cond_dim: int = 0,
            bidirectional: bool = True,
            rnn_type: str = "LSTM",
            mlp_dim: int = 512,
            hidden_activation: str = "Tanh",
            hidden_activation_kwargs: Optional[Dict] = None,
            complex_mask: bool = True,
            n_fft: int = 2048,
            win_length: Optional[int] = 2048,
            hop_length: int = 512,
            window_fn: str = "hann_window",
            wkwargs: Optional[Dict] = None,
            power: Optional[int] = None,
            center: bool = True,
            normalized: bool = True,
            pad_mode: str = "constant",
            onesided: bool = True,
            n_bands: int = None,
            use_freq_weights: bool = True,
            normalize_input: bool = False,
            mult_add_mask: bool = False,
            freeze_encoder: bool = False,
    ) -> None:
        super().__init__(
                stems=stems,
                band_specs=band_specs,
                fs=fs,
                n_fft=n_fft,
                win_length=win_length,
                hop_length=hop_length,
                window_fn=window_fn,
                wkwargs=wkwargs,
                power=power,
                center=center,
                normalized=normalized,
                pad_mode=pad_mode,
                onesided=onesided,
                n_bands=n_bands,
        )

        self.bsrnn = MultiSourceMultiMaskBandSplitCoreRNN(
                stems=stems,
                band_specs=self.band_specs,
                in_channel=in_channel,
                require_no_overlap=require_no_overlap,
                require_no_gap=require_no_gap,
                normalize_channel_independently=normalize_channel_independently,
                treat_channel_as_feature=treat_channel_as_feature,
                n_sqm_modules=n_sqm_modules,
                emb_dim=emb_dim,
                rnn_dim=rnn_dim,
                bidirectional=bidirectional,
                rnn_type=rnn_type,
                mlp_dim=mlp_dim,
                cond_dim=cond_dim,
                hidden_activation=hidden_activation,
                hidden_activation_kwargs=hidden_activation_kwargs,
                complex_mask=complex_mask,
                overlapping_band=self.overlapping_band,
                freq_weights=self.freq_weights,
                n_freq=n_fft // 2 + 1,
                use_freq_weights=use_freq_weights,
                mult_add_mask=mult_add_mask
        )

        self.normalize_input = normalize_input
        self.cond_dim = cond_dim

        if freeze_encoder:
            for param in self.bsrnn.band_split.parameters():
                param.requires_grad = False

            for param in self.bsrnn.tf_model.parameters():
                param.requires_grad = False





