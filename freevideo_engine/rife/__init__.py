# RIFE (Practical-RIFE 4.25 IFNet_HDv3), Copyright (c) 2021 hzwer.
# Adapted from https://github.com/hzwer/Practical-RIFE/tree/bbfd2ea90910789a860ea3e2b32a240cd577b75e for inference.
# SPDX-License-Identifier: MIT; see freevideo_engine/licenses/Practical-RIFE-MIT.txt.
# Changes: inference only (no teacher, timestep network or ensemble), the five blocks in one list so the
# published safetensors keys (blocks.N.*) load directly, and padding/scale handling in interpolate().
import math

import torch
import torch.nn as nn
import torch.nn.functional as F

_grids = {}


def warp(tensor, flow):
    key = (str(flow.device), flow.dtype, flow.shape)
    if key not in _grids:
        n, _, h, w = flow.shape
        hor = torch.linspace(-1.0, 1.0, w, device=flow.device, dtype=flow.dtype).view(1, 1, 1, w).expand(n, -1, h, -1)
        ver = torch.linspace(-1.0, 1.0, h, device=flow.device, dtype=flow.dtype).view(1, 1, h, 1).expand(n, -1, -1, w)
        _grids[key] = torch.cat([hor, ver], 1)
    flow = torch.cat([flow[:, 0:1] / ((tensor.shape[3] - 1.0) / 2.0), flow[:, 1:2] / ((tensor.shape[2] - 1.0) / 2.0)], 1)
    grid = (_grids[key] + flow).permute(0, 2, 3, 1)
    return F.grid_sample(tensor, grid, mode='bilinear', padding_mode='border', align_corners=True)


def conv(in_planes, out_planes, kernel_size=3, stride=1, padding=1, dilation=1):
    return nn.Sequential(nn.Conv2d(in_planes, out_planes, kernel_size=kernel_size, stride=stride,
                                   padding=padding, dilation=dilation, bias=True), nn.LeakyReLU(0.2, True))


class Head(nn.Module):
    def __init__(self):
        super().__init__()
        self.cnn0 = nn.Conv2d(3, 16, 3, 2, 1)
        self.cnn1 = nn.Conv2d(16, 16, 3, 1, 1)
        self.cnn2 = nn.Conv2d(16, 16, 3, 1, 1)
        self.cnn3 = nn.ConvTranspose2d(16, 4, 4, 2, 1)
        self.relu = nn.LeakyReLU(0.2, True)

    def forward(self, x):
        x = self.relu(self.cnn0(x))
        x = self.relu(self.cnn1(x))
        x = self.relu(self.cnn2(x))
        return self.cnn3(x)


class ResConv(nn.Module):
    def __init__(self, c, dilation=1):
        super().__init__()
        self.conv = nn.Conv2d(c, c, 3, 1, dilation, dilation=dilation, groups=1)
        self.beta = nn.Parameter(torch.ones((1, c, 1, 1)), requires_grad=True)
        self.relu = nn.LeakyReLU(0.2, True)

    def forward(self, x):
        return self.relu(self.conv(x) * self.beta + x)


class IFBlock(nn.Module):
    def __init__(self, in_planes, c=64):
        super().__init__()
        self.conv0 = nn.Sequential(conv(in_planes, c // 2, 3, 2, 1), conv(c // 2, c, 3, 2, 1))
        self.convblock = nn.Sequential(*(ResConv(c) for _ in range(8)))
        self.lastconv = nn.Sequential(nn.ConvTranspose2d(c, 4 * 13, 4, 2, 1), nn.PixelShuffle(2))

    def forward(self, x, flow=None, scale=1):
        x = F.interpolate(x, scale_factor=1. / scale, mode='bilinear', align_corners=False)
        if flow is not None:
            flow = F.interpolate(flow, scale_factor=1. / scale, mode='bilinear', align_corners=False) * 1. / scale
            x = torch.cat((x, flow), 1)
        feat = self.convblock(self.conv0(x))
        tmp = F.interpolate(self.lastconv(feat), scale_factor=scale, mode='bilinear', align_corners=False)
        return tmp[:, :4] * scale, tmp[:, 4:5], tmp[:, 5:]


class IFNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.blocks = nn.ModuleList([IFBlock(7 + 8, c=192), IFBlock(8 + 4 + 8 + 8, c=128), IFBlock(8 + 4 + 8 + 8, c=96),
                                     IFBlock(8 + 4 + 8 + 8, c=64), IFBlock(8 + 4 + 8 + 8, c=32)])
        self.encode = Head()

    def forward(self, img0, img1, timestep, scale_list):
        timestep = (img0[:, :1].clone() * 0 + 1) * timestep
        f0, f1 = self.encode(img0[:, :3]), self.encode(img1[:, :3])
        warped_img0, warped_img1 = img0, img1
        flow = mask = feat = None
        for i, block in enumerate(self.blocks):
            if flow is None:
                flow, mask, feat = block(torch.cat((img0[:, :3], img1[:, :3], f0, f1, timestep), 1), None, scale=scale_list[i])
            else:
                wf0, wf1 = warp(f0, flow[:, :2]), warp(f1, flow[:, 2:4])
                fd, mask, feat = block(torch.cat((warped_img0[:, :3], warped_img1[:, :3], wf0, wf1, timestep, mask, feat), 1),
                                       flow, scale=scale_list[i])
                flow = flow + fd
            warped_img0, warped_img1 = warp(img0, flow[:, :2]), warp(img1, flow[:, 2:4])
        mask = torch.sigmoid(mask)
        return warped_img0 * mask + warped_img1 * (1 - mask)


def interpolate(net, a, b, t, scale=1.0):
    """a, b: (1, 3, H, W) in 0..1. Pads to the network's multiple, as the reference inference does."""
    _, _, h, w = a.shape
    step = max(128, int(128 / scale))
    pad = (0, math.ceil(w / step) * step - w, 0, math.ceil(h / step) * step - h)
    a, b = F.pad(a, pad), F.pad(b, pad)
    merged = net(a, b, t, [16 / scale, 8 / scale, 4 / scale, 2 / scale, 1 / scale])
    return merged[:, :, :h, :w].clamp(0, 1)
