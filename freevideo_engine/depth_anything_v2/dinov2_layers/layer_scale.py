# Depth Anything V2, Copyright (c) 2024 Depth Anything authors.
# Adapted from a561b849ebae10a6f5ef49e26c83cbbcd36c71bf for Small inference.
# SPDX-License-Identifier: Apache-2.0; see freevideo_engine/licenses/Depth-Anything-V2-Apache-2.0.txt.
# Changes: Small only, ordinary torch attention, explicit device, no torchvision.

# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.

# Modified from: https://github.com/huggingface/pytorch-image-models/blob/main/timm/models/vision_transformer.py#L103-L110

from typing import Union

import torch
from torch import Tensor
from torch import nn


class LayerScale(nn.Module):
    def __init__(
        self,
        dim: int,
        init_values: Union[float, Tensor] = 1e-5,
        inplace: bool = False,
    ) -> None:
        super().__init__()
        self.inplace = inplace
        self.gamma = nn.Parameter(init_values * torch.ones(dim))

    def forward(self, x: Tensor) -> Tensor:
        return x.mul_(self.gamma) if self.inplace else x * self.gamma
