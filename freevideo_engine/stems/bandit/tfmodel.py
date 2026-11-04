# Adapted from Bandit (https://github.com/kwatcharasupat/source-separation), Copyright (c) the Bandit authors,
# Apache-2.0 (freevideo_engine/licenses/Bandit-Apache-2.0.txt). Modified: reduced to the inference subset.
import warnings

import torch
from torch import nn
from torch.nn.modules import rnn

import torch.backends.cuda


class TimeFrequencyModellingModule(nn.Module):
    def __init__(self) -> None:
        super().__init__()


class ResidualRNN(nn.Module):
    def __init__(
            self,
            emb_dim: int,
            rnn_dim: int,
            bidirectional: bool = True,
            rnn_type: str = "LSTM",
            use_batch_trick: bool = True,
            use_layer_norm: bool = True,
    ) -> None:
        # n_group is the size of the 2nd dim
        super().__init__()

        self.use_layer_norm = use_layer_norm
        if use_layer_norm:
            self.norm = nn.LayerNorm(emb_dim)
        else:
            self.norm = nn.GroupNorm(num_groups=emb_dim, num_channels=emb_dim)

        self.rnn = rnn.__dict__[rnn_type](
                input_size=emb_dim,
                hidden_size=rnn_dim,
                num_layers=1,
                batch_first=True,
                bidirectional=bidirectional,
        )

        self.fc = nn.Linear(
                in_features=rnn_dim * (2 if bidirectional else 1),
                out_features=emb_dim
        )

        self.use_batch_trick = use_batch_trick
        if not self.use_batch_trick:
            warnings.warn("NOT USING BATCH TRICK IS EXTREMELY SLOW!!")

    def forward(self, z):
        # z = (batch, n_uncrossed, n_across, emb_dim)

        z0 = torch.clone(z)

        # print(z.device)

        if self.use_layer_norm:
            z = self.norm(z)  # (batch, n_uncrossed, n_across, emb_dim)
        else:
            z = torch.permute(
                    z, (0, 3, 1, 2)
            )  # (batch, emb_dim, n_uncrossed, n_across)

            z = self.norm(z)  # (batch, emb_dim, n_uncrossed, n_across)

            z = torch.permute(
                    z, (0, 2, 3, 1)
            )  # (batch, n_uncrossed, n_across, emb_dim)

        batch, n_uncrossed, n_across, emb_dim = z.shape

        if self.use_batch_trick:
            z = torch.reshape(z, (batch * n_uncrossed, n_across, emb_dim))

            z = self.rnn(z.contiguous())[0]  # (batch * n_uncrossed, n_across, dir_rnn_dim)

            z = torch.reshape(z, (batch, n_uncrossed, n_across, -1))
            # (batch, n_uncrossed, n_across, dir_rnn_dim)
        else:
            # Note: this is EXTREMELY SLOW
            zlist = []
            for i in range(n_uncrossed):
                zi = self.rnn(z[:, i, :, :])[0]  # (batch, n_across, emb_dim)
                zlist.append(zi)

            z = torch.stack(
                    zlist,
                    dim=1
            )  # (batch, n_uncrossed, n_across, dir_rnn_dim)

        z = self.fc(z)  # (batch, n_uncrossed, n_across, emb_dim)

        z = z + z0

        return z


class SeqBandModellingModule(TimeFrequencyModellingModule):
    def __init__(
            self,
            n_modules: int = 12,
            emb_dim: int = 128,
            rnn_dim: int = 256,
            bidirectional: bool = True,
            rnn_type: str = "LSTM",
            parallel_mode=False,
    ) -> None:
        super().__init__()
        self.seqband = nn.ModuleList([])

        if parallel_mode:
            for _ in range(n_modules):
                self.seqband.append(
                        nn.ModuleList(
                                [ResidualRNN(
                                        emb_dim=emb_dim,
                                        rnn_dim=rnn_dim,
                                        bidirectional=bidirectional,
                                        rnn_type=rnn_type,
                                ),
                                        ResidualRNN(
                                                emb_dim=emb_dim,
                                                rnn_dim=rnn_dim,
                                                bidirectional=bidirectional,
                                                rnn_type=rnn_type,
                                        )]
                        )
                )
        else:

            for _ in range(2 * n_modules):
                self.seqband.append(
                        ResidualRNN(
                                emb_dim=emb_dim,
                                rnn_dim=rnn_dim,
                                bidirectional=bidirectional,
                                rnn_type=rnn_type,
                        )
                )

        self.parallel_mode = parallel_mode

    def forward(self, z):
        # z = (batch, n_bands, n_time, emb_dim)

        if self.parallel_mode:
            for sbm_pair in self.seqband:
                # z: (batch, n_bands, n_time, emb_dim)
                sbm_t, sbm_f = sbm_pair[0], sbm_pair[1]
                zt = sbm_t(z) # (batch, n_bands, n_time, emb_dim)
                zf = sbm_f(z.transpose(1, 2)) # (batch, n_time, n_bands, emb_dim)
                z = zt + zf.transpose(1, 2)
        else:
            for sbm in self.seqband:
                z = sbm(z)
                z = z.transpose(1, 2)

                # (batch, n_bands, n_time, emb_dim)
                #   --> (batch, n_time, n_bands, emb_dim)
                # OR
                # (batch, n_time, n_bands, emb_dim)
                #   --> (batch, n_bands, n_time, emb_dim)

        q = z
        return q  # (batch, n_bands, n_time, emb_dim)









