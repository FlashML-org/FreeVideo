import numpy as np
import torch


def _hz_to_mel(f):
    f = np.asarray(f, dtype=np.float64)
    lin = f / (200.0 / 3)
    log = 15.0 + np.log(np.maximum(f, 1e-10) / 1000.0) / (np.log(6.4) / 27.0)
    return np.where(f >= 1000.0, log, lin)


def _mel_to_hz(m):
    m = np.asarray(m, dtype=np.float64)
    lin = (200.0 / 3) * m
    log = 1000.0 * np.exp((np.log(6.4) / 27.0) * (m - 15.0))
    return np.where(m >= 15.0, log, lin)


def librosa_mel_fn(sr, n_fft, n_mels, fmin=0.0, fmax=None):
    """Slaney-scale, Slaney-normalised mel filterbank (same result as librosa.filters.mel, float32)."""
    fmax = sr / 2.0 if fmax is None else fmax
    freqs = np.linspace(0.0, sr / 2.0, n_fft // 2 + 1)
    pts = _mel_to_hz(np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2))
    fdiff = np.diff(pts)
    ramps = pts[:, None] - freqs[None, :]
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    w = np.maximum(0.0, np.minimum(lower, upper))
    w *= (2.0 / (pts[2:n_mels + 2] - pts[:n_mels]))[:, None]
    return w.astype(np.float32)

_FILTERS = {}


def log_mel_spectrogram(audio, n_mels=128, n_fft=400, hop=160, sr=16000):
    """Whisper-compatible log-mel spectrogram (Slaney filters, log10, floor at max - 8, scaled to ~[-1, 1])."""
    key = (n_mels, n_fft, sr, audio.device)
    if key not in _FILTERS:
        _FILTERS[key] = torch.from_numpy(librosa_mel_fn(sr, n_fft, n_mels)).to(audio.device)
    window = torch.hann_window(n_fft).to(audio.device)
    stft = torch.stft(audio, n_fft, hop, window=window, return_complex=True)
    magnitudes = stft[..., :-1].abs() ** 2
    log_spec = torch.clamp(_FILTERS[key] @ magnitudes, min=1e-10).log10()
    log_spec = torch.maximum(log_spec, log_spec.max() - 8.0)
    return (log_spec + 4.0) / 4.0


# mel_spectrogram below is copied from Matcha-TTS matcha/utils/audio.py (MIT; derived from HiFi-GAN, MIT)
def dynamic_range_compression_torch(x, C=1, clip_val=1e-5):
    return torch.log(torch.clamp(x, min=clip_val) * C)


def spectral_normalize_torch(magnitudes):
    return dynamic_range_compression_torch(magnitudes)


mel_basis = {}
hann_window = {}


def mel_spectrogram(y, n_fft, num_mels, sampling_rate, hop_size, win_size, fmin, fmax, center=False):
    if torch.min(y) < -1.0:
        print("min value is ", torch.min(y))
    if torch.max(y) > 1.0:
        print("max value is ", torch.max(y))

    global mel_basis, hann_window  # pylint: disable=global-statement,global-variable-not-assigned
    if f"{str(fmax)}_{str(y.device)}" not in mel_basis:
        mel = librosa_mel_fn(sr=sampling_rate, n_fft=n_fft, n_mels=num_mels, fmin=fmin, fmax=fmax)
        mel_basis[str(fmax) + "_" + str(y.device)] = torch.from_numpy(mel).float().to(y.device)
        hann_window[str(y.device)] = torch.hann_window(win_size).to(y.device)

    y = torch.nn.functional.pad(
        y.unsqueeze(1), (int((n_fft - hop_size) / 2), int((n_fft - hop_size) / 2)), mode="reflect"
    )
    y = y.squeeze(1)

    spec = torch.view_as_real(
        torch.stft(
            y,
            n_fft,
            hop_length=hop_size,
            win_length=win_size,
            window=hann_window[str(y.device)],
            center=center,
            pad_mode="reflect",
            normalized=False,
            onesided=True,
            return_complex=True,
        )
    )

    spec = torch.sqrt(spec.pow(2).sum(-1) + (1e-9))

    spec = torch.matmul(mel_basis[str(fmax) + "_" + str(y.device)], spec)
    spec = spectral_normalize_torch(spec)

    return spec
