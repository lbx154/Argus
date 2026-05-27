---
name: cv-research-pipeline
description: "Computer Vision research pipeline: dataset handling (ImageNet, COCO, ADE20K, etc.), model training (CNNs, ViTs, detection, segmentation), evaluation metrics (mAP, mIoU, top-1/5), and visualization. Use for any CV paper."
category: domain-cv
version: "1.0"
scientist_model: gpt-5.4
created_at: "2025-07-27"
---

# Computer Vision Research Pipeline

End-to-end pipeline for CV research papers: classification, detection, segmentation, generation.

## Supported Tasks & Benchmarks

| Task | Benchmarks | Key Metrics |
|------|-----------|-------------|
| Image Classification | ImageNet-1K/22K, CIFAR-10/100, Oxford Flowers | Top-1/5 Acc, FLOPs, Params |
| Object Detection | COCO, LVIS, Pascal VOC, Objects365 | mAP, AP50, AP75, APₛ/APₘ/APₗ |
| Instance Segmentation | COCO, Cityscapes, ADE20K | mAP (mask), PQ |
| Semantic Segmentation | ADE20K, Cityscapes, PASCAL Context | mIoU, mAcc |
| Panoptic Segmentation | COCO Panoptic, ADE20K | PQ, SQ, RQ |
| Video Understanding | Kinetics-400/700, Something-Something V2 | Top-1 Acc |
| Depth Estimation | NYU-v2, KITTI, ScanNet | δ<1.25, AbsRel, RMSE |
| Image Generation | FID, IS, CLIP-Score | FID↓, IS↑, CLIP-Score↑ |
| 3D Vision | ScanNet, ShapeNet, Objaverse | Chamfer Distance, IoU-3D |

## Workflow

### Step 1: Dataset Preparation

```python
# Standard dataset loading patterns
from torchvision import datasets, transforms
from torch.utils.data import DataLoader

# ImageNet
transform = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                         std=[0.229, 0.224, 0.225]),
])

# COCO Detection
from pycocotools.coco import COCO
# Use torchvision.datasets.CocoDetection or mmdet data pipeline

# Custom dataset with albumentations augmentation
import albumentations as A
from albumentations.pytorch import ToTensorV2
```

**Dataset checklist:**
- [ ] Train/val/test splits defined (use official splits)
- [ ] Augmentation pipeline documented
- [ ] Dataset statistics computed (mean, std, class distribution)
- [ ] License compliance checked

### Step 2: Model Architecture

```python
# Common frameworks
import timm           # Image classification (ViT, ConvNeXt, EfficientNet, etc.)
import mmdet          # Object detection (Faster R-CNN, DETR, DINO, etc.)
import mmseg          # Semantic segmentation (SegFormer, Mask2Former, etc.)
from ultralytics import YOLO  # Real-time detection
import detectron2     # Meta's detection/segmentation

# Model creation patterns
model = timm.create_model('vit_base_patch16_224', pretrained=True, num_classes=1000)
```

### Step 3: Training Loop

Standard training with proper logging:

```python
# Key training components
optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=0.05)
scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

# Mixed precision training
scaler = torch.amp.GradScaler('cuda')
with torch.amp.autocast('cuda', dtype=torch.bfloat16):
    outputs = model(images)
    loss = criterion(outputs, targets)

# EMA (common for CV papers)
from timm.utils import ModelEmaV2
model_ema = ModelEmaV2(model, decay=0.9999)
```

### Step 4: Evaluation

**Classification:**
```python
# Top-1, Top-5 accuracy
from torchmetrics import Accuracy
acc_top1 = Accuracy(task='multiclass', num_classes=1000, top_k=1)
acc_top5 = Accuracy(task='multiclass', num_classes=1000, top_k=5)

# Report: FLOPs, Params, Throughput
from fvcore.nn import FlopCountAnalysis
flops = FlopCountAnalysis(model, input_tensor)
print(f"GFLOPs: {flops.total() / 1e9:.1f}")
```

**Detection (COCO):**
```python
from pycocotools.cocoeval import COCOeval
coco_eval = COCOeval(coco_gt, coco_dt, 'bbox')
coco_eval.evaluate()
coco_eval.accumulate()
coco_eval.summarize()
# Reports: AP, AP50, AP75, APₛ, APₘ, APₗ
```

**Segmentation:**
```python
# mIoU computation
from torchmetrics import JaccardIndex
miou = JaccardIndex(task='multiclass', num_classes=num_classes)
```

### Step 5: Visualization for Papers

```python
import matplotlib.pyplot as plt

# Attention map visualization (for ViT papers)
def visualize_attention(model, image):
    """Extract and visualize attention maps from ViT."""
    ...

# Detection visualization
from pycocotools.coco import COCO
# Draw bounding boxes with confidence scores

# Feature map visualization (for architecture papers)
# GradCAM, feature map activations

# t-SNE/UMAP of learned representations
from sklearn.manifold import TSNE
```

### Step 6: Standard Table Format

```latex
\begin{table}[t]
\centering
\caption{Comparison on ImageNet-1K. $^\dagger$ denotes our reproduction.}
\begin{tabular}{lccccc}
\toprule
Method & Backbone & Params (M) & FLOPs (G) & Top-1 & Top-5 \\
\midrule
DeiT-B~\cite{touvron2021deit} & ViT-B/16 & 86 & 17.6 & 81.8 & -- \\
\textbf{Ours} & ViT-B/16 & 87 & 18.1 & \textbf{83.2} & \textbf{96.5} \\
\bottomrule
\end{tabular}
\end{table}
```

## Model Zoo & Pretrained Weights

| Source | Models | Use |
|--------|--------|-----|
| timm | 1000+ models | Classification backbone |
| HuggingFace | Detection, Seg | General purpose |
| MMDetection | 300+ configs | Detection/segmentation |
| Detectron2 | Mask R-CNN, etc. | Instance segmentation |
| torchvision | Standard models | Baselines |

## Common Pitfalls

- **Unfair comparisons**: Match FLOPs/params when comparing, not just accuracy
- **Augmentation leakage**: Don't apply test-time augmentation unless you report it
- **Resolution tricks**: Report resolution clearly; higher res = higher accuracy trivially
- **Missing error bars**: Run 3+ seeds for stochastic methods
- **Ignoring efficiency**: Always report FLOPs and params alongside accuracy

## Integration

- Works with `training-experiment-runner` for the actual training
- Results feed into `result-to-claim` and `ablation-planner`
- Figures generated via `paper-illustration-image2` or matplotlib
- Final paper uses `emnlp-paper-drafting` (works for CVPR/ICCV/ECCV too)
