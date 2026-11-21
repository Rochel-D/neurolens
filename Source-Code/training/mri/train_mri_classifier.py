"""Train the NeuroLens brain-MRI classifier — Google Colab (T4) or a Mac with Apple GPU (MPS).

Mac (VS Code terminal):
    pip install torch torchvision scikit-learn tensorboard kagglehub
    python -c "import kagglehub; print(kagglehub.dataset_download('masoudnickparvar/brain-tumor-mri-dataset'))"
    python training/mri/train_mri_classifier.py --data <printed path> --epochs 5 --out mri_out
    cp mri_out/mri_classifier.pt models/


Dataset: "Brain Tumor MRI Dataset" (Masoud Nickparvar, Kaggle) — 4 classes:
    glioma / meningioma / notumor / pituitary, already split into Training/ and Testing/.

Colab:
    !pip -q install kaggle torch torchvision scikit-learn
    # upload kaggle.json, then:
    !kaggle datasets download -d masoudnickparvar/brain-tumor-mri-dataset -p /content --unzip
    !python train_mri_classifier.py --data /content --epochs 8 --out /content/drive/MyDrive/neurolens

Outputs:
    mri_classifier.pt   → copy into NeuroLens/models/
    mri_metrics.json    → accuracy, per-class precision/recall/F1, confusion matrix (for the evaluation report)
    TensorBoard logs    → %tensorboard --logdir <out>/runs
"""
import argparse
import json
import time
from pathlib import Path

import torch
from sklearn.metrics import classification_report, confusion_matrix
from torch import nn
from torch.utils.data import DataLoader, random_split
from torch.utils.tensorboard import SummaryWriter
from torchvision import datasets, transforms
from torchvision.models import EfficientNet_B0_Weights, efficientnet_b0

CLASSES = ["glioma", "meningioma", "notumor", "pituitary"]  # must match backend/app/services/mri.py


device_pin = torch.cuda.is_available()  # pin_memory only helps (and only works quietly) on CUDA


def loaders(root: Path, batch: int, seed: int = 42):
    norm = transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    train_tf = transforms.Compose([
        transforms.Grayscale(3), transforms.Resize((224, 224)),
        transforms.RandomHorizontalFlip(), transforms.RandomRotation(10),
        transforms.ColorJitter(brightness=0.2, contrast=0.2), transforms.ToTensor(), norm,
    ])
    test_tf = transforms.Compose([transforms.Grayscale(3), transforms.Resize((224, 224)), transforms.ToTensor(), norm])
    full = datasets.ImageFolder(root / "Training", transform=train_tf)
    assert full.classes == CLASSES, f"unexpected class folders {full.classes}"
    n_val = int(0.1 * len(full))
    train, val = random_split(full, [len(full) - n_val, n_val], generator=torch.Generator().manual_seed(seed))
    test = datasets.ImageFolder(root / "Testing", transform=test_tf)
    kw = dict(batch_size=batch, num_workers=2, pin_memory=device_pin)
    return DataLoader(train, shuffle=True, **kw), DataLoader(val, **kw), DataLoader(test, **kw)


@torch.no_grad()
def evaluate(model, loader, device):
    model.eval()
    ys, ps = [], []
    for x, y in loader:
        ps += model(x.to(device)).argmax(1).cpu().tolist()
        ys += y.tolist()
    acc = sum(int(a == b) for a, b in zip(ys, ps)) / len(ys)
    return acc, ys, ps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/content")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    device = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")  # mps = Apple GPU
    tr, va, te = loaders(Path(a.data), a.batch)

    model = efficientnet_b0(weights=EfficientNet_B0_Weights.IMAGENET1K_V1)
    model.classifier[1] = nn.Linear(model.classifier[1].in_features, len(CLASSES))
    model.to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=a.epochs)
    scaler = torch.amp.GradScaler(enabled=device == "cuda")
    loss_fn = nn.CrossEntropyLoss(label_smoothing=0.05)
    writer = SummaryWriter(out / "runs")
    best = 0.0
    for ep in range(a.epochs):
        model.train()
        t0, total = time.time(), 0.0
        for x, y in tr:
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            with torch.autocast(device_type=device, dtype=torch.float16, enabled=device == "cuda"):
                loss = loss_fn(model(x), y)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            total += loss.item() * len(y)
        sched.step()
        val_acc, _, _ = evaluate(model, va, device)
        writer.add_scalar("loss/train", total / len(tr.dataset), ep)
        writer.add_scalar("acc/val", val_acc, ep)
        print(f"epoch {ep + 1}/{a.epochs} loss {total / len(tr.dataset):.4f} val_acc {val_acc:.4f} ({time.time() - t0:.0f}s)")
        if val_acc > best:
            best = val_acc
            torch.save({"model": model.state_dict(), "classes": CLASSES}, out / "mri_classifier.pt")

    model.load_state_dict(torch.load(out / "mri_classifier.pt", weights_only=True)["model"])
    test_acc, ys, ps = evaluate(model, te, device)
    report = classification_report(ys, ps, target_names=CLASSES, output_dict=True)
    metrics = {"test_accuracy": test_acc, "best_val_accuracy": best, "per_class": report,
               "confusion_matrix": confusion_matrix(ys, ps).tolist(), "classes": CLASSES, "epochs": a.epochs, "lr": a.lr}
    (out / "mri_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(json.dumps({"test_accuracy": round(test_acc, 4), "macro_f1": round(report["macro avg"]["f1-score"], 4)}))


if __name__ == "__main__":
    main()
