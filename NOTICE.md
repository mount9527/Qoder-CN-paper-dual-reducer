# 来源说明 / Provenance

本插件为 Qoder CN 重新开发，功能设计参考 DSH 插件 paper-dual-reducer
（https://github.com/mount9527/paper-dual-reducer，MIT）及其引擎
skill-paper-dual-reducer（https://github.com/mount9527/skill-paper-dual-reducer，MIT）。

引擎代码（skills/paper-dual/scripts/engine.py）为本仓库独立重写，未复制上述仓库源码；
算子编号、门禁阈值与流水线语义沿用其公开规格。

AIGC 真机检测模型（yuchuantian/AIGC_detector_zhv3，Apache-2.0，基座
chinese-roberta-wwm-ext 亦为 Apache-2.0）不随本仓库分发；v0.2.x 为离线核心，未包含真机检测，
后续版本如引入将由使用者首次运行时自行从 HuggingFace 下载权重。
